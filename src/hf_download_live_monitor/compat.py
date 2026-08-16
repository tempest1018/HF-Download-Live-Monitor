"""Compatibility helpers for the Hugging Face local-dir cache layout."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path, PurePosixPath


def short_cache_hash(filename: str) -> str:
    """Return the short cache key used by supported huggingface_hub releases."""
    return base64.urlsafe_b64encode(hashlib.sha1(filename.encode()).digest()).decode()


def incomplete_candidates(local_dir: Path, filename: str) -> tuple[Path, ...]:
    repo_path = PurePosixPath(filename.replace("\\", "/"))
    directory = local_dir / ".cache" / "huggingface" / "download" / Path(*repo_path.parent.parts)
    prefix = short_cache_hash(f"{repo_path.name}.metadata")
    try:
        return tuple(
            sorted(path for path in directory.glob(f"{prefix}.*.incomplete") if path.is_file())
        )
    except OSError:
        return ()
