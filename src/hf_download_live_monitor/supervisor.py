"""Deterministic coordination for continuously discovered downloads."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from hf_download_live_monitor.app import Observer, Repository
from hf_download_live_monitor.attach import DownloadCandidate, SessionKey
from hf_download_live_monitor.controls import SupervisorDisplayState
from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import DownloadPlan, MonitorError, ProgressSnapshot
from hf_download_live_monitor.supervisor_models import (
    DiscoveryHealth,
    EventType,
    SessionLifecycle,
    SessionSnapshot,
    SupervisorEvent,
    SupervisorSnapshot,
)


class SupervisorRenderer(Protocol):
    def render_snapshot(self, snapshot: SupervisorSnapshot) -> None: ...

    def render_event(self, event: SupervisorEvent) -> None: ...

    def close(self) -> None: ...


class SupervisorControls(Protocol):
    def poll(self, state: SupervisorDisplayState) -> SupervisorDisplayState: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class _SessionRuntime:
    candidate: DownloadCandidate
    session_id: str
    lifecycle: SessionLifecycle = SessionLifecycle.DISCOVERED
    finalized_at: float | None = None
    present: bool = True
    plan: DownloadPlan | None = None
    progress: ProgressSnapshot | None = None
    engine: ProgressEngine | None = None
    future: Future[DownloadPlan | ProgressSnapshot] | None = None
    work_kind: Literal["prepare", "finalize"] | None = None
    next_refresh: float = 0.0
    diagnostics: tuple[MonitorError, ...] = ()

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            session_id=self.session_id,
            pid=self.candidate.pid,
            repo=self.candidate.spec.repo,
            local_dir=str(self.candidate.spec.local_dir),
            lifecycle=self.lifecycle,
            progress=self.progress,
            diagnostics=self.diagnostics,
        )


class DownloadSupervisor:
    """Reconcile process discovery into stable, immutable session snapshots."""

    def __init__(
        self,
        discover: Callable[[], tuple[DownloadCandidate, ...]],
        *,
        discovery_refresh: float = 1.0,
        retention: float = 15.0,
        max_sessions: int = 32,
        clock: Callable[[], float] = time.monotonic,
        run_id: str | None = None,
        repository: Repository | None = None,
        observer: Observer | None = None,
        engine_factory: Callable[[], ProgressEngine] | None = None,
        executor: Executor | None = None,
        refresh: float = 0.25,
        renderer: SupervisorRenderer | None = None,
        controls: SupervisorControls | None = None,
        display_state: SupervisorDisplayState | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if discovery_refresh <= 0:
            raise ValueError("discovery refresh must be positive")
        if retention < 0:
            raise ValueError("retention must be non-negative")
        if max_sessions < 1:
            raise ValueError("max sessions must be positive")
        if refresh <= 0:
            raise ValueError("refresh must be positive")
        dependencies = (repository, observer, engine_factory)
        if any(item is None for item in dependencies) and any(
            item is not None for item in dependencies
        ):
            raise ValueError("repository, observer, and engine factory must be supplied together")
        self._discover = discover
        self._discovery_refresh = discovery_refresh
        self._retention = retention
        self._max_sessions = max_sessions
        self._clock = clock
        self._repository = repository
        self._observer = observer
        self._engine_factory = engine_factory
        self._executor = executor if repository is not None else None
        if repository is not None and self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hf-supervisor")
        self._refresh = refresh
        self._renderer = renderer
        self._controls = controls
        self._display_state = display_state or SupervisorDisplayState()
        self._sleeper = sleeper
        self._run_id = run_id or str(uuid.uuid4())
        self._sessions: dict[SessionKey, _SessionRuntime] = {}
        self._events: deque[SupervisorEvent] = deque(maxlen=4096)
        self._sequence = 0
        self._discovery_health = DiscoveryHealth.HEALTHY
        self._warning_active = False
        self._next_discovery = float("-inf")
        self._idle_interval = discovery_refresh
        self._snapshot = SupervisorSnapshot.build(clock(), ())
        self._closed = False

    @property
    def snapshot(self) -> SupervisorSnapshot:
        return self._snapshot

    @property
    def events(self) -> tuple[SupervisorEvent, ...]:
        return tuple(self._events)

    def drain_events(self) -> tuple[SupervisorEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def run(self) -> int:
        """Run until the operator cancels; attached child processes are never signalled."""
        try:
            while True:
                snapshot = self.tick()
                if self._renderer is not None:
                    for event in self.drain_events():
                        self._renderer.render_event(event)
                    self._renderer.render_snapshot(snapshot)
                if self._controls is not None:
                    ids = tuple(item.session_id for item in snapshot.sessions)
                    self._display_state = self._display_state.reconcile(ids)
                    self._display_state = self._controls.poll(self._display_state)
                    update = getattr(self._renderer, "update_display_state", None)
                    if callable(update):
                        update(self._display_state)
                    if self._display_state.cancel_requested:
                        return exit_code_for(ErrorCategory.CANCELLED)
                self._sleeper(self._refresh)
        except KeyboardInterrupt:
            return exit_code_for(ErrorCategory.CANCELLED)
        finally:
            self.shutdown()

    def tick(self) -> SupervisorSnapshot:
        now = self._clock()
        self._collect_work(now)
        if now >= self._next_discovery:
            self._run_discovery(now)
        self._observe_active(now)
        self._expire_sessions(now)
        self._refresh_snapshot(now)
        return self._snapshot

    def _run_discovery(self, now: float) -> None:
        try:
            candidates = tuple(sorted(self._discover(), key=lambda item: item.key))
        except Exception:
            self._discovery_health = DiscoveryHealth.DEGRADED
            self._next_discovery = now + self._discovery_refresh
            if not self._warning_active:
                self._warning_active = True
                self._emit(EventType.DISCOVERY_WARNING, None, now)
            return

        self._discovery_health = DiscoveryHealth.HEALTHY
        self._warning_active = False
        visible = {item.key: item for item in candidates[: self._max_sessions]}
        for key, candidate in visible.items():
            if key in self._sessions:
                self._sessions[key].present = True
                continue
            runtime = _SessionRuntime(candidate, _session_id(key))
            self._sessions[key] = runtime
            self._emit(EventType.SESSION_ADDED, runtime, now)
            self._schedule_prepare(runtime)

        for key, runtime in self._sessions.items():
            if key in visible or runtime.finalized_at is not None:
                continue
            runtime.present = False
            if runtime.lifecycle is SessionLifecycle.ACTIVE:
                self._schedule_finalization(runtime)
            elif runtime.lifecycle is SessionLifecycle.DISCOVERED:
                self._finish(runtime, SessionLifecycle.LOST, now)

        if visible:
            self._idle_interval = self._discovery_refresh
        else:
            self._idle_interval = min(5.0, max(self._discovery_refresh, self._idle_interval * 2))
        self._next_discovery = now + self._idle_interval

    def _schedule_prepare(self, runtime: _SessionRuntime) -> None:
        if self._repository is None or self._executor is None:
            return
        runtime.lifecycle = SessionLifecycle.PREPARING
        runtime.work_kind = "prepare"
        runtime.future = self._executor.submit(self._repository.prepare, runtime.candidate.spec)

    def _schedule_finalization(self, runtime: _SessionRuntime) -> None:
        if runtime.future is not None:
            return
        if runtime.plan is None or runtime.engine is None or self._executor is None:
            self._finish(runtime, SessionLifecycle.LOST, self._clock())
            return
        runtime.lifecycle = SessionLifecycle.FINALIZING
        runtime.work_kind = "finalize"
        runtime.future = self._executor.submit(
            self._observe,
            runtime.plan,
            runtime.engine,
            True,
        )

    def _collect_work(self, now: float) -> None:
        for runtime in tuple(self._sessions.values()):
            future = runtime.future
            if future is None or not future.done():
                continue
            kind = runtime.work_kind
            runtime.future = None
            runtime.work_kind = None
            try:
                result = future.result()
            except Exception as exc:
                runtime.diagnostics = (
                    MonitorError(
                        "session_work_failed", f"session work failed ({type(exc).__name__})"
                    ),
                )
                self._finish(runtime, SessionLifecycle.FAILED, now)
                continue
            if kind == "prepare":
                runtime.plan = cast(DownloadPlan, result)
                runtime.engine = cast(Callable[[], ProgressEngine], self._engine_factory)()
                runtime.lifecycle = SessionLifecycle.ACTIVE
                runtime.next_refresh = now
                self._emit(EventType.SESSION_READY, runtime, now)
                if not runtime.present:
                    self._schedule_finalization(runtime)
            elif kind == "finalize":
                runtime.progress = cast(ProgressSnapshot, result)
                outcome = _final_lifecycle(runtime.progress)
                self._finish(runtime, outcome, now)

    def _observe_active(self, now: float) -> None:
        if self._observer is None:
            return
        for _, runtime in sorted(self._sessions.items(), key=lambda item: item[0]):
            if runtime.lifecycle is not SessionLifecycle.ACTIVE or now < runtime.next_refresh:
                continue
            if runtime.plan is None or runtime.engine is None:
                continue
            try:
                runtime.progress = self._observe(runtime.plan, runtime.engine, False)
            except Exception as exc:
                runtime.diagnostics = (
                    MonitorError(
                        "session_observation_failed", f"observation failed ({type(exc).__name__})"
                    ),
                )
                self._finish(runtime, SessionLifecycle.FAILED, now)
                continue
            runtime.next_refresh = now + self._refresh
            self._emit(EventType.PROGRESS, runtime, now)

    def _observe(
        self,
        plan: DownloadPlan,
        engine: ProgressEngine,
        final: bool,
    ) -> ProgressSnapshot:
        if self._observer is None:
            raise RuntimeError("observer is unavailable")
        now = self._clock()
        observations = self._observer.observe(plan.spec, plan.manifest, now)
        return engine.update(plan, observations, now=now, final=final)

    def _finish(
        self,
        runtime: _SessionRuntime,
        lifecycle: SessionLifecycle,
        now: float,
    ) -> None:
        runtime.lifecycle = lifecycle
        runtime.finalized_at = now
        self._emit(EventType.SESSION_FINALIZED, runtime, now)

    def _expire_sessions(self, now: float) -> None:
        expired = [
            key
            for key, runtime in self._sessions.items()
            if runtime.finalized_at is not None and now - runtime.finalized_at >= self._retention
        ]
        for key in expired:
            runtime = self._sessions.pop(key)
            self._emit(EventType.SESSION_REMOVED, runtime, now)

    def _emit(
        self,
        event: EventType,
        runtime: _SessionRuntime | None,
        observed_at: float,
    ) -> None:
        self._sequence += 1
        self._events.append(
            SupervisorEvent(
                self._sequence,
                self._run_id,
                observed_at,
                event,
                None if runtime is None else runtime.snapshot(),
            )
        )

    def _refresh_snapshot(self, now: float) -> None:
        sessions = tuple(
            runtime.snapshot()
            for _, runtime in sorted(self._sessions.items(), key=lambda item: item[0])
        )
        self._snapshot = SupervisorSnapshot.build(
            now,
            sessions,
            discovery_health=self._discovery_health,
        )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        now = self._clock()
        self._emit(EventType.SUPERVISOR_STOPPED, None, now)
        self._refresh_snapshot(now)
        if self._renderer is not None:
            for event in self.drain_events():
                self._renderer.render_event(event)
            self._renderer.render_snapshot(self._snapshot)
        for runtime in self._sessions.values():
            if runtime.engine is not None:
                runtime.engine.close()
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        if self._controls is not None:
            self._controls.close()
        if self._renderer is not None:
            self._renderer.close()


def _session_id(key: SessionKey) -> str:
    identity = "\0".join(
        (
            key.repo_type,
            key.repo,
            key.local_dir,
            key.revision,
            str(key.process.pid),
            key.process.start_token,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _final_lifecycle(snapshot: ProgressSnapshot) -> SessionLifecycle:
    if snapshot.failed_files:
        return SessionLifecycle.FAILED
    if snapshot.expected_bytes > 0 and snapshot.downloaded_bytes >= snapshot.expected_bytes:
        return SessionLifecycle.COMPLETED
    return SessionLifecycle.LOST
