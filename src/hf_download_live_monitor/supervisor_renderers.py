"""Output contracts for continuous multi-download supervision."""

from __future__ import annotations

import json
import sys
from typing import Protocol, TextIO

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
