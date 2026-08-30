"""Platform-native and containment-safe paths for local history."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.models import MonitorError

_ENV_OVERRIDE = "HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR"


@dataclass(frozen=True, slots=True)
class HistoryPaths:
    directory: Path
    database: Path
    pseudonym_key: Path
    custom_database: bool = False


def resolve_history_paths(
    *,
    override: Path | None = None,
    platform: str = sys.platform,
    environment: Mapping[str, str] = os.environ,
) -> HistoryPaths:
    if override is not None:
        if not override.is_absolute():
            raise _path_error("history path must be absolute")
        database = override
        return HistoryPaths(database.parent, database, database.parent / "pseudonym.key", True)

    env_root = environment.get(_ENV_OVERRIDE)
    if env_root:
        root = Path(env_root)
        if not _is_absolute(env_root, platform):
            raise _path_error(f"{_ENV_OVERRIDE} must be absolute")
        directory = root
    elif platform == "win32":
        directory = (
            Path(_required_env(environment, "LOCALAPPDATA"))
            / "HF Download Live Monitor"
            / "history"
        )
    elif platform == "darwin":
        directory = (
            Path(_required_env(environment, "HOME"))
            / "Library"
            / "Application Support"
            / "HF Download Live Monitor"
            / "history"
        )
    else:
        state_root = environment.get("XDG_STATE_HOME")
        if state_root:
            directory = Path(state_root) / "hf-download-live-monitor" / "history"
        else:
            directory = (
                Path(_required_env(environment, "HOME"))
                / ".local"
                / "state"
                / "hf-download-live-monitor"
                / "history"
            )
    if not _is_absolute(str(directory), platform):
        raise _path_error("resolved history directory must be absolute")
    return HistoryPaths(directory, directory / "history.sqlite3", directory / "pseudonym.key")


def validate_history_paths(paths: HistoryPaths) -> None:
    for candidate in (paths.database, paths.pseudonym_key):
        if candidate.is_symlink():
            raise _path_error("history files must not be symbolic links")
        try:
            candidate.resolve(strict=False).relative_to(paths.directory.resolve(strict=False))
        except ValueError as exc:
            raise _path_error("history file escapes its directory") from exc


def ensure_private_history_directory(paths: HistoryPaths) -> None:
    validate_history_paths(paths)
    paths.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        paths.directory.chmod(0o700)
    validate_history_paths(paths)


def _required_env(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise _path_error(f"{name} is required to resolve the history directory")
    return value


def _is_absolute(value: str, platform: str) -> bool:
    path_type = PureWindowsPath if platform == "win32" else PurePosixPath
    if platform != "win32":
        value = value.replace("\\", "/")
    return path_type(value).is_absolute()


def _path_error(message: str) -> MonitorError:
    return MonitorError("history_path_invalid", message, category=ErrorCategory.MONITOR)
