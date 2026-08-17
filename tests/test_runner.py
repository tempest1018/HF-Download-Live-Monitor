import subprocess
from pathlib import Path
from typing import Any

import pytest

from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import (
    DownloadPlan,
    DownloadSpec,
    ManifestFile,
    MonitorError,
    RepoType,
)
from hf_download_live_monitor.runner import ManagedDownload, _stop_and_reap, build_hf_command


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


def test_build_hf_command_always_pins_revision() -> None:
    command = build_hf_command(DownloadSpec("owner/repo", Path("out")))

    assert command[-2:] == ("--revision", "main")


class FakeProcess:
    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.terminated = False
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        return self.code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class FakeApplication:
    def __init__(
        self,
        error: BaseException | None = None,
        code: int = 0,
        cancellation_requested: bool = False,
    ) -> None:
        self.error = error
        self.code = code
        self.stop_seen = False
        self.plan: DownloadPlan | None = None
        self.cancellation_requested = cancellation_requested

    def run(self, spec: DownloadSpec, **kwargs: Any) -> int:
        if self.error is not None:
            raise self.error
        self.stop_seen = kwargs["stop_when"]()
        self.plan = kwargs.get("plan")
        return self.code


def test_managed_download_propagates_child_exit_and_stop_condition() -> None:
    process = FakeProcess(code=7)
    application = FakeApplication()
    runner = ManagedDownload(application, process_factory=lambda _: process)
    result = runner.run(DownloadSpec("owner/repo", Path("out")))
    assert result == 7
    assert application.stop_seen
    assert process.wait_timeouts == [None]


def test_managed_download_returns_child_failure_when_monitor_succeeds() -> None:
    process = FakeProcess(code=9)

    assert (
        ManagedDownload(FakeApplication(), process_factory=lambda _: process).run(
            DownloadSpec("owner/repo", Path("out"))
        )
        == 9
    )


def test_managed_download_forwards_prepared_plan() -> None:
    process = FakeProcess()
    application = FakeApplication()
    spec = DownloadSpec("owner/repo", Path("out"), revision="a" * 40)
    plan = DownloadPlan(spec, "main", (ManifestFile("model.bin", 1),))

    ManagedDownload(application, process_factory=lambda _: process).run(spec, plan=plan)

    assert application.plan is plan


def test_managed_download_preserves_monitor_failure_code() -> None:
    process = FakeProcess(code=0)
    runner = ManagedDownload(FakeApplication(code=8), process_factory=lambda _: process)

    assert runner.run(DownloadSpec("owner/repo", Path("out"))) == 8


def test_managed_download_cancellation_terminates_and_reaps_running_child() -> None:
    process = FakeProcess()
    process.code = None  # type: ignore[assignment]

    def wait(timeout: float | None = None) -> int:
        process.wait_timeouts.append(timeout)
        return 143

    process.wait = wait  # type: ignore[method-assign]
    cancelled = exit_code_for(ErrorCategory.CANCELLED)
    result = ManagedDownload(
        FakeApplication(code=cancelled), process_factory=lambda _: process
    ).run(DownloadSpec("owner/repo", Path("out")))
    assert result == cancelled
    assert process.terminated
    assert process.wait_timeouts == [5.0]


def test_managed_download_cancellation_kills_after_timeout() -> None:
    process = FakeProcess()
    process.code = None  # type: ignore[assignment]

    def wait(timeout: float | None = None) -> int:
        process.wait_timeouts.append(timeout)
        if timeout is not None:
            raise subprocess.TimeoutExpired("hf", timeout)
        return 137

    process.wait = wait  # type: ignore[method-assign]
    cancelled = exit_code_for(ErrorCategory.CANCELLED)
    result = ManagedDownload(
        FakeApplication(code=cancelled), process_factory=lambda _: process
    ).run(DownloadSpec("owner/repo", Path("out")))
    assert result == cancelled
    assert process.terminated and process.killed
    assert process.wait_timeouts == [5.0, None]


def test_managed_download_cancellation_reaps_already_exited_child() -> None:
    process = FakeProcess(code=0)
    cancelled = exit_code_for(ErrorCategory.CANCELLED)
    result = ManagedDownload(
        FakeApplication(code=cancelled), process_factory=lambda _: process
    ).run(DownloadSpec("owner/repo", Path("out")))
    assert result == cancelled
    assert not process.terminated
    assert process.wait_timeouts == [None]


def test_managed_download_stops_cancelled_child_while_preserving_integrity_code() -> None:
    process = FakeProcess()
    process.code = None  # type: ignore[assignment]
    process.wait = lambda timeout=None: 143  # type: ignore[method-assign]
    integrity = exit_code_for(ErrorCategory.INTEGRITY)
    result = ManagedDownload(
        FakeApplication(code=integrity, cancellation_requested=True),
        process_factory=lambda _: process,
    ).run(DownloadSpec("owner/repo", Path("out")))
    assert result == integrity
    assert process.terminated


