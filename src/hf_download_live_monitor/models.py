"""Typed domain models shared by the monitor's subsystems."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from hf_download_live_monitor.errors import ErrorCategory


class RepoType(str, Enum):
    MODEL = "model"
    DATASET = "dataset"
    SPACE = "space"

    @classmethod
    def parse(cls, value: str) -> RepoType:
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"unknown repository type {value!r}; choose {choices}") from exc


class FileState(str, Enum):
    QUEUED = "queued"
    MEASURING = "measuring"
    DOWNLOADING = "downloading"
    WAITING = "waiting"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    INCONSISTENT = "inconsistent"


@dataclass(frozen=True, slots=True)
class DownloadSpec:
    repo: str
    local_dir: Path
    repo_type: RepoType = RepoType.MODEL
    revision: str = "main"
    filenames: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.repo.strip():
            raise ValueError("repository must not be empty")
        if not self.revision.strip():
            raise ValueError("revision must not be empty")


@dataclass(frozen=True, slots=True)
class ManifestFile:
    filename: str
    expected_bytes: int
    sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_filename(self.filename)
        _validate_bytes(self.expected_bytes)
        if self.sha256 is not None:
            if re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256) is None:
                raise ValueError("SHA-256 digest must be exactly 64 hexadecimal characters")
            object.__setattr__(self, "sha256", self.sha256.lower())


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    spec: DownloadSpec
    requested_revision: str
    manifest: tuple[ManifestFile, ...]

    def __post_init__(self) -> None:
        if not self.requested_revision.strip():
            raise ValueError("requested revision must not be empty")


@dataclass(frozen=True, slots=True)
class FileObservation:
    filename: str
    expected_bytes: int
    visible_bytes: int
    final_bytes: int | None
    partial_bytes: int | None
    observed_at: float

    def __post_init__(self) -> None:
        _validate_filename(self.filename)
        for value in (
            self.expected_bytes,
            self.visible_bytes,
            self.final_bytes,
            self.partial_bytes,
        ):
            if value is not None:
                _validate_bytes(value)


@dataclass(frozen=True, slots=True)
class FileProgress:
    filename: str
    expected_bytes: int
    downloaded_bytes: int
    state: FileState
    rate_bytes_per_second: float | None = None
    eta_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    spec: DownloadSpec
    files: tuple[FileProgress, ...]
    observed_at: float
    downloaded_bytes: int
    expected_bytes: int
    rate_bytes_per_second: float | None
    eta_seconds: float | None
    errors: tuple[MonitorError, ...] = ()


@dataclass(frozen=True, slots=True)
class MonitorError(Exception):
    code: str
    message: str
    recoverable: bool = False
    category: ErrorCategory = ErrorCategory.MONITOR

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("error code must not be empty")
        if not self.message.strip():
            raise ValueError("error message must not be empty")
        Exception.__init__(self, self.message)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }


def _validate_filename(filename: str) -> None:
    if not filename.strip():
        raise ValueError("filename must not be empty")


def _validate_bytes(value: int) -> None:
    if value < 0:
        raise ValueError("byte counts must be non-negative")
