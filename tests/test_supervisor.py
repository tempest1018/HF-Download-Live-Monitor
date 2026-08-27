from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest

from hf_download_live_monitor.attach import DownloadCandidate
from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.models import (
    DownloadPlan,
    DownloadSpec,
    FileObservation,
    ManifestFile,
    MonitorError,
)
from hf_download_live_monitor.processes import ProcessIdentity
from hf_download_live_monitor.supervisor import DownloadSupervisor
from hf_download_live_monitor.supervisor_models import (
    DiscoveryHealth,
    EventType,
    SessionLifecycle,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SequenceDiscovery:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> tuple[DownloadCandidate, ...]:
        self.calls += 1
        value = self._values.pop(0) if self._values else ()
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, tuple)
        return value


class ImmediateExecutor:
    def __init__(self) -> None:
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


class FakeRepository:
    def prepare(self, spec: DownloadSpec) -> DownloadPlan:
        resolved = DownloadSpec(
            spec.repo,
            spec.local_dir,
            spec.repo_type,
            "a" * 40,
            spec.filenames,
            spec.includes,
            spec.excludes,
        )
        return DownloadPlan(resolved, spec.revision, (ManifestFile("config.json", 10),))


class FakeObserver:
    def __init__(self, visible_bytes: int = 10) -> None:
        self.visible_bytes = visible_bytes

    def observe(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        now: float,
    ) -> tuple[FileObservation, ...]:
        return (
            FileObservation(
                "config.json",
                10,
                self.visible_bytes,
                self.visible_bytes,
                None,
                now,
            ),
        )


def candidate(
    pid: int = 7,
    start_token: str = "100",
    repo: str = "owner/repo",
) -> DownloadCandidate:
    return DownloadCandidate(
        ProcessIdentity(pid, start_token),
        DownloadSpec(repo, Path(f"out-{pid}-{start_token}").resolve()),
    )


def test_supervisor_adds_new_sessions_and_prevents_pid_reuse_merge() -> None:
    clock = FakeClock()
    discovery = SequenceDiscovery(((candidate(),), (candidate(start_token="200"),)))
    supervisor = DownloadSupervisor(discovery, clock=clock)

    supervisor.tick()
    clock.advance(1.0)
    supervisor.tick()

    sessions = supervisor.snapshot.sessions
    assert len(sessions) == 2
    assert len({item.session_id for item in sessions}) == 2
    assert {item.lifecycle for item in sessions} == {
        SessionLifecycle.DISCOVERED,
        SessionLifecycle.LOST,
    }


def test_finalized_session_is_removed_only_after_retention() -> None:
    clock = FakeClock()
    discovery = SequenceDiscovery(((candidate(),), ()))
    supervisor = DownloadSupervisor(discovery, clock=clock, retention=15.0)

    supervisor.tick()
    clock.advance(1.0)
    supervisor.tick()
    assert supervisor.snapshot.sessions[0].lifecycle is SessionLifecycle.LOST

    clock.advance(14.9)
    supervisor.tick()
    assert len(supervisor.snapshot.sessions) == 1
    clock.advance(0.1)
    supervisor.tick()
    assert supervisor.snapshot.sessions == ()


def test_discovery_failure_preserves_sessions_and_deduplicates_warning() -> None:
    clock = FakeClock()
    discovery = SequenceDiscovery(((candidate(),), OSError("secret"), OSError("secret")))
    supervisor = DownloadSupervisor(discovery, clock=clock)

    supervisor.tick()
    clock.advance(1.0)
    supervisor.tick()
    clock.advance(1.0)
    supervisor.tick()

    assert len(supervisor.snapshot.sessions) == 1
    assert supervisor.snapshot.discovery_health is DiscoveryHealth.DEGRADED
    warnings = [event for event in supervisor.events if event.event is EventType.DISCOVERY_WARNING]
    assert len(warnings) == 1
    assert "secret" not in str(warnings[0].to_dict())


