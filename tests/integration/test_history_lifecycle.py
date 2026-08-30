import json
from pathlib import Path

from typer.testing import CliRunner

from hf_download_live_monitor.cli import cli
from hf_download_live_monitor.history_paths import resolve_history_paths
from hf_download_live_monitor.history_recorder import SQLiteHistoryRecorder
from hf_download_live_monitor.history_store import HistoryStore
from hf_download_live_monitor.models import DownloadSpec, FileProgress, FileState, ProgressSnapshot


def test_cli_records_sanitized_simulation_and_purges(tmp_path: Path) -> None:
    state = tmp_path / "history"
    environment = {"HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR": str(state)}
    runner = CliRunner()
    assert runner.invoke(cli, ["history", "status"], env=environment).exit_code == 0
    assert not state.exists()
    assert runner.invoke(cli, ["history", "enable"], env=environment).exit_code == 0

    store = HistoryStore.open(resolve_history_paths(environment=environment), create=False)
    assert store is not None
    recorder = SQLiteHistoryRecorder(store)
    spec = DownloadSpec("public/test-fixture", tmp_path / "download")
    session_id = recorder.start(spec, "watch", 100.0)
    snapshot = ProgressSnapshot(
        spec=spec,
        files=(FileProgress("fixture.bin", 4, 4, FileState.VERIFIED, 4.0),),
        observed_at=101.0,
        downloaded_bytes=4,
        expected_bytes=4,
        rate_bytes_per_second=4.0,
        eta_seconds=0.0,
        verified_files=1,
    )
    recorder.checkpoint(session_id, snapshot, 101.0, final=True)
    recorder.close()

    exported = runner.invoke(cli, ["history", "export", "--jsonl"], env=environment)
    assert exported.exit_code == 0
    payload = json.loads(exported.stdout)
    assert payload["outcome"] == "completed"
    assert "public/test-fixture" not in exported.stdout
    assert str(tmp_path) not in exported.stdout

    purged = runner.invoke(cli, ["history", "purge", "--yes"], env=environment)
    assert purged.exit_code == 0
    assert not tuple(state.glob("*")) if state.exists() else True
