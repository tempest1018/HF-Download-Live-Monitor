"""Immutable state and structured contracts for multi-download supervision."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hf_download_live_monitor.models import MonitorError, ProgressSnapshot
from hf_download_live_monitor.renderers import snapshot_to_dict
from hf_download_live_monitor.security import redact_text


class SessionLifecycle(str, Enum):
    DISCOVERED = "discovered"
    PREPARING = "preparing"
    ACTIVE = "active"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"


class EventType(str, Enum):
    SESSION_ADDED = "session_added"
    SESSION_READY = "session_ready"
    PROGRESS = "progress"
    SESSION_FINALIZED = "session_finalized"
    SESSION_REMOVED = "session_removed"
    DISCOVERY_WARNING = "discovery_warning"
    SUPERVISOR_STOPPED = "supervisor_stopped"


class DiscoveryHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    pid: int
    repo: str
    local_dir: str
    lifecycle: SessionLifecycle
    progress: ProgressSnapshot | None = None
    diagnostics: tuple[MonitorError, ...] = ()

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session ID must not be empty")
        if self.pid <= 0:
            raise ValueError("PID must be positive")
        if not self.repo:
            raise ValueError("repository must not be empty")

    def to_dict(self) -> dict[str, Any]:
        repository: dict[str, object] = {
            "id": self.repo,
            "local_dir": self.local_dir,
        }
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "pid": self.pid,
            "repository": repository,
            "lifecycle": self.lifecycle.value,
            "diagnostics": [
                {**error.to_dict(), "message": redact_text(error.message)}
                for error in self.diagnostics
            ],
        }
        if self.progress is not None:
            payload["progress"] = snapshot_to_dict(self.progress)
        return payload


@dataclass(frozen=True, slots=True)
class SupervisorSnapshot:
    observed_at: float
    sessions: tuple[SessionSnapshot, ...]
    discovery_health: DiscoveryHealth
    downloaded_bytes: int
    expected_bytes: int
    rate_bytes_per_second: float
    active_sessions: int
    finalizing_sessions: int
    completed_sessions: int
    failed_sessions: int
    lost_sessions: int

    @classmethod
    def build(
        cls,
        observed_at: float,
        sessions: tuple[SessionSnapshot, ...],
        *,
        discovery_health: DiscoveryHealth = DiscoveryHealth.HEALTHY,
    ) -> SupervisorSnapshot:
        progress = tuple(item.progress for item in sessions if item.progress is not None)
        rates = (
            item.rate_bytes_per_second
            for item in progress
            if item.rate_bytes_per_second is not None
            and math.isfinite(item.rate_bytes_per_second)
            and item.rate_bytes_per_second >= 0
        )
        return cls(
            observed_at=observed_at,
            sessions=sessions,
            discovery_health=discovery_health,
            downloaded_bytes=sum(item.downloaded_bytes for item in progress),
            expected_bytes=sum(item.expected_bytes for item in progress),
            rate_bytes_per_second=sum(rates),
            active_sessions=_count(sessions, SessionLifecycle.ACTIVE),
            finalizing_sessions=_count(sessions, SessionLifecycle.FINALIZING),
            completed_sessions=_count(sessions, SessionLifecycle.COMPLETED),
            failed_sessions=_count(sessions, SessionLifecycle.FAILED),
            lost_sessions=_count(sessions, SessionLifecycle.LOST),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "supervisor_snapshot",
            "observed_at": self.observed_at,
            "discovery_health": self.discovery_health.value,
            "downloaded_bytes": self.downloaded_bytes,
            "expected_bytes": self.expected_bytes,
            "rate_bytes_per_second": self.rate_bytes_per_second,
            "sessions": [item.to_dict() for item in self.sessions],
            "counts": {
                "active": self.active_sessions,
                "finalizing": self.finalizing_sessions,
                "completed": self.completed_sessions,
                "failed": self.failed_sessions,
                "lost": self.lost_sessions,
            },
        }


@dataclass(frozen=True, slots=True)
class SupervisorEvent:
    sequence: int
    run_id: str
    observed_at: float
    event: EventType
    session: SessionSnapshot | None = None

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("event sequence must be positive")
        if not self.run_id:
            raise ValueError("run ID must not be empty")

    @property
    def session_id(self) -> str | None:
        return None if self.session is None else self.session.session_id

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "kind": "supervisor_event",
            "sequence": self.sequence,
            "run_id": self.run_id,
            "observed_at": self.observed_at,
            "event": self.event.value,
        }
        if self.session is not None:
            payload["session_id"] = self.session.session_id
            payload["session"] = self.session.to_dict()
        return payload


def _count(sessions: tuple[SessionSnapshot, ...], lifecycle: SessionLifecycle) -> int:
    return sum(item.lifecycle is lifecycle for item in sessions)
