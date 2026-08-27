from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from typer.testing import CliRunner

from hf_download_live_monitor import cli as cli_module
from hf_download_live_monitor.cli import cli
from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import DownloadPlan, DownloadSpec, ManifestFile, MonitorError

runner = CliRunner()


def _plain(output: str) -> str:
    """Remove styling so help assertions are stable when CI forces color."""
    return click.unstyle(output)


def test_help_exposes_watch_command() -> None:
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "watch" in result.stdout
    assert "attach" in result.stdout
    assert "run" in result.stdout


def test_watch_help_exposes_repository_and_output_options() -> None:
    result = runner.invoke(cli, ["watch", "--help"])

    assert result.exit_code == 0
    for option in (
        "--local-dir",
        "--repo-type",
        "--revision",
        "--include",
        "--json",
        "--view",
        "--reduced-motion",
    ):
        assert option in _plain(result.stdout)


def test_watch_rejects_conflicting_structured_outputs() -> None:
    result = runner.invoke(
        cli,
        ["watch", "owner/repo", "--local-dir", "out", "--json", "--jsonl", "--once"],
    )
    assert result.exit_code != 0
    assert "cannot be used together" in result.output


def test_watch_rejects_nonpositive_refresh() -> None:
    result = runner.invoke(
        cli,
        ["watch", "owner/repo", "--local-dir", "out", "--refresh", "0", "--once"],
    )
    assert result.exit_code != 0


def test_watch_maps_handled_interrupt_cancellation_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module, "_watch_spec", lambda *args, **kwargs: exit_code_for(ErrorCategory.CANCELLED)
    )
    result = runner.invoke(cli, ["watch", "owner/repo", "--local-dir", "out"])
    assert result.exit_code == exit_code_for(ErrorCategory.CANCELLED)


def test_attach_maps_handled_interrupt_cancellation_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = SimpleNamespace(spec=DownloadSpec("owner/repo", Path("out")))
    monkeypatch.setattr(cli_module, "system_process_provider", lambda: object())
    monkeypatch.setattr(cli_module, "discover_downloads", lambda _: (candidate,))
    monkeypatch.setattr(cli_module, "select_download", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(
        cli_module, "_watch_spec", lambda *args, **kwargs: exit_code_for(ErrorCategory.CANCELLED)
    )
    result = runner.invoke(cli, ["attach"])
    assert result.exit_code == exit_code_for(ErrorCategory.CANCELLED)


def test_attach_help_exposes_pid_and_once() -> None:
    result = runner.invoke(cli, ["attach", "--help"])
    assert result.exit_code == 0
    assert "--pid" in _plain(result.stdout)
    assert "--once" in _plain(result.stdout)
    assert "--view" in _plain(result.stdout)
    assert "--discovery-refresh" in _plain(result.stdout)
    assert "--retention" in _plain(result.stdout)
    assert "--max-sessions" in _plain(result.stdout)


def test_continuous_attach_all_routes_to_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    class Supervisor:
        runs = 0

        def run(self) -> int:
            self.runs += 1
            return 0

    supervisor = Supervisor()
    monkeypatch.setattr(cli_module, "_make_supervisor", lambda **_: supervisor)
    result = runner.invoke(cli, ["attach", "--all", "--jsonl"])
    assert result.exit_code == 0
    assert supervisor.runs == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["--discovery-refresh", "0"],
        ["--retention", "-1"],
        ["--max-sessions", "0"],
    ],
)
def test_attach_all_validates_supervisor_limits(arguments: list[str]) -> None:
    result = runner.invoke(cli, ["attach", "--all", *arguments])
    assert result.exit_code == 2


def test_run_help_exposes_download_options() -> None:
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    for option in (
        "--local-dir",
        "--repo-type",
        "--revision",
        "--include",
        "--exclude",
        "--view",
        "--reduced-motion",
    ):
        assert option in _plain(result.stdout)


def test_help_options_remain_testable_when_color_is_forced(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")

    result = runner.invoke(cli, ["watch", "--help"])

    assert result.exit_code == 0
    assert "--local-dir" in _plain(result.stdout)


def test_run_prepares_and_preflights_before_start_using_resolved_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    events: list[object] = []
    resolved = "a" * 40

    class Repository:
        def prepare(self, spec: DownloadSpec) -> DownloadPlan:
            events.append("prepare")
            return DownloadPlan(
                DownloadSpec(spec.repo, spec.local_dir, revision=resolved),
                spec.revision,
                (ManifestFile("model.bin", 1),),
            )

    class Download:
        def __init__(self, application: object) -> None:
            events.append("managed")

        def run(self, spec: DownloadSpec, *, executable: str, plan: DownloadPlan) -> int:
            events.append(("start", spec.revision, plan))
            return 0

    monkeypatch.setattr(cli_module, "HubRepository", Repository)
    monkeypatch.setattr(
        cli_module, "validate_destination", lambda plan: events.append(("preflight", plan))
    )
    monkeypatch.setattr(cli_module, "_make_application", lambda **_: object())
    monkeypatch.setattr(cli_module, "ManagedDownload", Download)

    result = runner.invoke(cli, ["run", "owner/repo", "--local-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert events[0] == "prepare"
    assert events[1][0] == "preflight"
    assert events[2] == "managed"
    assert events[3][0:2] == ("start", resolved)


def test_run_preflight_failure_never_starts_child_and_uses_stable_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    plan = DownloadPlan(
        DownloadSpec("owner/repo", tmp_path, revision="a" * 40),
        "main",
        (ManifestFile("model.bin", 1),),
    )

    class Repository:
        def prepare(self, spec: DownloadSpec) -> DownloadPlan:
            return plan

    monkeypatch.setattr(cli_module, "HubRepository", Repository)
    monkeypatch.setattr(
        cli_module,
        "validate_destination",
        lambda _: (_ for _ in ()).throw(
            MonitorError(
                "insufficient_disk_space",
                "not enough space",
                category=ErrorCategory.DESTINATION,
            )
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "ManagedDownload",
        lambda _: (_ for _ in ()).throw(AssertionError("child constructed")),
    )

    result = runner.invoke(cli, ["run", "owner/repo", "--local-dir", str(tmp_path)])

    assert result.exit_code == exit_code_for(ErrorCategory.DESTINATION)
    assert "insufficient_disk_space" in result.stderr


def test_run_launch_failure_is_redacted_downloader_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    plan = DownloadPlan(
        DownloadSpec("owner/repo", tmp_path, revision="a" * 40),
        "main",
        (ManifestFile("model.bin", 1),),
    )

    class Repository:
        def prepare(self, spec: DownloadSpec) -> DownloadPlan:
            return plan

    class Download:
        def __init__(self, application: object) -> None:
            pass

        def run(self, spec: DownloadSpec, *, executable: str, plan: DownloadPlan) -> int:
            raise OSError("token=hf_secret")

    monkeypatch.setattr(cli_module, "HubRepository", Repository)
    monkeypatch.setattr(cli_module, "validate_destination", lambda _: None)
    monkeypatch.setattr(cli_module, "_make_application", lambda **_: object())
    monkeypatch.setattr(cli_module, "ManagedDownload", Download)

    result = runner.invoke(cli, ["run", "owner/repo", "--local-dir", str(tmp_path)])

    assert result.exit_code == exit_code_for(ErrorCategory.DOWNLOADER)
    assert "launch_failed" in result.stderr
    assert "hf_secret" not in result.stderr
    assert "<redacted>" in result.stderr
