import io
import json
from dataclasses import replace
from pathlib import Path

from rich.columns import Columns
from rich.console import Console

from hf_download_live_monitor.controls import DisplayState
from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.layout import ViewMode, layout_policy
from hf_download_live_monitor.models import (
    DownloadSpec,
    FileProgress,
    FileState,
    MonitorError,
    ProgressSnapshot,
    RepoType,
)
from hf_download_live_monitor.renderers import (
    JsonLinesRenderer,
    JsonRenderer,
    PlainRenderer,
    RichRenderer,
    _dashboard,
    snapshot_to_dict,
)


def sample() -> ProgressSnapshot:
    spec = DownloadSpec("owner/repo", Path("out"), RepoType.MODEL, "main")
    return ProgressSnapshot(
        spec=spec,
        files=(FileProgress("model.bin", 100, 25, FileState.DOWNLOADING, 10.0, 7.5),),
        observed_at=2.0,
        downloaded_bytes=25,
        expected_bytes=100,
        rate_bytes_per_second=10.0,
        eta_seconds=7.5,
    )


def test_snapshot_dictionary_has_versioned_stable_types() -> None:
    data = snapshot_to_dict(sample())
    assert data["schema_version"] == 2
    assert data["repository"]["type"] == "model"
    assert data["repository"]["requested_revision"] == "main"
    assert data["repository"]["resolved_revision"] == "main"
    assert data["integrity"] == {
        "verified_files": 0,
        "complete_unverified_files": 0,
        "failed_files": 0,
    }
    assert data["files"][0]["state"] == "downloading"
    assert data["downloaded_bytes"] == 25


def test_plain_renderer_has_no_ansi_and_supports_ascii() -> None:
    stream = io.StringIO()
    PlainRenderer(stream=stream, ascii_only=True).render(sample())
    output = stream.getvalue()
    assert "\x1b[" not in output
    assert "owner/repo" in output
    assert "25.00%" in output
    output.encode("ascii")


def test_json_renderer_emits_one_document() -> None:
    stream = io.StringIO()
    JsonRenderer(stream).render(sample())
    assert json.loads(stream.getvalue())["schema_version"] == 2
    assert stream.getvalue().count("\n") == 1


def test_json_renderer_never_concatenates_documents() -> None:
    stream = io.StringIO()
    renderer = JsonRenderer(stream)
    renderer.render(sample())
    renderer.render(sample())
    assert json.loads(stream.getvalue())["schema_version"] == 2
    assert stream.getvalue().count("\n") == 1


def test_json_lines_renderer_emits_one_object_per_render() -> None:
    stream = io.StringIO()
    renderer = JsonLinesRenderer(stream)
    renderer.render(sample())
    renderer.render(sample())
    assert len([json.loads(line) for line in stream.getvalue().splitlines()]) == 2


def test_structured_renderers_redact_error_credentials_without_mutating_error() -> None:
    error = MonitorError(
        "unsafe_message",
        "token=hf_secret Authorization: Bearer hf_authorization_secret",
        category=ErrorCategory.MONITOR,
    )
    snapshot = replace(sample(), errors=(error,))

    stream = io.StringIO()
    JsonRenderer(stream).render(snapshot)
    encoded = stream.getvalue()
    data = json.loads(encoded)

    assert "hf_secret" not in encoded
    assert "hf_authorization_secret" not in encoded
    assert "<redacted>" in data["errors"][0]["message"]
    assert "hf_secret" in error.message


def test_rich_renderer_can_render_without_terminal() -> None:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=80)
    RichRenderer(console=console).render(sample())
    assert "model.bin" in stream.getvalue()


def test_rich_renderer_adapts_at_all_boundary_widths_and_ascii() -> None:
    for width in (40, 59, 60, 80, 109, 110, 160):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=width, color_system=None)
        renderer = RichRenderer(
            console, view_mode=ViewMode.BALANCED, ascii_only=True, reduced_motion=True
        )
        renderer.update_display_state(DisplayState(show_help=True))
        renderer.render(sample())
        output = stream.getvalue()
        assert "owner/repo" in output
        assert "25.00%" in output
        output.encode("ascii")


def test_ascii_rich_renderer_is_ascii_on_forced_terminal(monkeypatch) -> None:
    def reject_unicode_progress(*args, **kwargs):
        raise AssertionError("ASCII mode must not construct Rich's Unicode progress bar")

    monkeypatch.setattr("hf_download_live_monitor.renderers.ProgressBar", reject_unicode_progress)
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, width=59, color_system=None)
    renderer = RichRenderer(console, ascii_only=True, reduced_motion=True)

    renderer.render(varied_sample())
    renderer.close()

    stream.getvalue().encode("ascii")


def test_attention_orders_active_and_failed_before_completed() -> None:
    snapshot = sample()
    snapshot = replace(
        snapshot,
        files=(
            FileProgress("done.bin", 1, 1, FileState.VERIFIED),
            FileProgress("bad.bin", 1, 1, FileState.FAILED),
            FileProgress("active.bin", 2, 1, FileState.DOWNLOADING),
        ),
    )
    stream = io.StringIO()
    renderer = RichRenderer(
        Console(file=stream, force_terminal=False, width=120, color_system=None),
        view_mode=ViewMode.DETAILED,
        reduced_motion=True,
    )
    renderer.render(snapshot)
    output = stream.getvalue()
    assert output.index("active.bin") < output.index("done.bin")
    assert output.index("bad.bin") < output.index("done.bin")


