"""Fail-open conversion of monitor snapshots into private history summaries."""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.history_models import HistoryCheckpoint, HistoryOutcome
from hf_download_live_monitor.history_store import HistoryStore
from hf_download_live_monitor.models import DownloadSpec, MonitorError, ProgressSnapshot

_CHECKPOINT_INTERVAL = 5.0


class HistoryRecorder(Protocol):
    def start(self, spec: DownloadSpec, mode: str, observed_at_utc: float) -> str: ...

    def checkpoint(
        self,
        session_id: str,
        snapshot: ProgressSnapshot,
        observed_at_utc: float,
        *,
        final: bool,
        outcome: HistoryOutcome | None = None,
    ) -> None: ...

    def interrupt(self, session_id: str, observed_at_utc: float) -> None: ...

    def close(self) -> None: ...


class NullHistoryRecorder:
    def start(self, spec: DownloadSpec, mode: str, observed_at_utc: float) -> str:
        return ""

    def checkpoint(
        self,
        session_id: str,
        snapshot: ProgressSnapshot,
        observed_at_utc: float,
        *,
        final: bool,
        outcome: HistoryOutcome | None = None,
    ) -> None:
        return None

    def interrupt(self, session_id: str, observed_at_utc: float) -> None:
        return None

    def close(self) -> None:
        return None


@dataclass(slots=True)
class _Recording:
    checkpoint: HistoryCheckpoint
    last_write: float
    previous_bytes: int = 0
    rate_total: float = 0.0
    rate_samples: int = 0
    waiting_started: float | None = None


class SQLiteHistoryRecorder:
    def __init__(
        self,
        store: HistoryStore,
        *,
        include_identifiers: bool = False,
        utc_clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._include_identifiers = include_identifiers
        self._utc_clock = utc_clock
        self._recordings: dict[str, _Recording] = {}
        self.available = True
        self.warning: MonitorError | None = None
        try:
            store.mark_stale_interrupted(now_utc=float(utc_clock()))
        except Exception as exc:
            self._disable(exc)

    def start(self, spec: DownloadSpec, mode: str, observed_at_utc: float) -> str:
        if not self.available:
            return ""
        session_id = str(uuid.uuid4())
        repository_hmac, repository_label = self._store.pseudonymize(
            spec.repo, label="repository"
        )
        destination = str(spec.local_dir.resolve())
        destination_hmac, destination_label = self._store.pseudonymize(
            destination, label="destination"
        )
        current = HistoryCheckpoint.start(
            session_id=session_id,
            mode=mode,
            repo_type=spec.repo_type,
            repository_hmac=repository_hmac,
            destination_hmac=destination_hmac,
            repository_label=repository_label,
            destination_label=destination_label,
            repository=spec.repo if self._include_identifiers else None,
            local_dir=spec.local_dir.resolve() if self._include_identifiers else None,
            include_identifiers=self._include_identifiers,
            observed_at_utc=observed_at_utc,
        )
        try:
            self._store.checkpoint(current)
        except Exception as exc:
            self._disable(exc)
            return ""
        self._recordings[session_id] = _Recording(current, observed_at_utc)
        return session_id

    def checkpoint(
        self,
        session_id: str,
        snapshot: ProgressSnapshot,
        observed_at_utc: float,
        *,
        final: bool,
        outcome: HistoryOutcome | None = None,
    ) -> None:
        recording = self._recordings.get(session_id)
        if not self.available or recording is None:
            return
        if not final and observed_at_utc - recording.last_write < _CHECKPOINT_INTERVAL:
            return
        rate = snapshot.rate_bytes_per_second
        valid_rate = rate if rate is not None and math.isfinite(rate) and rate >= 0 else 0.0
        if valid_rate:
            recording.rate_total += valid_rate
            recording.rate_samples += 1
        waiting = snapshot.downloaded_bytes <= recording.previous_bytes and not final
        waiting_seconds = recording.checkpoint.waiting_seconds
        longest_wait = recording.checkpoint.longest_wait_seconds
        if waiting:
            recording.waiting_started = recording.waiting_started or recording.last_write
            interval = observed_at_utc - recording.waiting_started
            waiting_seconds += max(0.0, observed_at_utc - recording.last_write)
            longest_wait = max(longest_wait, interval)
        else:
            recording.waiting_started = None
        final_outcome = outcome or (_outcome(snapshot) if final else None)
        current = replace(
            recording.checkpoint,
            updated_at_utc=observed_at_utc,
            ended_at_utc=observed_at_utc if final else None,
            outcome=final_outcome,
            expected_bytes=snapshot.expected_bytes,
            downloaded_bytes=snapshot.downloaded_bytes,
            average_rate=(
                recording.rate_total / recording.rate_samples if recording.rate_samples else 0.0
            ),
            peak_rate=max(recording.checkpoint.peak_rate, valid_rate),
            waiting_seconds=waiting_seconds,
            longest_wait_seconds=longest_wait,
            verified_files=snapshot.verified_files,
            unverified_files=snapshot.complete_unverified_files,
            failed_files=snapshot.failed_files,
        )
        try:
            if final:
                self._store.finalize(current)
            else:
                self._store.checkpoint(current)
            recording.checkpoint = current
            recording.last_write = observed_at_utc
            recording.previous_bytes = snapshot.downloaded_bytes
            if final:
                self._recordings.pop(session_id, None)
            self._store.enforce_limits(now_utc=observed_at_utc)
        except Exception as exc:
            self._disable(exc)

    def interrupt(self, session_id: str, observed_at_utc: float) -> None:
        recording = self._recordings.pop(session_id, None)
        if not self.available or recording is None:
            return
        try:
            self._store.finalize(
                recording.checkpoint.finish(HistoryOutcome.INTERRUPTED, observed_at_utc)
            )
        except Exception as exc:
            self._disable(exc)

    def _disable(self, exc: Exception) -> None:
        self.available = False
        code = "history_busy" if "lock" in str(exc).lower() else "history_write_failed"
        self.warning = MonitorError(
            code,
            f"local history recording stopped ({type(exc).__name__})",
            recoverable=True,
            category=ErrorCategory.MONITOR,
        )

    def close(self) -> None:
        self._store.close()


def _outcome(snapshot: ProgressSnapshot) -> HistoryOutcome:
    if snapshot.failed_files:
        return HistoryOutcome.FAILED
    if snapshot.expected_bytes > 0 and snapshot.downloaded_bytes >= snapshot.expected_bytes:
        return HistoryOutcome.COMPLETED
    return HistoryOutcome.LOST
