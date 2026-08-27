from pathlib import Path

from hf_download_live_monitor.attach import DownloadCandidate
from hf_download_live_monitor.models import DownloadSpec
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
