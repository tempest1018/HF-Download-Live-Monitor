import math
from pathlib import Path

import pytest

from hf_download_live_monitor.models import DownloadSpec, ProgressSnapshot
from hf_download_live_monitor.supervisor_models import (
    DiscoveryHealth,
    EventType,
    SessionLifecycle,
    SessionSnapshot,
    SupervisorEvent,
    SupervisorSnapshot,
)


def progress(*, rate: float | None = 4.0) -> ProgressSnapshot:
    return ProgressSnapshot(
        DownloadSpec("owner/repo", Path("out")),
        (),
        10.0,
        5,
        10,
        rate,
        1.25,
    )


def session(
    session_id: str = "session-1",
    *,
    rate: float | None = 4.0,
    lifecycle: SessionLifecycle = SessionLifecycle.ACTIVE,
) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        pid=7,
        repo="owner/repo",
        local_dir="out",
        lifecycle=lifecycle,
        progress=progress(rate=rate),
    )


def test_supervisor_event_contract_is_versioned_and_json_safe() -> None:
    event = SupervisorEvent(7, "run-id", 10.0, EventType.SESSION_ADDED, session("s1"))

    payload = event.to_dict()

    assert payload["schema_version"] == 1
    assert payload["kind"] == "supervisor_event"
    assert payload["sequence"] == 7
    assert payload["run_id"] == "run-id"
    assert payload["observed_at"] == 10.0
    assert payload["event"] == "session_added"
    assert payload["session_id"] == "s1"
    assert payload["session"]["repository"]["id"] == "owner/repo"  # type: ignore[index]


def test_aggregate_snapshot_sums_only_finite_non_negative_rates() -> None:
    snapshot = SupervisorSnapshot.build(
        10.0,
        (
            session("one", rate=4.0),
            session("two", rate=None),
            session("three", rate=math.nan),
        ),
    )

    assert snapshot.rate_bytes_per_second == 4.0
    assert snapshot.active_sessions == 3
    assert snapshot.downloaded_bytes == 15
    assert snapshot.expected_bytes == 30


def test_snapshot_counts_terminal_lifecycles() -> None:
    snapshot = SupervisorSnapshot.build(
        10.0,
        (
            session("active"),
            session("complete", lifecycle=SessionLifecycle.COMPLETED),
            session("failed", lifecycle=SessionLifecycle.FAILED),
            session("lost", lifecycle=SessionLifecycle.LOST),
        ),
        discovery_health=DiscoveryHealth.DEGRADED,
    )

    assert snapshot.active_sessions == 1
    assert snapshot.completed_sessions == 1
    assert snapshot.failed_sessions == 1
    assert snapshot.lost_sessions == 1
    assert snapshot.discovery_health is DiscoveryHealth.DEGRADED


def test_event_rejects_nonpositive_sequence() -> None:
    with pytest.raises(ValueError, match="sequence"):
        SupervisorEvent(0, "run-id", 10.0, EventType.PROGRESS, session())
