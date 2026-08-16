import click
from typer.testing import CliRunner

from hf_live_monitor.cli import cli

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
    for option in ("--local-dir", "--repo-type", "--revision", "--include", "--json"):
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


def test_attach_help_exposes_pid_and_once() -> None:
    result = runner.invoke(cli, ["attach", "--help"])
    assert result.exit_code == 0
    assert "--pid" in _plain(result.stdout)
    assert "--once" in _plain(result.stdout)


def test_run_help_exposes_download_options() -> None:
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    for option in ("--local-dir", "--repo-type", "--revision", "--include", "--exclude"):
        assert option in _plain(result.stdout)


def test_help_options_remain_testable_when_color_is_forced(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")

    result = runner.invoke(cli, ["watch", "--help"])

    assert result.exit_code == 0
    assert "--local-dir" in _plain(result.stdout)
