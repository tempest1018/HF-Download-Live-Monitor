"""Managed execution of the official Hugging Face downloader."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Protocol

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
    if spec.revision != "main":
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
        process = self._process_factory(build_hf_command(spec, executable=executable))
        try:
            monitor_code = self._application.run(
                spec,
                manifest=manifest,
                plan=plan,
                stop_when=lambda: process.poll() is not None,
                handle_interrupt=False,
            )
            child_code = process.wait()
            return monitor_code or child_code
        except KeyboardInterrupt:
            process.terminate()
            try:
                return process.wait(timeout=5.0)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                process.kill()
                return process.wait()


def _start_process(command: tuple[str, ...]) -> ChildProcess:
    return subprocess.Popen(command)
