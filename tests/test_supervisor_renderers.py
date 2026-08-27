import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from hf_download_live_monitor.controls import SupervisorDisplayState
from hf_download_live_monitor.layout import ViewMode
from hf_download_live_monitor.models import DownloadSpec, ProgressSnapshot
from hf_download_live_monitor.supervisor_models import (
    EventType,
    SessionLifecycle,
    SessionSnapshot,
    SupervisorEvent,
    SupervisorSnapshot,
)
from hf_download_live_monitor.supervisor_renderers import (
    SupervisorJsonLinesRenderer,
    SupervisorJsonRenderer,
    SupervisorPlainRenderer,
    SupervisorRichRenderer,
    supervisor_layout,
)


def session(session_id: str = "session-1") -> SessionSnapshot:
    progress = ProgressSnapshot(
        DownloadSpec("owner/repo", Path("out")),
        (),
        1.0,
        5,
        10,
        4.0,
        1.25,
    )
    return SessionSnapshot(
        session_id,
        7,
        "owner/repo",
        "out",
        SessionLifecycle.ACTIVE,
        progress,
    )


def event(sequence: int, kind: EventType, observed_at: float) -> SupervisorEvent:
    return SupervisorEvent(sequence, "run-id", observed_at, kind, session())


def snapshot(observed_at: float = 1.0) -> SupervisorSnapshot:
    return SupervisorSnapshot.build(observed_at, (session(),))


def test_jsonl_rate_limits_progress_but_never_suppresses_final_event() -> None:
    stream = StringIO()
    renderer = SupervisorJsonLinesRenderer(stream, progress_interval=1.0)

    renderer.render_event(event(1, EventType.PROGRESS, 1.0))
    renderer.render_event(event(2, EventType.PROGRESS, 1.1))
    renderer.render_event(event(3, EventType.SESSION_FINALIZED, 1.2))

    payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [item["sequence"] for item in payloads] == [1, 3]
    assert payloads[-1]["event"] == "session_finalized"


def test_final_json_writes_exactly_one_latest_document_on_close() -> None:
    stream = StringIO()
    renderer = SupervisorJsonRenderer(stream)

    renderer.render_snapshot(snapshot(1.0))
    renderer.render_snapshot(snapshot(2.0))
    assert stream.getvalue() == ""
    renderer.close()
    renderer.close()

    payload = json.loads(stream.getvalue())
    assert payload["kind"] == "supervisor_snapshot"
    assert payload["observed_at"] == 2.0
    assert len(stream.getvalue().splitlines()) == 1


def test_plain_renderer_deduplicates_lifecycle_lines() -> None:
    stream = StringIO()
    renderer = SupervisorPlainRenderer(stream, progress_interval=1.0)
    added = event(1, EventType.SESSION_ADDED, 1.0)

    renderer.render_event(added)
    renderer.render_event(added)

    assert stream.getvalue().count("owner/repo") == 1


@pytest.mark.parametrize(("width", "columns"), [(45, 4), (90, 6), (140, 8)])
def test_supervisor_layout_adapts_repository_columns(width: int, columns: int) -> None:
    assert supervisor_layout(width, ViewMode.BALANCED).repository_columns == columns


@pytest.mark.parametrize("width", [45, 90, 140])
def test_rich_dashboard_renders_at_supported_widths(width: int) -> None:
    stream = StringIO()
    console = Console(file=stream, width=width, force_terminal=False, no_color=True)
    renderer = SupervisorRichRenderer(
        console,
        display_state=SupervisorDisplayState(),
        ascii_only=True,
        reduced_motion=True,
    )
    renderer.render_snapshot(snapshot())
    renderer.close()
    output = stream.getvalue()
    assert "owner/repo" in output
    assert "HF Download Live Monitor" in output
