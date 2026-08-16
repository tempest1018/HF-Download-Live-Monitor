"""Requested-file selection for repository manifests."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase

from hf_download_live_monitor.models import ManifestFile, MonitorError


def _normalize(value: str) -> str:
    return value.replace("\\", "/").lstrip("./")


def select_manifest(
    manifest: Iterable[ManifestFile],
    *,
    filenames: Sequence[str] = (),
    includes: Sequence[str] = (),
    excludes: Sequence[str] = (),
) -> tuple[ManifestFile, ...]:
    """Select requested files, with exclusions taking final precedence."""
    available = {_normalize(item.filename): item for item in manifest}
    requested = tuple(_normalize(item) for item in filenames)
    missing = sorted(set(requested).difference(available))
    if missing:
        raise MonitorError(
            "requested_file_missing",
            f"requested files are absent from repository metadata: {', '.join(missing)}",
        )

    candidates = set(requested) if requested else set(available)
    if includes:
        candidates = {
            name for name in candidates if any(fnmatchcase(name, pattern) for pattern in includes)
        }
    if excludes:
        candidates = {
            name
            for name in candidates
            if not any(fnmatchcase(name, pattern) for pattern in excludes)
        }
    return tuple(available[name] for name in sorted(candidates))
