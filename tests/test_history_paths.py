import os
from pathlib import Path

import pytest

from hf_download_live_monitor.history_paths import (
    ensure_private_history_directory,
    resolve_history_paths,
    validate_history_paths,
)
from hf_download_live_monitor.models import MonitorError


@pytest.mark.parametrize(
    ("platform", "environment", "suffix"),
    [
        ("win32", {"LOCALAPPDATA": "C:/Local"}, "HF Download Live Monitor/history"),
        (
            "darwin",
            {"HOME": "/Users/tester"},
            "Library/Application Support/HF Download Live Monitor/history",
        ),
        (
            "linux",
            {"HOME": "/home/tester"},
            ".local/state/hf-download-live-monitor/history",
        ),
    ],
)
def test_default_history_root_is_platform_native(
    platform: str, environment: dict[str, str], suffix: str
) -> None:
    paths = resolve_history_paths(platform=platform, environment=environment)
    assert paths.directory.as_posix().endswith(suffix)
    assert paths.database.name == "history.sqlite3"
    assert paths.pseudonym_key.name == "pseudonym.key"


def test_xdg_state_home_takes_precedence() -> None:
    paths = resolve_history_paths(
        platform="linux", environment={"HOME": "/home/tester", "XDG_STATE_HOME": "/state"}
    )
    assert paths.directory == Path("/state/hf-download-live-monitor/history")


def test_relative_override_is_rejected() -> None:
    with pytest.raises(MonitorError, match="absolute"):
        resolve_history_paths(override=Path("relative/history.db"))


def test_database_override_uses_its_parent_for_key(tmp_path: Path) -> None:
    database = tmp_path / "portable.sqlite3"
    paths = resolve_history_paths(override=database)
    assert paths.database == database
    assert paths.directory == tmp_path
    assert paths.pseudonym_key == tmp_path / "pseudonym.key"


def test_directory_creation_is_explicit_and_private(tmp_path: Path) -> None:
    paths = resolve_history_paths(override=tmp_path / "state" / "history.sqlite3")
    assert not paths.directory.exists()
    ensure_private_history_directory(paths)
    assert paths.directory.is_dir()
    if os.name != "nt":
        assert paths.directory.stat().st_mode & 0o777 == 0o700


def test_symlinked_database_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("not a database", encoding="utf-8")
    database = tmp_path / "history.sqlite3"
    try:
        database.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")
    paths = resolve_history_paths(override=database)
    with pytest.raises(MonitorError, match="symbolic link"):
        validate_history_paths(paths)
