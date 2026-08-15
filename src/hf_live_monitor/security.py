"""Credential redaction and safe repository path handling."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

_REDACTED = "<redacted>"
_TOKEN_ASSIGNMENT = re.compile(r"(?i)(\b(?:hf_token|token|access_token)\s*=\s*)([^&\s]+)")
_BEARER = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")


def redact_args(args: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            result.append(_REDACTED)
            redact_next = False
            continue
        if arg.lower() in {"--token", "--access-token"}:
            result.append(arg)
            redact_next = True
            continue
        result.append(redact_text(arg))
    return tuple(result)


def redact_text(value: str) -> str:
    value = _TOKEN_ASSIGNMENT.sub(rf"\1{_REDACTED}", value)
    return _BEARER.sub(rf"\1{_REDACTED}", value)


def resolve_repo_path(root: Path, filename: str) -> Path:
    normalized = filename.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if (
        not normalized
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
    ):
        raise ValueError(f"unsafe repository path: {filename!r}")

    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*posix.parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"unsafe repository path: {filename!r}")
    return candidate
