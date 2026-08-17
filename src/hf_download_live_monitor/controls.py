"""Best-effort, non-blocking terminal controls."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any, Protocol, TextIO

from hf_download_live_monitor.layout import ViewMode


@dataclass(frozen=True, slots=True)
class DisplayState:
    view_mode: ViewMode = ViewMode.BALANCED
    show_details: bool = False
    show_events: bool = False
    show_help: bool = False
    cancel_requested: bool = False

    def apply_key(self, key: str) -> DisplayState:
        key = key.lower()
        if key == "v":
            modes = (ViewMode.COMPACT, ViewMode.BALANCED, ViewMode.DETAILED)
            return replace(self, view_mode=modes[(modes.index(self.view_mode) + 1) % len(modes)])
        toggles = {"d": "show_details", "e": "show_events", "?": "show_help"}
        if key in toggles:
            field = toggles[key]
            return replace(self, **{field: not getattr(self, field)})
        if key == "q":
            return replace(self, cancel_requested=True)
        return self


class KeyboardController:
    """Poll a key reader; terminal failures silently disable interaction."""

    def __init__(
        self,
        read_key: Callable[[], str | None] | None = None,
        *,
        close_reader: Callable[[], None] | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self._enabled = True
        self._closed = False
        self._close_reader = close_reader
        if read_key is not None:
            self._read_key = read_key
            return
        terminal = stream or sys.stdin
        if not terminal.isatty():
            self._enabled = False
            self._read_key = lambda: None
            return
        try:
            self._read_key, platform_close = _platform_reader(terminal)
            self._close_reader = platform_close
        except Exception:
            self._enabled = False
            self._read_key = lambda: None

    def poll(self, state: DisplayState) -> DisplayState:
        if not self._enabled or self._closed:
            return state
        try:
            key = self._read_key()
        except Exception:
            self._enabled = False
            return state
        return state if key is None else state.apply_key(key)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._close_reader is not None:
            with suppress(Exception):
                self._close_reader()


def _platform_reader(stream: TextIO) -> tuple[Callable[[], str | None], Callable[[], None]]:
    if os.name == "nt":
        import msvcrt

        return _windows_reader(msvcrt)

    import select
    import termios
    import tty

    return _posix_reader(stream, select, termios, tty)


class _WindowsConsole(Protocol):
    def kbhit(self) -> bool: ...

    def getwch(self) -> str: ...


def _windows_reader(
    console: _WindowsConsole,
) -> tuple[Callable[[], str | None], Callable[[], None]]:
    return (lambda: console.getwch() if console.kbhit() else None), lambda: None


def _posix_reader(
    stream: TextIO,
    select_module: Any,
    termios_module: Any,
    tty_module: Any,
) -> tuple[Callable[[], str | None], Callable[[], None]]:
    descriptor = stream.fileno()
    previous = termios_module.tcgetattr(descriptor)
    tty_module.setcbreak(descriptor)

    def read() -> str | None:
        ready, _, _ = select_module.select([stream], [], [], 0)
        return stream.read(1) if ready else None

    def close() -> None:
        termios_module.tcsetattr(descriptor, termios_module.TCSADRAIN, previous)

    return read, close
