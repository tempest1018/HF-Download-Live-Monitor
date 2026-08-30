"""Immutable contracts for private local download history."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from hf_download_live_monitor.models import RepoType

DEFAULT_HISTORY_MAX_BYTES = 64 * 1024 * 1024
MAX_HISTORY_QUERY_LIMIT = 1_000
MAX_DIAGNOSTIC_LENGTH = 512


class HistoryOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class WaitingState(str, Enum):
    PROGRESSING = "progressing"
    WAITING_FOR_DATA = "waiting_for_data"
    FINALIZING = "finalizing"
    INTERRUPTED = "interrupted"


class HistoryHealth(str, Enum):
    NEVER_ENABLED = "never_enabled"
    HEALTHY = "healthy"
    LOCKED = "locked"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class HistoryConfig:
    enabled: bool = False
    retention_days: int | None = 30
    max_size_bytes: int = DEFAULT_HISTORY_MAX_BYTES
    include_identifiers: bool = False

    def __post_init__(self) -> None:
        if self.retention_days is not None and self.retention_days < 1:
            raise ValueError("retention days must be positive or unlimited")
        if self.max_size_bytes < 1024 * 1024:
            raise ValueError("history size cap must be at least 1 MiB")

    @classmethod
    def defaults(cls) -> HistoryConfig:
        return cls()


@dataclass(frozen=True, slots=True)
class HistoryDiagnostic:
    observed_at_utc: float
    category: str
    code: str
    message: str
    recoverable: bool = False

    def __post_init__(self) -> None:
        if self.observed_at_utc < 0:
            raise ValueError("diagnostic timestamp must be non-negative")
        if not self.category or not self.code or not self.message:
            raise ValueError("diagnostic fields must not be empty")
        if len(self.message) > MAX_DIAGNOSTIC_LENGTH:
            raise ValueError("diagnostic message is too long")


@dataclass(frozen=True, slots=True)
class HistoryCheckpoint:
    session_id: str
    mode: str
    repo_type: RepoType
    repository_hmac: str
    destination_hmac: str
    repository_label: str
    destination_label: str
    started_at_utc: float
    updated_at_utc: float
    repository_identifier: str | None = None
    destination_identifier: str | None = None
    expected_bytes: int = 0
    downloaded_bytes: int = 0
    average_rate: float = 0.0
    peak_rate: float = 0.0
    waiting_seconds: float = 0.0
    longest_wait_seconds: float = 0.0
    verified_files: int = 0
    unverified_files: int = 0
    failed_files: int = 0
    outcome: HistoryOutcome | None = None
    ended_at_utc: float | None = None
    revision_kind: str = "requested"

    def __post_init__(self) -> None:
        if not self.session_id or not self.mode:
            raise ValueError("history identity fields must not be empty")
        if len(self.repository_hmac) != 64 or len(self.destination_hmac) != 64:
            raise ValueError("history HMAC values must be 64 characters")
        values = (
            self.started_at_utc,
            self.updated_at_utc,
            self.expected_bytes,
            self.downloaded_bytes,
            self.average_rate,
            self.peak_rate,
            self.waiting_seconds,
            self.longest_wait_seconds,
            self.verified_files,
            self.unverified_files,
            self.failed_files,
        )
        if any(value < 0 for value in values):
            raise ValueError("history numeric values must be non-negative")
        if self.ended_at_utc is not None and self.ended_at_utc < self.started_at_utc:
            raise ValueError("history end time precedes start time")

    @classmethod
    def start(
        cls,
        *,
        session_id: str,
        mode: str,
        repo_type: RepoType,
        repository_hmac: str,
        destination_hmac: str,
        repository_label: str,
        destination_label: str,
        repository: str | None = None,
        local_dir: Path | None = None,
        include_identifiers: bool = False,
        observed_at_utc: float,
    ) -> HistoryCheckpoint:
        if (repository is not None or local_dir is not None) and not include_identifiers:
            raise ValueError("readable identifiers require identifier opt-in")
        return cls(
            session_id=session_id,
            mode=mode,
            repo_type=repo_type,
            repository_hmac=repository_hmac,
            destination_hmac=destination_hmac,
            repository_label=repository_label,
            destination_label=destination_label,
            repository_identifier=repository if include_identifiers else None,
            destination_identifier=str(local_dir) if include_identifiers and local_dir else None,
            started_at_utc=observed_at_utc,
            updated_at_utc=observed_at_utc,
        )

    def finish(self, outcome: HistoryOutcome, observed_at_utc: float) -> HistoryCheckpoint:
        return replace(
            self,
            outcome=outcome,
            ended_at_utc=observed_at_utc,
            updated_at_utc=observed_at_utc,
        )


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    checkpoint: HistoryCheckpoint
    diagnostics: tuple[HistoryDiagnostic, ...] = ()

    def __getattr__(self, name: str) -> object:
        return getattr(self.checkpoint, name)


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    outcomes: tuple[HistoryOutcome, ...] = ()
    since_utc: float | None = None
    before_utc: float | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_HISTORY_QUERY_LIMIT:
            raise ValueError(f"history query limit must be between 1 and {MAX_HISTORY_QUERY_LIMIT}")
        for value in (self.since_utc, self.before_utc):
            if value is not None and value < 0:
                raise ValueError("history query timestamps must be non-negative")
