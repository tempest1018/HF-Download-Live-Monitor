from pathlib import Path

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.history_models import HistoryOutcome
from hf_download_live_monitor.history_paths import resolve_history_paths
from hf_download_live_monitor.history_recorder import NullHistoryRecorder, SQLiteHistoryRecorder
from hf_download_live_monitor.history_store import HistoryStore
from hf_download_live_monitor.models import (
    DownloadSpec,
    FileProgress,
    FileState,
    MonitorError,
    ProgressSnapshot,
)


def make_store(tmp_path: Path) -> HistoryStore:
    store = HistoryStore.open(
        resolve_history_paths(override=tmp_path / "history" / "history.sqlite3"),
        create=True,
    )
    assert store is not None
    return store


def snapshot(
    *, repo: str = "private-owner/private-repo", downloaded: int = 5, failed: int = 0
) -> ProgressSnapshot:
    spec = DownloadSpec(repo, Path("C:/Sensitive/Models/private-repo"), revision="resolved-sha")
    return ProgressSnapshot(
        spec=spec,
        files=(FileProgress("secret-file.bin", 10, downloaded, FileState.DOWNLOADING, 2.0),),
        observed_at=1.0,
        downloaded_bytes=downloaded,
        expected_bytes=10,
        rate_bytes_per_second=2.0,
        eta_seconds=2.5,
        failed_files=failed,
    )


def test_null_recorder_is_inert() -> None:
    recorder = NullHistoryRecorder()
    assert recorder.start(snapshot().spec, "watch", 100.0) == ""
    recorder.checkpoint("", snapshot(), 101.0, final=False)
    recorder.finalize("", HistoryOutcome.LOST, 101.5)
    recorder.interrupt("", 102.0)
    recorder.close()


def test_recorder_checkpoints_at_most_every_five_seconds(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    recorder = SQLiteHistoryRecorder(store)
    session_id = recorder.start(snapshot().spec, "watch", 100.0)
    recorder.checkpoint(session_id, snapshot(downloaded=6), 101.0, final=False)
    first = store.get_record(session_id)
    assert first is not None and first.checkpoint.updated_at_utc == 100.0
    recorder.checkpoint(session_id, snapshot(downloaded=7), 105.0, final=False)
    second = store.get_record(session_id)
    assert second is not None and second.checkpoint.updated_at_utc == 105.0
    recorder.close()


def test_final_snapshot_records_completed_outcome(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    recorder = SQLiteHistoryRecorder(store)
    session_id = recorder.start(snapshot().spec, "run", 100.0)
    recorder.checkpoint(session_id, snapshot(downloaded=10), 110.0, final=True)
    record = store.get_record(session_id)
    assert record is not None
    assert record.checkpoint.outcome is HistoryOutcome.COMPLETED
    assert record.checkpoint.downloaded_bytes == 10
    recorder.close()


def test_default_database_never_contains_readable_identifiers(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    recorder = SQLiteHistoryRecorder(store)
    current = snapshot()
    session_id = recorder.start(current.spec, "watch", 100.0)
    recorder.checkpoint(session_id, current, 105.0, final=True)
    store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    blob = store.paths.database.read_bytes()
    assert b"private-owner" not in blob
    assert b"private-repo" not in blob
    assert b"Sensitive" not in blob
    assert b"secret-file" not in blob
    recorder.close()


def test_identifier_opt_in_is_scoped_to_recorder(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    recorder = SQLiteHistoryRecorder(store, include_identifiers=True)
    current = snapshot(repo="public-owner/public-repo")
    session_id = recorder.start(current.spec, "watch", 100.0)
    record = store.get_record(session_id)
    assert record is not None
    assert record.checkpoint.repository_identifier == "public-owner/public-repo"
    assert record.checkpoint.destination_identifier == str(current.spec.local_dir)
    recorder.close()


def test_store_failure_disables_only_recorder(tmp_path: Path, monkeypatch) -> None:
    store = make_store(tmp_path)
    recorder = SQLiteHistoryRecorder(store)

    def fail(_value: object) -> None:
        raise OSError

    monkeypatch.setattr(store, "checkpoint", fail)
    assert recorder.start(snapshot().spec, "watch", 100.0) == ""
    assert recorder.available is False
    assert recorder.warning is not None
    assert recorder.warning.code == "history_write_failed"
    recorder.close()


def test_interrupt_marks_active_record(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    recorder = SQLiteHistoryRecorder(store)
    session_id = recorder.start(snapshot().spec, "attach", 100.0)
    recorder.interrupt(session_id, 110.0)
    record = store.get_record(session_id)
    assert record is not None
    assert record.checkpoint.outcome is HistoryOutcome.INTERRUPTED
    recorder.close()


def test_diagnostic_is_sanitized_and_loaded_with_record(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    recorder = SQLiteHistoryRecorder(store)
    session_id = recorder.start(snapshot().spec, "watch", 100.0)
    recorder.diagnostic(
        session_id,
        MonitorError(
            "download_failed",
            "token hf_abcdefghijklmnopqrstuvwxyz123456 at C:/Sensitive/private.txt",
            recoverable=True,
            category=ErrorCategory.DOWNLOADER,
        ),
        101.0,
    )
    record = store.get_record(session_id)
    assert record is not None
    assert len(record.diagnostics) == 1
    diagnostic = record.diagnostics[0]
    assert diagnostic.code == "download_failed"
    assert "hf_" not in diagnostic.message
    assert "Sensitive" not in diagnostic.message
    recorder.close()
