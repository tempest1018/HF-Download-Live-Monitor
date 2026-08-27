"""Managed execution of the official Hugging Face downloader."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from typing import Protocol

from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import DownloadPlan, DownloadSpec, ManifestFile, RepoType


class ChildProcess(Protocol):
    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class MonitorApplication(Protocol):
    def run(
        self,
        spec: DownloadSpec,
        *,
        manifest: tuple[ManifestFile, ...] | None = None,
        plan: DownloadPlan | None = None,
        once: bool = False,
        stop_when: Callable[[], bool] | None = None,
        handle_interrupt: bool = True,
        interrupt_cleanup: Callable[[], object] | None = None,
    ) -> int: ...


def build_hf_command(spec: DownloadSpec, *, executable: str = "hf") -> tuple[str, ...]:
    command = [
        executable,
        "download",
        spec.repo,
        *spec.filenames,
        "--local-dir",
        str(spec.local_dir),
    ]
    if spec.repo_type is not RepoType.MODEL:
        command.extend(("--repo-type", spec.repo_type.value))
    command.extend(("--revision", spec.revision))
    for pattern in spec.includes:
        command.extend(("--include", pattern))
    for pattern in spec.excludes:
        command.extend(("--exclude", pattern))
    return tuple(command)


class ManagedDownload:
    def __init__(
        self,
        application: MonitorApplication,
        *,
        process_factory: Callable[[tuple[str, ...]], ChildProcess] | None = None,
    ) -> None:
        self._application = application
        self._process_factory = process_factory or _start_process

    def run(
        self,
        spec: DownloadSpec,
        *,
        executable: str = "hf",
        manifest: tuple[ManifestFile, ...] | None = None,
        plan: DownloadPlan | None = None,
    ) -> int:
        command_spec = plan.spec if plan is not None else spec
        process = self._process_factory(build_hf_command(command_spec, executable=executable))
        try:
            monitor_code = self._application.run(
                spec,
                manifest=manifest,
                plan=plan,
                stop_when=lambda: process.poll() is not None,
                interrupt_cleanup=lambda: _stop_and_reap(process),
            )
            cancellation_requested = monitor_code == exit_code_for(ErrorCategory.CANCELLED) or bool(
                getattr(self._application, "cancellation_requested", False)
            )
            if cancellation_requested:
                _stop_and_reap(process)
                return monitor_code
            child_code = process.wait()
            return monitor_code or child_code
        except BaseException as error:
            try:
                _stop_and_reap(process)
            except BaseException as cleanup_error:
                _attach_cleanup_note(error, cleanup_error)
            raise


def _stop_and_reap(process: ChildProcess, grace: float = 5.0) -> int:
    if process.poll() is not None:
        return process.wait()
    try:
        process.terminate()
    except ProcessLookupError:
        return process.wait()
    try:
        return process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()
    except KeyboardInterrupt as interrupt:
        try:
            process.kill()
            process.wait()
        except BaseException as cleanup_error:
            _attach_cleanup_note(interrupt, cleanup_error)
        raise


def _attach_cleanup_note(error: BaseException, cleanup_error: BaseException) -> None:
    diagnostic = f"Downloader cleanup also failed ({type(cleanup_error).__name__})."
    add_note = getattr(error, "add_note", None)
    if add_note is not None:
        add_note(diagnostic)
    else:
        error.__context__ = RuntimeError(diagnostic)


def _start_process(command: tuple[str, ...]) -> ChildProcess:
    return subprocess.Popen(
        command,
        stdout=sys.stderr,
        stderr=None,
        env=_child_environment(),
    )


def _child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if not getattr(sys, "frozen", False):
        return environment
    for variable in ("LD_LIBRARY_PATH", "LIBPATH"):
        original = environment.pop(f"{variable}_ORIG", None)
        if original is None:
            environment.pop(variable, None)
        else:
            environment[variable] = original
    return environment