def varied_sample() -> ProgressSnapshot:
    return replace(
        sample(),
        files=(
            FileProgress("done.bin", 1, 1, FileState.VERIFIED),
            FileProgress("unverified.bin", 1, 1, FileState.COMPLETE_UNVERIFIED),
            FileProgress("active.bin", 2, 1, FileState.DOWNLOADING),
        ),
        downloaded_bytes=3,
        expected_bytes=4,
        verified_files=1,
        complete_unverified_files=1,
        rate_history=tuple(float(value) for value in range(30)),
    )


def render_at(width: int, mode: ViewMode, *, ascii_only: bool = False) -> str:
    stream = io.StringIO()
    renderer = RichRenderer(
        Console(file=stream, force_terminal=False, width=width, color_system=None),
        view_mode=mode,
        ascii_only=ascii_only,
        reduced_motion=True,
    )
    renderer.render(varied_sample())
    return stream.getvalue()


def test_policy_composition_differs_for_narrow_normal_and_wide() -> None:
    narrow = render_at(50, ViewMode.BALANCED)
    normal = render_at(80, ViewMode.BALANCED)
    wide = render_at(120, ViewMode.BALANCED)
    assert "Spd" in narrow and "Speed" not in narrow
    assert "Speed" in normal
    assert narrow != normal != wide
    policy = layout_policy(120, ViewMode.BALANCED)
    dashboard = _dashboard(varied_sample(), policy, DisplayState(), False)
    assert any(isinstance(item, Columns) for item in dashboard.renderables)


def test_compact_omits_optional_sections_and_detailed_expands_them() -> None:
    compact = render_at(120, ViewMode.COMPACT)
    detailed = render_at(120, ViewMode.DETAILED)
    for absent in ("Preflight / integrity", "Recent events", "rate ", "done.bin"):
        assert absent not in compact
    for present in ("Preflight / integrity", "Recent events", "rate ", "done.bin"):
        assert present in detailed


def test_header_status_requires_all_files_verified_and_handles_terminal_states() -> None:
    partial = replace(varied_sample(), complete_unverified_files=0)
    assert "MONITORING" in _render_snapshot(partial)
    verified = replace(
        partial,
        files=(FileProgress("done.bin", 1, 1, FileState.VERIFIED),),
        verified_files=1,
    )
    assert "VERIFIED" in _render_snapshot(verified)
    terminal_unverified = replace(
        varied_sample(),
        files=(FileProgress("unverified.bin", 1, 1, FileState.COMPLETE_UNVERIFIED),),
    )
    assert "COMPLETE / UNVERIFIED" in _render_snapshot(terminal_unverified)
    failed = replace(varied_sample(), failed_files=1)
    assert "FAILED" in _render_snapshot(failed)


def _render_snapshot(snapshot: ProgressSnapshot) -> str:
    stream = io.StringIO()
    renderer = RichRenderer(
        Console(file=stream, force_terminal=False, width=80, color_system=None),
        reduced_motion=True,
    )
    renderer.render(snapshot)
    return stream.getvalue()


def test_dynamic_resize_recomputes_layout() -> None:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=50, color_system=None)
    renderer = RichRenderer(console, reduced_motion=True)
    renderer.render(varied_sample())
    console.width = 120
    renderer.render(varied_sample())
    output = stream.getvalue()
    assert "Spd" in output and "Speed" in output


def test_reduced_motion_terminal_uses_one_non_scrolling_live(monkeypatch) -> None:
    events: list[object] = []

    class FakeLive:
        def __init__(self, renderable, **kwargs):
            events.append(("init", kwargs))

        def start(self, refresh: bool = False) -> None:
            events.append(("start", refresh))

        def update(self, renderable, *, refresh: bool) -> None:
            events.append(("update", refresh))

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr("hf_download_live_monitor.renderers.Live", FakeLive)
    console = Console(file=io.StringIO(), force_terminal=True, width=80, color_system=None)
    renderer = RichRenderer(console, reduced_motion=True)
    renderer.render(sample())
    renderer.render(sample())
    renderer.close()
    assert events[0] == ("init", {"console": console, "auto_refresh": False})
    assert events[1:] == [("start", True), ("update", True), "stop"]


def test_narrow_focus_keeps_essential_state_without_events() -> None:
    output = render_at(50, ViewMode.BALANCED)
    assert "75.00%" in output
    assert "MONITORING" in output
    assert "active.bin" in output
    assert "Recent events" not in output


def test_unicode_ascii_and_no_color_contracts() -> None:
    unicode_output = render_at(80, ViewMode.BALANCED)
    assert any(ord(character) > 127 for character in unicode_output)
    for width in (40, 59, 60, 80, 109, 110, 160):
        for mode in ViewMode:
            render_at(width, mode, ascii_only=True).encode("ascii")
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=80, no_color=True)
    RichRenderer(console, reduced_motion=True).render(sample())
    assert "\x1b[" not in stream.getvalue()


def test_animated_terminal_uses_auto_refresh_live(monkeypatch) -> None:
    events: list[object] = []

    class FakeLive:
        def __init__(self, renderable, **kwargs):
            events.append(("init", kwargs))

        def start(self, refresh: bool = False) -> None:
            events.append(("start", refresh))

        def update(self, renderable, *, refresh: bool) -> None:
            events.append(("update", refresh))

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr("hf_download_live_monitor.renderers.Live", FakeLive)
    console = Console(file=io.StringIO(), force_terminal=True, width=80, color_system=None)
    renderer = RichRenderer(console)
    renderer.render(sample())
    renderer.close()
    assert events[0] == ("init", {"console": console, "refresh_per_second": 4})
