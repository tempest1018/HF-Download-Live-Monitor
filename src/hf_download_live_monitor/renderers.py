"""Terminal and structured progress renderers."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any, Protocol, TextIO

from rich.console import Console
from rich.live import Live
from rich.table import Table

from hf_download_live_monitor.models import ProgressSnapshot


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
        "schema_version": 1,
        "observed_at": snapshot.observed_at,
        "repository": {
            "id": snapshot.spec.repo,
            "type": snapshot.spec.repo_type.value,
            "revision": snapshot.spec.revision,
            "local_dir": str(snapshot.spec.local_dir),
        },
        "downloaded_bytes": snapshot.downloaded_bytes,
        "expected_bytes": snapshot.expected_bytes,
        "rate_bytes_per_second": snapshot.rate_bytes_per_second,
        "eta_seconds": snapshot.eta_seconds,
        "files": [
            {
                **asdict(item),
                "state": item.state.value,
            }
            for item in snapshot.files
        ],
        "errors": [
            {"code": error.code, "message": error.message, "recoverable": error.recoverable}
            for error in snapshot.errors
        ],
    }


class PlainRenderer:
    def __init__(self, stream: TextIO | None = None, *, ascii_only: bool = False) -> None:
        self._stream = stream or sys.stdout
        self._ascii_only = ascii_only

    def render(self, snapshot: ProgressSnapshot) -> None:
        separator = "-" if self._ascii_only else "—"
        percent = _percent(snapshot.downloaded_bytes, snapshot.expected_bytes)
        self._stream.write(
            f"{snapshot.spec.repo}@{snapshot.spec.revision} {separator} "
            f"{human_bytes(snapshot.downloaded_bytes)} / {human_bytes(snapshot.expected_bytes)} "
            f"({percent:.2f}%)"
        )
        if snapshot.rate_bytes_per_second is not None:
            self._stream.write(f" {separator} {human_bytes(snapshot.rate_bytes_per_second)}/s")
        self._stream.write("\n")
        for item in snapshot.files:
            item_percent = _percent(item.downloaded_bytes, item.expected_bytes)
            self._stream.write(
                f"  {item.filename}: {human_bytes(item.downloaded_bytes)} / "
                f"{human_bytes(item.expected_bytes)} ({item_percent:.2f}%) [{item.state.value}]\n"
            )
        for error in snapshot.errors:
            self._stream.write(f"  error {error.code}: {error.message}\n")
        self._stream.flush()

    def close(self) -> None:
        return None


class JsonRenderer:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def render(self, snapshot: ProgressSnapshot) -> None:
        json.dump(snapshot_to_dict(snapshot), self._stream, sort_keys=True)
        self._stream.write("\n")
        self._stream.flush()

    def close(self) -> None:
        return None


class JsonLinesRenderer(JsonRenderer):
    pass


class RichRenderer:
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._live: Live | None = None

    def render(self, snapshot: ProgressSnapshot) -> None:
        table = _progress_table(snapshot)
        if not self._console.is_terminal:
            self._console.print(table)
            return
        if self._live is None:
            self._live = Live(table, console=self._console, refresh_per_second=4)
            self._live.start()
        else:
            self._live.update(table, refresh=True)

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None


def _progress_table(snapshot: ProgressSnapshot) -> Table:
    table = Table(title=f"{snapshot.spec.repo} @ {snapshot.spec.revision}", expand=True)
    table.add_column("File", overflow="fold")
    table.add_column("Progress", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Speed", justify="right")
    table.add_column("State")
    for item in snapshot.files:
        rate = (
            "-"
            if item.rate_bytes_per_second is None
            else f"{human_bytes(item.rate_bytes_per_second)}/s"
        )
        table.add_row(
            item.filename,
            f"{human_bytes(item.downloaded_bytes)} / {human_bytes(item.expected_bytes)}",
            f"{_percent(item.downloaded_bytes, item.expected_bytes):.2f}%",
            rate,
            item.state.value,
        )
    table.caption = (
        f"Total {human_bytes(snapshot.downloaded_bytes)} / {human_bytes(snapshot.expected_bytes)}"
    )
    return table


def _percent(downloaded: int, expected: int) -> float:
    if expected == 0:
        return 100.0
    return min(100.0, downloaded / expected * 100.0)
