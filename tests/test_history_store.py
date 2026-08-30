from pathlib import Path

import pytest

from hf_download_live_monitor.history_models import (
    HistoryCheckpoint,
    HistoryConfig,
    HistoryHealth,
    HistoryOutcome,
    HistoryQuery,
)
from hf_download_live_monitor.history_paths import resolve_history_paths
from hf_download_live_monitor.history_store import SCHEMA_VERSION, HistoryStore
from hf_download_live_monitor.models import RepoType


def paths_under(tmp_path: Path):
    return resolve_history_paths(override=tmp_path / "state" / "history.sqlite3")


def checkpoint(session_id: str, observed_at: float = 100.0) -> HistoryCheckpoint:
    return HistoryCheckpoint.start(
        session_id=session_id,
        mode="watch",
        repo_type=RepoType.MODEL,
        repository_hmac="a" * 64,
        destination_hmac="b" * 64,
        repository_label="repository-aaaaaaaa",
        destination_label="destination-bbbbbbbb",
        observed_at_utc=observed_at,
    )


def test_open_without_create_does_not_touch_disk(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    assert HistoryStore.open(paths, create=False) is None
    assert not paths.directory.exists()


def test_read_only_open_enforces_query_only_mode(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    store = HistoryStore.open(paths, create=True)
    assert store is not None
    store.close()
    reader = HistoryStore.open(paths, create=False, readonly=True)
    assert reader is not None
    assert reader.connection.execute("PRAGMA query_only").fetchone()[0] == 1
    reader.close()


def test_create_initializes_versioned_wal_database(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    assert store.schema_version == SCHEMA_VERSION == 1
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert store.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 100
    store.close()


def test_settings_round_trip_without_persisting_identifier_opt_in(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    store.save_config(
        HistoryConfig(
            enabled=True,
            retention_days=None,
            max_size_bytes=8 * 1024 * 1024,
            include_identifiers=True,
        )
    )
    assert store.load_config() == HistoryConfig(
        enabled=True, retention_days=None, max_size_bytes=8 * 1024 * 1024
    )
    store.close()


def test_pseudonyms_are_stable_and_keyed(tmp_path: Path) -> None:
    first = HistoryStore.open(paths_under(tmp_path / "one"), create=True)
    second = HistoryStore.open(paths_under(tmp_path / "two"), create=True)
    assert first is not None and second is not None
    digest, label = first.pseudonymize("owner/repo", label="repository")
    assert first.pseudonymize("owner/repo", label="repository") == (digest, label)
    assert second.pseudonymize("owner/repo", label="repository")[0] != digest
    assert label == f"repository-{digest[:8]}"
    first.close()
    second.close()


def test_checkpoint_finalize_and_query_round_trip(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    item = checkpoint("session-1")
    store.checkpoint(item)
    store.finalize(item.finish(HistoryOutcome.COMPLETED, 120.0))
    record = store.get_record("session-1")
    assert record is not None
    assert record.checkpoint.outcome is HistoryOutcome.COMPLETED
    assert [row.checkpoint.session_id for row in store.list_records(HistoryQuery())] == [
        "session-1"
    ]
    store.close()


def test_delete_removes_one_record(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    store.checkpoint(checkpoint("session-1"))
    assert store.delete("session-1") is True
    assert store.delete("session-1") is False
    assert store.get_record("session-1") is None
    store.close()


def test_clear_before_removes_only_selected_range(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    store.finalize(checkpoint("old", 10.0).finish(HistoryOutcome.COMPLETED, 20.0))
    store.finalize(checkpoint("new", 100.0).finish(HistoryOutcome.COMPLETED, 120.0))
    assert store.clear_before(50.0) == 1
    assert store.get_record("old") is None
    assert store.get_record("new") is not None
    store.close()


def test_retention_deletes_old_terminal_rows_but_not_active(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    store.save_config(HistoryConfig(enabled=True, retention_days=30))
    cutoff = 1_800_000_000.0 - 30 * 86_400
    store.finalize(
        checkpoint("old-terminal", cutoff - 20).finish(HistoryOutcome.COMPLETED, cutoff - 10)
    )
    store.checkpoint(checkpoint("old-active", cutoff - 20))
    assert store.enforce_limits(now_utc=1_800_000_000.0) == 1
    assert store.get_record("old-terminal") is None
    assert store.get_record("old-active") is not None
    store.close()


def test_stale_active_rows_are_marked_interrupted(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    store.checkpoint(checkpoint("stale", 10.0))
    assert store.mark_stale_interrupted(now_utc=100.0) == 1
    record = store.get_record("stale")
    assert record is not None
    assert record.checkpoint.outcome is HistoryOutcome.INTERRUPTED
    assert record.checkpoint.ended_at_utc == 100.0
    store.close()


def test_health_reports_healthy_database(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    store = HistoryStore.open(paths, create=True)
    assert store is not None
    assert store.health() is HistoryHealth.HEALTHY
    store.close()


def test_purge_removes_only_managed_files(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    store = HistoryStore.open(paths, create=True)
    assert store is not None
    unrelated = paths.directory / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    removed = store.purge()
    assert removed >= 2
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not paths.database.exists()
    assert not paths.pseudonym_key.exists()


def test_version_zero_database_migrates_transactionally(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    store = HistoryStore.open(paths, create=True)
    assert store is not None
    store.connection.execute("DROP INDEX sessions_outcome_idx")
    store.connection.execute("UPDATE metadata SET schema_version = 0")
    store.connection.commit()
    store.close()
    migrated = HistoryStore.open(paths, create=True)
    assert migrated is not None
    assert migrated.schema_version == SCHEMA_VERSION
    assert migrated.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='sessions_outcome_idx'"
    ).fetchone()
    migrated.close()


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    store = HistoryStore.open(paths, create=True)
    assert store is not None
    store.connection.execute("UPDATE metadata SET schema_version = ?", (SCHEMA_VERSION + 1,))
    store.connection.commit()
    store.close()
    with pytest.raises(Exception, match="newer"):
        HistoryStore.open(paths, create=False)