def test_session_cap_is_deterministic() -> None:
    downloads = (candidate(9, repo="z/repo"), candidate(3, repo="a/repo"))
    supervisor = DownloadSupervisor(SequenceDiscovery((downloads,)), max_sessions=1)

    supervisor.tick()

    assert [item.repo for item in supervisor.snapshot.sessions] == ["a/repo"]


def test_idle_discovery_backs_off_without_busy_loop() -> None:
    clock = FakeClock()
    discovery = SequenceDiscovery(((), (), ()))
    supervisor = DownloadSupervisor(discovery, clock=clock, discovery_refresh=1.0)

    supervisor.tick()
    for _ in range(9):
        clock.advance(0.1)
        supervisor.tick()
    assert discovery.calls == 1

    clock.advance(1.1)
    supervisor.tick()
    assert discovery.calls == 2


def test_preparation_activates_session_with_isolated_progress() -> None:
    clock = FakeClock()
    supervisor = DownloadSupervisor(
        SequenceDiscovery(((candidate(),), (candidate(),))),
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine_factory=ProgressEngine,
        executor=ImmediateExecutor(),
        clock=clock,
    )

    supervisor.tick()
    clock.advance(1.0)
    supervisor.tick()

    current = supervisor.snapshot.sessions[0]
    assert current.lifecycle is SessionLifecycle.ACTIVE
    assert current.progress is not None
    assert current.progress.resolved_revision == "a" * 40


def test_disappeared_process_is_forced_to_completed_state() -> None:
    clock = FakeClock()
    observer = FakeObserver()
    supervisor = DownloadSupervisor(
        SequenceDiscovery(((candidate(),), (candidate(),), (), ())),
        repository=FakeRepository(),
        observer=observer,
        engine_factory=ProgressEngine,
        executor=ImmediateExecutor(),
        clock=clock,
    )

    for _ in range(4):
        supervisor.tick()
        clock.advance(1.0)

    assert supervisor.snapshot.sessions[0].lifecycle is SessionLifecycle.COMPLETED


def test_incomplete_disappeared_process_is_failed_by_integrity_check() -> None:
    clock = FakeClock()
    supervisor = DownloadSupervisor(
        SequenceDiscovery(((candidate(),), (candidate(),), (), ())),
        repository=FakeRepository(),
        observer=FakeObserver(visible_bytes=5),
        engine_factory=ProgressEngine,
        executor=ImmediateExecutor(),
        clock=clock,
    )

    for _ in range(4):
        supervisor.tick()
        clock.advance(1.0)

    assert supervisor.snapshot.sessions[0].lifecycle is SessionLifecycle.FAILED


def test_shutdown_closes_executor_without_signalling_downloaders() -> None:
    executor = ImmediateExecutor()
    supervisor = DownloadSupervisor(
        SequenceDiscovery(((),)),
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine_factory=ProgressEngine,
        executor=executor,
    )

    supervisor.shutdown()

    assert executor.shutdown_calls == [(True, False)]


def test_shutdown_forces_final_observation_of_active_session() -> None:
    supervisor = DownloadSupervisor(
        SequenceDiscovery(((candidate(),),)),
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine_factory=ProgressEngine,
        executor=ImmediateExecutor(),
    )
    supervisor.tick()
    supervisor.tick()
    assert supervisor.snapshot.sessions[0].lifecycle is SessionLifecycle.ACTIVE

    supervisor.shutdown()

    assert supervisor.snapshot.sessions[0].lifecycle is SessionLifecycle.COMPLETED
    assert [event.event for event in supervisor.events][-2:] == [
        EventType.SESSION_FINALIZED,
        EventType.SUPERVISOR_STOPPED,
    ]


def test_shutdown_maps_renderer_close_failure_without_leaking_detail() -> None:
    class Renderer:
        def render_snapshot(self, snapshot) -> None:
            pass

        def render_event(self, event) -> None:
            pass

        def close(self) -> None:
            raise OSError("token=hf_sensitive")

    supervisor = DownloadSupervisor(SequenceDiscovery(((),)), renderer=Renderer())
    with pytest.raises(MonitorError) as caught:
        supervisor.shutdown()
    assert caught.value.code == "supervisor_shutdown_failed"
    assert "hf_sensitive" not in caught.value.message
