from pathlib import Path

import pytest

from hf_download_live_monitor.history_models import HistoryHealth
from hf_download_live_monitor.history_paths import resolve_history_paths
from hf_download_live_monitor.history_store import HistoryStore, inspect_history_health
from hf_download_live_monitor.models import MonitorError


def paths_under(tmp_path: Path):
    return resolve_history_paths(override=tmp_path / "state" / "history.sqlite3")


def test_corruption_is_reported_without_modifying_source(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    paths.directory.mkdir(parents=True)
    paths.database.write_bytes(b"not sqlite and must remain unchanged")
    before = paths.database.read_bytes()
    assert inspect_history_health(paths) is HistoryHealth.CORRUPT
    assert paths.database.read_bytes() == before


def test_recovery_creates_separate_valid_database(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    store = HistoryStore.open(paths, create=True)
    assert store is not None
    output = tmp_path / "recovered.sqlite3"
    store.recover(output)
    assert output.exists()
    assert output != paths.database
    store.close()


def test_recovery_refuses_existing_output(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    output = tmp_path / "existing.db"
    output.write_bytes(b"keep")
    with pytest.raises(MonitorError, match="already exists"):
        store.recover(output)
    assert output.read_bytes() == b"keep"
    store.close()


def test_recovery_requires_absolute_output(tmp_path: Path) -> None:
    store = HistoryStore.open(paths_under(tmp_path), create=True)
    assert store is not None
    with pytest.raises(MonitorError, match="absolute"):
        store.recover(Path("relative.db"))
    store.close()
