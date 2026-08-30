from pathlib import Path

from typer.testing import CliRunner

from hf_download_live_monitor.cli import cli

runner = CliRunner()


def test_help_exposes_complete_history_control_surface() -> None:
    result = runner.invoke(cli, ["history", "--help"])
    assert result.exit_code == 0
    for name in (
        "status",
        "enable",
        "disable",
        "configure",
        "list",
        "show",
        "export",
        "delete",
        "clear",
        "purge",
        "vacuum",
        "recover",
        "reset",
    ):
        assert name in result.stdout


def test_status_does_not_create_never_enabled_state(tmp_path: Path) -> None:
    state = tmp_path / "state"
    result = runner.invoke(
        cli,
        ["history", "status"],
        env={"HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR": str(state)},
    )
    assert result.exit_code == 0
    assert "never_enabled" in result.stdout
    assert not state.exists()


def test_enable_disable_and_purge_leave_user_in_control(tmp_path: Path) -> None:
    state = tmp_path / "state"
    environment = {"HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR": str(state)}
    enabled = runner.invoke(cli, ["history", "enable"], env=environment)
    assert enabled.exit_code == 0
    assert (state / "history.sqlite3").exists()
    status = runner.invoke(cli, ["history", "status", "--json"], env=environment)
    assert status.exit_code == 0
    assert '"enabled": true' in status.stdout
    disabled = runner.invoke(cli, ["history", "disable"], env=environment)
    assert disabled.exit_code == 0
    assert (state / "history.sqlite3").exists()
    purged = runner.invoke(cli, ["history", "purge", "--yes"], env=environment)
    assert purged.exit_code == 0
    assert not (state / "history.sqlite3").exists()
    assert not (state / "pseudonym.key").exists()


def test_monitor_help_exposes_history_privacy_options() -> None:
    for command in ("watch", "run", "attach"):
        result = runner.invoke(cli, [command, "--help"], terminal_width=160)
        assert result.exit_code == 0
        assert "--record-history" in result.stdout
        assert "--include-identifiers" in result.stdout
        assert "--history-path" in result.stdout


def test_reset_preserves_corrupt_database(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    database = state / "history.sqlite3"
    database.write_bytes(b"corrupt evidence")
    result = runner.invoke(
        cli,
        ["history", "reset", "--preserve-corrupt", "--yes"],
        env={"HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR": str(state)},
    )
    assert result.exit_code == 0
    assert database.exists()
    assert tuple(state.glob("history.sqlite3.corrupt-*"))


def test_purge_removes_corrupt_database_without_opening_it(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "history.sqlite3").write_bytes(b"corrupt")
    (state / "pseudonym.key").write_bytes(b"k" * 32)
    result = runner.invoke(
        cli,
        ["history", "purge", "--yes"],
        env={"HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR": str(state)},
    )
    assert result.exit_code == 0
    assert not (state / "history.sqlite3").exists()
    assert not (state / "pseudonym.key").exists()
