import io
import json
from dataclasses import replace
from pathlib import Path

from rich.console import Console

from hf_download_live_monitor.controls import DisplayState
from hf_download_live_monitor.layout import ViewMode
from hf_download_live_monitor.models import (
    DownloadSpec,
    FileProgress,
    FileState,
    ProgressSnapshot,
    RepoType,
)
from hf_download_live_monitor.renderers import (
    JsonLinesRenderer,
    JsonRenderer,
    PlainRenderer,
    RichRenderer,
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
