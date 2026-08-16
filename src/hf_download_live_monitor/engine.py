"""Deterministic progress, transfer-rate, and ETA calculations."""

from __future__ import annotations

from collections import deque

from hf_download_live_monitor.models import (
    DownloadSpec,
    FileObservation,
    FileProgress,
    FileState,
    ManifestFile,
    ProgressSnapshot,
)


class ProgressEngine:
    def __init__(self, rate_window: float = 2.0, measuring_window: float = 0.75) -> None:
        if rate_window <= 0 or measuring_window < 0:
            raise ValueError("timing windows must be positive")
        self._rate_window = rate_window
        self._measuring_window = measuring_window
        self._histories: dict[str, deque[tuple[float, int]]] = {}

    def update(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        observations: tuple[FileObservation, ...],
        now: float,
    ) -> ProgressSnapshot:
        expected_names = {item.filename for item in manifest}
        for stale in self._histories.keys() - expected_names:
            del self._histories[stale]

        observed = {item.filename: item for item in observations}
        progress: list[FileProgress] = []
        for item in manifest:
            current = observed.get(item.filename)
            if current is None:
                current = FileObservation(item.filename, item.expected_bytes, 0, None, None, now)
            rate, elapsed = self._record(item.filename, now, current.visible_bytes)
            state = self._state(current, rate, elapsed)
            remaining = max(0, item.expected_bytes - current.visible_bytes)
            eta = remaining / rate if rate is not None and rate > 0 else None
            progress.append(
                FileProgress(
                    filename=item.filename,
                    expected_bytes=item.expected_bytes,
                    downloaded_bytes=min(current.visible_bytes, item.expected_bytes),
                    state=state,
                    rate_bytes_per_second=rate,
                    eta_seconds=eta,
                )
            )

        downloaded = sum(item.downloaded_bytes for item in progress)
        expected = sum(item.expected_bytes for item in progress)
        rates = [
            item.rate_bytes_per_second
            for item in progress
            if item.rate_bytes_per_second is not None
        ]
        aggregate_rate = sum(rates) if rates else None
        aggregate_eta = (
            (expected - downloaded) / aggregate_rate
            if aggregate_rate is not None and aggregate_rate > 0
            else None
        )
        return ProgressSnapshot(
            spec=spec,
            files=tuple(progress),
            observed_at=now,
            downloaded_bytes=downloaded,
            expected_bytes=expected,
            rate_bytes_per_second=aggregate_rate,
            eta_seconds=aggregate_eta,
        )

    def _record(self, filename: str, now: float, size: int) -> tuple[float | None, float]:
        history = self._histories.setdefault(filename, deque())
        history.append((now, size))
        while len(history) > 2 and history[1][0] <= now - self._rate_window:
            history.popleft()
        elapsed = now - history[0][0]
        if len(history) == 1 or elapsed <= 0:
            return None, elapsed
        delta = max(0, size - history[0][1])
        return delta / elapsed, elapsed

    def _state(self, observation: FileObservation, rate: float | None, elapsed: float) -> FileState:
        if observation.final_bytes is not None:
            return (
                FileState.COMPLETE
                if observation.final_bytes == observation.expected_bytes
                else FileState.INCONSISTENT
            )
        if observation.partial_bytes is None:
            return FileState.QUEUED
        if observation.partial_bytes >= observation.expected_bytes:
            return FileState.FINALIZING
        if rate is None or elapsed < self._measuring_window:
            return FileState.MEASURING
        return FileState.DOWNLOADING if rate > 0 else FileState.WAITING