@pytest.mark.parametrize(
    "error",
    [
        MonitorError("observer.failed", "observer failed"),
        RuntimeError("render failed"),
        KeyboardInterrupt(),
    ],
)
def test_managed_download_stops_child_and_reraises_application_failure(
    error: BaseException,
) -> None:
    process = FakeProcess(code=130)
    process.code = None  # type: ignore[assignment]
    runner = ManagedDownload(FakeApplication(error=error), process_factory=lambda _: process)

    with pytest.raises(type(error)):
        runner.run(DownloadSpec("owner/repo", Path("out")))

    assert process.terminated
    assert process.wait_timeouts == [5.0]


def test_stop_and_reap_reaps_already_exited_process_without_terminating() -> None:
    process = FakeProcess(code=0)

    assert _stop_and_reap(process) == 0

    assert not process.terminated
    assert process.wait_timeouts == [None]


def test_stop_and_reap_kills_after_terminate_timeout() -> None:
    process = FakeProcess(code=0)
    process.code = None  # type: ignore[assignment]

    def wait(timeout: float | None = None) -> int:
        process.wait_timeouts.append(timeout)
        if timeout is not None:
            raise subprocess.TimeoutExpired("hf", timeout)
        return 137

    process.wait = wait  # type: ignore[method-assign]

    assert _stop_and_reap(process) == 137
    assert process.terminated
    assert process.killed
    assert process.wait_timeouts == [5.0, None]


def test_stop_and_reap_preserves_interrupt_during_terminate_wait() -> None:
    process = FakeProcess(code=0)
    process.code = None  # type: ignore[assignment]

    def wait(timeout: float | None = None) -> int:
        process.wait_timeouts.append(timeout)
        if timeout is not None:
            raise KeyboardInterrupt
        return 130

    process.wait = wait  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt):
        _stop_and_reap(process)

    assert process.killed
    assert process.wait_timeouts == [5.0, None]


def test_stop_and_reap_cleanup_failure_does_not_mask_interrupt() -> None:
    process = FakeProcess(code=0)
    process.code = None  # type: ignore[assignment]

    def wait(timeout: float | None = None) -> int:
        if timeout is not None:
            raise KeyboardInterrupt
        return 130

    def kill() -> None:
        raise OSError("secret process detail")

    process.wait = wait  # type: ignore[method-assign]
    process.kill = kill  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt) as caught:
        _stop_and_reap(process)

    notes = getattr(caught.value, "__notes__", None)
    cleanup_diagnostic = notes or [str(caught.value.__context__)]
    assert cleanup_diagnostic == ["Downloader cleanup also failed (OSError)."]


def test_stop_and_reap_handles_exit_race_during_terminate() -> None:
    process = FakeProcess(code=0)
    process.code = None  # type: ignore[assignment]

    def terminate() -> None:
        process.code = 0
        raise ProcessLookupError

    process.terminate = terminate  # type: ignore[method-assign]

    assert _stop_and_reap(process) == 0
    assert not process.killed
    assert process.wait_timeouts == [None]


def test_stop_and_reap_raises_cleanup_failure_when_called_directly() -> None:
    process = FakeProcess(code=0)
    process.code = None  # type: ignore[assignment]

    def terminate() -> None:
        raise OSError("terminate failed")

    process.terminate = terminate  # type: ignore[method-assign]

    with pytest.raises(OSError, match="terminate failed"):
        _stop_and_reap(process)


@pytest.mark.parametrize("failure_point", ["terminate", "wait"])
def test_cleanup_failure_does_not_hide_application_failure(failure_point: str) -> None:
    process = FakeProcess(code=0)
    process.code = None  # type: ignore[assignment]
    original = RuntimeError("renderer leaked")

    def terminate() -> None:
        if failure_point == "terminate":
            raise OSError("sensitive cleanup detail")

    def wait(timeout: float | None = None) -> int:
        if failure_point == "wait":
            raise OSError("sensitive cleanup detail")
        return 0

    process.terminate = terminate  # type: ignore[method-assign]
    process.wait = wait  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="renderer leaked") as caught:
        ManagedDownload(FakeApplication(error=original), process_factory=lambda _: process).run(
            DownloadSpec("owner/repo", Path("out"))
        )

    notes = getattr(caught.value, "__notes__", None)
    cleanup_diagnostic = notes or [str(caught.value.__context__)]
    assert cleanup_diagnostic == ["Downloader cleanup also failed (OSError)."]
    assert "sensitive cleanup detail" not in str(cleanup_diagnostic)


def test_managed_download_process_creation_failure_propagates() -> None:
    def fail(_: tuple[str, ...]) -> FakeProcess:
        raise OSError("not found")

    with pytest.raises(OSError, match="not found"):
        ManagedDownload(FakeApplication(), process_factory=fail).run(
            DownloadSpec("owner/repo", Path("out"))
        )


def test_managed_download_uses_resolved_plan_spec_for_child_command() -> None:
    commands: list[tuple[str, ...]] = []
    requested = DownloadSpec("owner/repo", Path("out"), revision="main", includes=("*.bin",))
    resolved = DownloadSpec("owner/repo", Path("out"), revision="a" * 40, includes=("*.bin",))
    plan = DownloadPlan(resolved, "main", (ManifestFile("model.bin", 1),))

    ManagedDownload(
        FakeApplication(),
        process_factory=lambda command: commands.append(command) or FakeProcess(),
    ).run(requested, plan=plan)

    assert commands[0][-4:] == ("--revision", "a" * 40, "--include", "*.bin")
