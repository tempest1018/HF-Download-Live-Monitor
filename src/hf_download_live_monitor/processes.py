"""Cross-platform process records and discovery providers."""

from __future__ import annotations

import ctypes
import json
import os
import shlex
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import psutil


@dataclass(frozen=True, slots=True, order=True)
class ProcessIdentity:
    pid: int
    start_token: str

    def __post_init__(self) -> None:
        if self.pid <= 0:
            raise ValueError("PID must be positive")
        if not self.start_token:
            raise ValueError("process start token must not be empty")


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    pid: int
    args: tuple[str, ...]
    cwd: Path
    start_token: str = "unknown"

    def __post_init__(self) -> None:
        if self.pid <= 0:
            raise ValueError("PID must be positive")
        if not self.start_token:
            raise ValueError("process start token must not be empty")

    @property
    def identity(self) -> ProcessIdentity:
        return ProcessIdentity(self.pid, self.start_token)


class ProcessProvider(Protocol):
    def discover(self) -> tuple[ProcessRecord, ...]: ...


class _PsutilProcess(Protocol):
    pid: int

    def cmdline(self) -> list[str]: ...

    def cwd(self) -> str: ...

    def create_time(self) -> float: ...


class PsutilProcessProvider:
    def __init__(
        self,
        iterator: Callable[[], Iterable[_PsutilProcess]] | None = None,
    ) -> None:
        self._iterator = iterator or psutil.process_iter

    def discover(self) -> tuple[ProcessRecord, ...]:
        records: list[ProcessRecord] = []
        try:
            iterator = iter(self._iterator())
        except (OSError, ValueError, psutil.Error):
            return ()
        while True:
            try:
                process = next(iterator)
            except StopIteration:
                break
            except (OSError, ValueError, psutil.Error):
                break
            try:
                args = tuple(process.cmdline())
                if not args:
                    continue
                records.append(
                    ProcessRecord(
                        process.pid,
                        args,
                        Path(process.cwd()),
                        f"{process.create_time():.9f}",
                    )
                )
            except (OSError, ValueError, psutil.Error):
                continue
        return tuple(sorted(records, key=lambda item: item.identity))


class PosixProcessProvider:
    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        self._proc_root = proc_root

    def discover(self) -> tuple[ProcessRecord, ...]:
        records: list[ProcessRecord] = []
        try:
            entries = tuple(self._proc_root.iterdir())
        except OSError:
            return ()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
                args = tuple(part.decode(errors="replace") for part in raw.split(b"\0") if part)
                cwd = (entry / "cwd").resolve(strict=True)
                if args:
                    records.append(ProcessRecord(int(entry.name), args, cwd))
            except (OSError, ValueError):
                continue
        return tuple(sorted(records, key=lambda item: item.pid))


_CIM_COMMAND = (
    "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
)


class WindowsProcessProvider:
    def __init__(
        self,
        runner: Callable[[str], str] | None = None,
        cwd_resolver: Callable[[int], Path] | None = None,
    ) -> None:
        self._runner = runner or _run_powershell
        self._cwd_resolver = cwd_resolver or _windows_process_cwd

    def discover(self) -> tuple[ProcessRecord, ...]:
        try:
            decoded: object = json.loads(self._runner(_CIM_COMMAND))
        except (OSError, ValueError, subprocess.SubprocessError):
            return ()
        items: list[object] = (
            cast(list[object], decoded) if isinstance(decoded, list) else [decoded]
        )
        records: list[ProcessRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = cast(dict[str, object], item)
            pid = record.get("ProcessId")
            command_line = record.get("CommandLine")
            if not isinstance(pid, int) or not isinstance(command_line, str) or not command_line:
                continue
            try:
                records.append(
                    ProcessRecord(
                        pid, _split_windows_command_line(command_line), self._cwd_resolver(pid)
                    )
                )
            except (OSError, ValueError):
                continue
        return tuple(sorted(records, key=lambda item: item.pid))


def system_process_provider(platform: str | None = None) -> ProcessProvider:
    platform = platform or os.name
    if platform in {"nt", "posix"}:
        return PsutilProcessProvider()
    raise ValueError(f"unsupported process platform: {platform}")


def _run_powershell(command: str) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return completed.stdout


def _windows_process_cwd(pid: int) -> Path:
    try:
        import psutil
    except ImportError as exc:
        raise OSError("psutil is required for Windows process working directories") from exc
    return Path(psutil.Process(pid).cwd())


def _split_windows_command_line(command_line: str) -> tuple[str, ...]:
    if os.name != "nt":
        return tuple(item.strip('"') for item in shlex.split(command_line, posix=False))
    argc = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    pointer = command_line_to_argv(command_line, ctypes.byref(argc))
    if not pointer:
        raise OSError("CommandLineToArgvW failed")
    try:
        return tuple(pointer[index] for index in range(argc.value))
    finally:
        ctypes.windll.kernel32.LocalFree(pointer)
