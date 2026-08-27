"""Deterministic coordination for continuously discovered downloads."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from hf_download_live_monitor.attach import DownloadCandidate, SessionKey
from hf_download_live_monitor.supervisor_models import (
    DiscoveryHealth,
    EventType,
    SessionLifecycle,
    SessionSnapshot,
    SupervisorEvent,
    SupervisorSnapshot,
)


@dataclass(slots=True)
class _SessionRuntime:
    candidate: DownloadCandidate
    session_id: str
    lifecycle: SessionLifecycle = SessionLifecycle.DISCOVERED
    finalized_at: float | None = None

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            session_id=self.session_id,
            pid=self.candidate.pid,
            repo=self.candidate.spec.repo,
            local_dir=str(self.candidate.spec.local_dir),
            lifecycle=self.lifecycle,
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
    ) -> None:
        if discovery_refresh <= 0:
            raise ValueError("discovery refresh must be positive")
        if retention < 0:
            raise ValueError("retention must be non-negative")
        if max_sessions < 1:
            raise ValueError("max sessions must be positive")
        self._discover = discover
        self._discovery_refresh = discovery_refresh
        self._retention = retention
        self._max_sessions = max_sessions
        self._clock = clock
        self._run_id = run_id or str(uuid.uuid4())
        self._sessions: dict[SessionKey, _SessionRuntime] = {}
        self._events: list[SupervisorEvent] = []
        self._sequence = 0
        self._discovery_health = DiscoveryHealth.HEALTHY
        self._warning_active = False
        self._next_discovery = float("-inf")
        self._idle_interval = discovery_refresh
        self._snapshot = SupervisorSnapshot.build(clock(), ())

    @property
    def snapshot(self) -> SupervisorSnapshot:
        return self._snapshot

    @property
    def events(self) -> tuple[SupervisorEvent, ...]:
        return tuple(self._events)

    def tick(self) -> SupervisorSnapshot:
        now = self._clock()
        if now >= self._next_discovery:
            self._run_discovery(now)
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
                continue
            runtime = _SessionRuntime(candidate, _session_id(key))
            self._sessions[key] = runtime
            self._emit(EventType.SESSION_ADDED, runtime, now)

        for key, runtime in self._sessions.items():
            if key in visible or runtime.finalized_at is not None:
                continue
            runtime.lifecycle = SessionLifecycle.LOST
            runtime.finalized_at = now
            self._emit(EventType.SESSION_FINALIZED, runtime, now)

        if visible:
            self._idle_interval = self._discovery_refresh
        else:
            self._idle_interval = min(5.0, max(self._discovery_refresh, self._idle_interval * 2))
        self._next_discovery = now + self._idle_interval

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
