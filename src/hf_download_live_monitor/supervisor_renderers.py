"""Output contracts for continuous multi-download supervision."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Protocol, TextIO

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hf_download_live_monitor.controls import SupervisorDisplayState
from hf_download_live_monitor.layout import LayoutClass, ViewMode, choose_layout
from hf_download_live_monitor.renderers import human_bytes
from hf_download_live_monitor.supervisor_models import (
    EventType,
    SupervisorEvent,
    SupervisorSnapshot,
)


class SupervisorRenderer(Protocol):
    def render_snapshot(self, snapshot: SupervisorSnapshot) -> None: ...

    def render_event(self, event: SupervisorEvent) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SupervisorLayout:
    repository_columns: int
    layout_class: LayoutClass


def supervisor_layout(width: int, view_mode: ViewMode) -> SupervisorLayout:
    layout_class = choose_layout(width, view_mode)
    columns = {
        LayoutClass.NARROW: 4,
        LayoutClass.NORMAL: 6,
        LayoutClass.WIDE: 8,
    }[layout_class]
    return SupervisorLayout(columns, layout_class)


class SupervisorRichRenderer:
    """Accessible aggregate dashboard composed solely from immutable snapshots."""

    def __init__(
        self,
        console: Console | None = None,
        *,
        display_state: SupervisorDisplayState | None = None,
        ascii_only: bool = False,
        reduced_motion: bool = False,
    ) -> None:
        self._console = console or Console()
        self.display_state = display_state or SupervisorDisplayState()
        self._ascii_only = ascii_only
        self._reduced_motion = reduced_motion
        self._live: Live | None = None
        self._closed = False

    def update_display_state(self, state: SupervisorDisplayState) -> None:
        self.display_state = state

    def render_snapshot(self, snapshot: SupervisorSnapshot) -> None:
        if self._closed:
            return
        ordered_ids = tuple(item.session_id for item in snapshot.sessions)
        self.display_state = self.display_state.reconcile(ordered_ids)
        dashboard = self.compose(snapshot)
        if not self._console.is_terminal:
            self._console.print(dashboard)
        elif self._live is None:
            if self._reduced_motion:
                self._live = Live(dashboard, console=self._console, auto_refresh=False)
            else:
                self._live = Live(dashboard, console=self._console, refresh_per_second=4)
            self._live.start(refresh=True)
        else:
            self._live.update(dashboard, refresh=True)

    def render_event(self, event: SupervisorEvent) -> None:
        return

    def compose(self, snapshot: SupervisorSnapshot) -> RenderableType:
        policy = supervisor_layout(self._console.width, self.display_state.view_mode)
        title = Text("HF Download Live Monitor", style="bold cyan")
        summary = Text(
            f"{len(snapshot.sessions)} repositories | "
            f"{human_bytes(snapshot.downloaded_bytes)} / "
            f"{human_bytes(snapshot.expected_bytes)} | "
            f"{human_bytes(snapshot.rate_bytes_per_second)}/s"
        )
        if snapshot.discovery_health.value != "healthy":
            summary.append(" | discovery degraded", style="bold yellow")
        table = self._repository_table(snapshot, policy.repository_columns)
        parts: list[RenderableType] = [title, summary, table]
        selected = next(
            (
                item
                for item in snapshot.sessions
                if item.session_id == self.display_state.selected_session_id
            ),
            None,
        )
        if selected is not None and policy.layout_class is not LayoutClass.NARROW:
            parts.append(
                Panel(
                    f"PID {selected.pid}\nDestination: {selected.local_dir}",
                    title=f"Selected: {selected.repo}",
                    border_style="dim",
                )
            )
        if self.display_state.show_help:
            parts.append(Text("j/k select | v view | ? help | q quit", style="dim"))
        return Group(*parts)

    def _repository_table(self, snapshot: SupervisorSnapshot, columns: int) -> Table:
        table = Table(box=box.ASCII if self._ascii_only else box.ROUNDED, expand=True)
        headings = ("Repository", "State", "Progress", "Rate", "PID", "Destination")
        for heading in headings[:columns]:
            table.add_column(heading, no_wrap=heading in {"State", "PID"})
        for item in snapshot.sessions:
            progress = item.progress
            values = [
                item.repo,
                item.lifecycle.value,
                "-"
                if progress is None
                else (
                    f"{human_bytes(progress.downloaded_bytes)}/"
                    f"{human_bytes(progress.expected_bytes)}"
                ),
                "-"
                if progress is None or progress.rate_bytes_per_second is None
                else f"{human_bytes(progress.rate_bytes_per_second)}/s",
                str(item.pid),
                item.local_dir,
            ]
            table.add_row(*values[:columns])
        return table

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._closed = True


class SupervisorJsonRenderer:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout
        self._latest: SupervisorSnapshot | None = None
        self._closed = False

    def render_snapshot(self, snapshot: SupervisorSnapshot) -> None:
        if not self._closed:
            self._latest = snapshot

    def render_event(self, event: SupervisorEvent) -> None:
        return

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._latest is not None:
            json.dump(self._latest.to_dict(), self._stream, sort_keys=True)
            self._stream.write("\n")
            self._stream.flush()


class SupervisorJsonLinesRenderer:
    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        progress_interval: float = 1.0,
    ) -> None:
        if progress_interval <= 0:
            raise ValueError("progress interval must be positive")
        self._stream = stream or sys.stdout
        self._progress_interval = progress_interval
        self._last_progress: dict[str, float] = {}

    def render_snapshot(self, snapshot: SupervisorSnapshot) -> None:
        return

    def render_event(self, event: SupervisorEvent) -> None:
        session_id = event.session_id
        if event.event is EventType.PROGRESS and session_id is not None:
            previous = self._last_progress.get(session_id)
            if previous is not None and event.observed_at - previous < self._progress_interval:
                return
            self._last_progress[session_id] = event.observed_at
        json.dump(event.to_dict(), self._stream, sort_keys=True)
        self._stream.write("\n")
        self._stream.flush()

    def close(self) -> None:
        return


class SupervisorPlainRenderer:
    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        progress_interval: float = 1.0,
    ) -> None:
        if progress_interval <= 0:
            raise ValueError("progress interval must be positive")
        self._stream = stream or sys.stdout
        self._progress_interval = progress_interval
        self._last_progress: dict[str, float] = {}
        self._last_lifecycle: dict[str, tuple[EventType, str]] = {}

    def render_snapshot(self, snapshot: SupervisorSnapshot) -> None:
        return

    def render_event(self, event: SupervisorEvent) -> None:
        session = event.session
        if session is None:
            if event.event is EventType.DISCOVERY_WARNING:
                self._stream.write(f"[{event.observed_at:.3f}] discovery degraded\n")
                self._stream.flush()
            return
        if event.event is EventType.PROGRESS:
            previous = self._last_progress.get(session.session_id)
            if previous is not None and event.observed_at - previous < self._progress_interval:
                return
            self._last_progress[session.session_id] = event.observed_at
            progress = session.progress
            if progress is None:
                return
            rate = progress.rate_bytes_per_second
            rate_text = "" if rate is None else f" {human_bytes(rate)}/s"
            self._stream.write(
                f"[{event.observed_at:.3f}] {session.repo} "
                f"{human_bytes(progress.downloaded_bytes)} / "
                f"{human_bytes(progress.expected_bytes)}{rate_text}\n"
            )
        else:
            identity = (event.event, session.lifecycle.value)
            if self._last_lifecycle.get(session.session_id) == identity:
                return
            self._last_lifecycle[session.session_id] = identity
            self._stream.write(
                f"[{event.observed_at:.3f}] {session.repo} {session.lifecycle.value}\n"
            )
        self._stream.flush()

    def close(self) -> None:
        return
