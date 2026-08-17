"""Terminal and structured progress renderers."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any, Protocol, TextIO

from rich import box
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from hf_download_live_monitor.controls import DisplayState
from hf_download_live_monitor.layout import LayoutPolicy, ViewMode, layout_policy
from hf_download_live_monitor.models import FileProgress, FileState, ProgressSnapshot
from hf_download_live_monitor.security import redact_text


class Renderer(Protocol):
    def render(self, snapshot: ProgressSnapshot) -> None: ...
    def close(self) -> None: ...


def human_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = max(0.0, float(value))
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def snapshot_to_dict(snapshot: ProgressSnapshot) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "observed_at": snapshot.observed_at,
        "repository": {
            "id": snapshot.spec.repo,
            "type": snapshot.spec.repo_type.value,
            "requested_revision": snapshot.requested_revision,
            "resolved_revision": snapshot.resolved_revision,
            "local_dir": str(snapshot.spec.local_dir),
        },
        "downloaded_bytes": snapshot.downloaded_bytes,
        "expected_bytes": snapshot.expected_bytes,
        "rate_bytes_per_second": snapshot.rate_bytes_per_second,
        "eta_seconds": snapshot.eta_seconds,
        "integrity": {
            "verified_files": snapshot.verified_files,
            "complete_unverified_files": snapshot.complete_unverified_files,
            "failed_files": snapshot.failed_files,
        },
        "files": [{**asdict(item), "state": item.state.value} for item in snapshot.files],
        "errors": [
            {**error.to_dict(), "message": redact_text(error.message)} for error in snapshot.errors
        ],
    }


class PlainRenderer:
    def __init__(self, stream: TextIO | None = None, *, ascii_only: bool = False) -> None:
        self._stream = stream or sys.stdout
        self._ascii_only = ascii_only

    def render(self, snapshot: ProgressSnapshot) -> None:
        separator = "-" if self._ascii_only else "\N{EM DASH}"
        percent = _percent(snapshot.downloaded_bytes, snapshot.expected_bytes)
        progress = (
            f"{human_bytes(snapshot.downloaded_bytes)} / "
            f"{human_bytes(snapshot.expected_bytes)} ({percent:.2f}%)"
        )
        self._stream.write(f"{snapshot.spec.repo}@{snapshot.spec.revision} {separator} {progress}")
        if snapshot.rate_bytes_per_second is not None:
            self._stream.write(f" {separator} {human_bytes(snapshot.rate_bytes_per_second)}/s")
        self._stream.write("\n")
        for item in snapshot.files:
            item_bytes = (
                f"{human_bytes(item.downloaded_bytes)} / {human_bytes(item.expected_bytes)}"
            )
            item_percent = _percent(item.downloaded_bytes, item.expected_bytes)
            self._stream.write(
                f"  {item.filename}: {item_bytes} ({item_percent:.2f}%) [{item.state.value}]\n"
            )
        for error in snapshot.errors:
            self._stream.write(f"  error {error.code}: {error.message}\n")
        self._stream.flush()

    def close(self) -> None:
        pass


class JsonRenderer:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout
        self._rendered = False

    def render(self, snapshot: ProgressSnapshot) -> None:
        if self._rendered:
            return
        self._rendered = True
        self._emit(snapshot)

    def _emit(self, snapshot: ProgressSnapshot) -> None:
        json.dump(snapshot_to_dict(snapshot), self._stream, sort_keys=True)
        self._stream.write("\n")
        self._stream.flush()

    def close(self) -> None:
        pass


class JsonLinesRenderer(JsonRenderer):
    def render(self, snapshot: ProgressSnapshot) -> None:
        self._emit(snapshot)


class RichRenderer:
    def __init__(
        self,
        console: Console | None = None,
        *,
        view_mode: ViewMode = ViewMode.BALANCED,
        ascii_only: bool = False,
        reduced_motion: bool = False,
    ) -> None:
        self._console = console or Console()
        self._state = DisplayState(view_mode=view_mode)
        self._ascii_only = ascii_only
        self._reduced_motion = reduced_motion
        self._live: Live | None = None

    def update_display_state(self, state: DisplayState) -> None:
        self._state = state

    def render(self, snapshot: ProgressSnapshot) -> None:
        policy = layout_policy(
            self._console.width,
            self._state.view_mode,
            self._reduced_motion,
            self._state.show_details,
            self._state.show_events,
        )
        dashboard = _dashboard(snapshot, policy, self._state, self._ascii_only)
        if not self._console.is_terminal:
            self._console.print(dashboard)
        elif self._live is None:
            if policy.animate:
                self._live = Live(dashboard, console=self._console, refresh_per_second=4)
            else:
                self._live = Live(dashboard, console=self._console, auto_refresh=False)
            self._live.start(refresh=True)
        else:
            self._live.update(dashboard, refresh=True)

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None


def _dashboard(
    snapshot: ProgressSnapshot, policy: LayoutPolicy, state: DisplayState, ascii_only: bool
) -> Group:
    aggregate = _aggregate_panel(snapshot, policy, ascii_only)
    attention = _attention_table(snapshot, policy, ascii_only)
    preflight = _preflight_panel(snapshot, ascii_only)
    parts: list[RenderableType] = [_header_panel(snapshot, ascii_only)]
    if policy.columns == 2:
        summary: list[RenderableType] = [aggregate]
        if policy.show_preflight:
            summary.append(preflight)
        parts.extend((Columns(summary, expand=True, equal=True), attention))
    else:
        parts.extend((aggregate, attention))
        if policy.show_preflight:
            parts.append(preflight)
    if policy.show_events:
        parts.append(_events_panel(snapshot, ascii_only))
    if state.show_help:
        parts.append(_help_panel(ascii_only))
    parts.append(_footer())
    return Group(*parts)


def _panel(content: RenderableType, title: str, ascii_only: bool) -> Panel:
    return Panel(content, title=title, box=box.ASCII if ascii_only else box.ROUNDED)


def _header_panel(snapshot: ProgressSnapshot, ascii_only: bool) -> Panel:
    states = tuple(item.state for item in snapshot.files)
    terminal = {
        FileState.VERIFIED,
        FileState.COMPLETE_UNVERIFIED,
        FileState.COMPLETE,
        FileState.SIZE_MATCHED,
    }
    if snapshot.failed_files or FileState.FAILED in states:
        status = "FAILED"
    elif states and all(state is FileState.VERIFIED for state in states):
        status = "VERIFIED"
    elif (
        states
        and all(state in terminal for state in states)
        and FileState.COMPLETE_UNVERIFIED in states
    ):
        status = "COMPLETE / UNVERIFIED"
    else:
        status = "MONITORING"
    return _panel(
        Text(
            f"{snapshot.spec.repo}  {snapshot.requested_revision} -> "
            f"{snapshot.resolved_revision[:12]}  {status}"
        ),
        "Repository",
        ascii_only,
    )


def _aggregate_panel(snapshot: ProgressSnapshot, policy: LayoutPolicy, ascii_only: bool) -> Panel:
    speed = (
        "-"
        if snapshot.rate_bytes_per_second is None
        else f"{human_bytes(snapshot.rate_bytes_per_second)}/s"
    )
    eta = "-" if snapshot.eta_seconds is None else f"{snapshot.eta_seconds:.1f}s"
    percent = _percent(snapshot.downloaded_bytes, snapshot.expected_bytes)
    speed_label = "Spd" if policy.abbreviate_labels else "Speed"
    text = (
        f"{percent:.2f}%  {human_bytes(snapshot.downloaded_bytes)} / "
        f"{human_bytes(snapshot.expected_bytes)}  {speed_label} {speed}  ETA {eta}"
    )
    if policy.show_sparkline and snapshot.rate_history:
        text += "\nrate " + _sparkline(snapshot.rate_history[-24:], ascii_only)
    progress: RenderableType
    if ascii_only:
        cells = 20
        completed_cells = min(cells, int(percent / 100.0 * cells))
        progress = Text(f"[{'#' * completed_cells}{'-' * (cells - completed_cells)}]")
    else:
        progress = ProgressBar(total=100.0, completed=percent, width=None)
    return _panel(Group(Text(text), progress), "Progress", ascii_only)


def _attention_table(snapshot: ProgressSnapshot, policy: LayoutPolicy, ascii_only: bool) -> Table:
    table = Table(title="Attention files", expand=True, box=box.ASCII if ascii_only else box.SIMPLE)
    table.add_column("File", overflow="fold")
    table.add_column("Done", justify="right")
    table.add_column("State", overflow="fold")
    completed = {
        FileState.COMPLETE,
        FileState.VERIFIED,
        FileState.COMPLETE_UNVERIFIED,
        FileState.SIZE_MATCHED,
    }
    files = [
        item
        for item in snapshot.files
        if policy.show_completed_files or item.state not in completed
    ]
    files.sort(key=_attention_rank)
    for item in files:
        table.add_row(
            item.filename,
            f"{_percent(item.downloaded_bytes, item.expected_bytes):.2f}%",
            item.state.value.upper(),
        )
    if not files:
        table.add_row("No files need attention", "", "")
    return table


def _attention_rank(item: FileProgress) -> tuple[int, str]:
    active = {
        FileState.FAILED,
        FileState.DOWNLOADING,
        FileState.VERIFYING,
        FileState.FINALIZING,
        FileState.INCONSISTENT,
    }
    return (0 if item.state in active else 1, item.filename)


def _preflight_panel(snapshot: ProgressSnapshot, ascii_only: bool) -> Panel:
    counts = (
        f"verified {snapshot.verified_files} | "
        f"complete unverified {snapshot.complete_unverified_files} | "
        f"failed {snapshot.failed_files}"
    )
    return _panel(
        Text(counts),
        "Preflight / integrity",
        ascii_only,
    )


def _events_panel(snapshot: ProgressSnapshot, ascii_only: bool) -> Panel:
    content = (
        "No recent events"
        if not snapshot.errors
        else "\n".join(f"{error.code}: {error.message}" for error in snapshot.errors)
    )
    return _panel(Text(content), "Recent events", ascii_only)


def _help_panel(ascii_only: bool) -> Panel:
    return _panel(Text("v view | d details | e events | ? help | q cancel"), "Keys", ascii_only)


def _footer() -> Text:
    return Text("Press ? for help")


def _sparkline(values: tuple[float, ...], ascii_only: bool) -> str:
    symbols = ".:-=+*#@" if ascii_only else "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    high = max(values) or 1.0
    return "".join(
        symbols[min(len(symbols) - 1, int(value / high * (len(symbols) - 1)))] for value in values
    )


def _percent(downloaded: int, expected: int) -> float:
    return 100.0 if expected == 0 else min(100.0, downloaded / expected * 100.0)
