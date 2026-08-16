from pathlib import Path
from typing import Any

from hf_download_live_monitor.models import DownloadSpec, RepoType
from hf_download_live_monitor.runner import ManagedDownload, build_hf_command


def test_build_hf_command_forwards_supported_options() -> None:
    spec = DownloadSpec(
        "owner/data",
        Path("out"),
        RepoType.DATASET,
        "v2",
        filenames=("a.json",),
        includes=("*.json",),
        excludes=("private*",),
    )
    command = build_hf_command(spec, executable="hf-custom")
    assert command == (
        "hf-custom",
        "download",
        "owner/data",
        "a.json",
        "--local-dir",
        str(Path("out")),
        "--repo-type",
        "dataset",
        "--revision",
        "v2",
        "--include",
        "*.json",
        "--exclude",
        "private*",
    )


class FakeProcess:
    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float | None = None) -> int:
        return self.code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class FakeApplication:
    def __init__(self, interrupt: bool = False) -> None:
        self.interrupt = interrupt
        self.stop_seen = False

    def run(self, spec: DownloadSpec, **kwargs: Any) -> int:
        if self.interrupt:
            raise KeyboardInterrupt
        self.stop_seen = kwargs["stop_when"]()
        return 0


def test_managed_download_propagates_child_exit_and_stop_condition() -> None:
    process = FakeProcess(code=7)
    application = FakeApplication()
    runner = ManagedDownload(application, process_factory=lambda _: process)
    result = runner.run(DownloadSpec("owner/repo", Path("out")))
    assert result == 7
    assert application.stop_seen


def test_managed_download_terminates_on_interrupt() -> None:
    process = FakeProcess(code=130)
    runner = ManagedDownload(FakeApplication(interrupt=True), process_factory=lambda _: process)
    assert runner.run(DownloadSpec("owner/repo", Path("out"))) == 130
    assert process.terminated
