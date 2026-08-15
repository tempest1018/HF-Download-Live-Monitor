from typer.testing import CliRunner

from hf_live_monitor.cli import cli

runner = CliRunner()


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
        assert option in result.stdout


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
    assert "--pid" in result.stdout
    assert "--once" in result.stdout


def test_run_help_exposes_download_options() -> None:
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    for option in ("--local-dir", "--repo-type", "--revision", "--include", "--exclude"):
        assert option in result.stdout
