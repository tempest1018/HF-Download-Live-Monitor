"""Deterministic progress, transfer-rate, and ETA calculations."""

from __future__ import annotations

from collections import deque
from typing import cast, overload

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.integrity import IntegrityVerifier, VerificationResult
from hf_download_live_monitor.models import (
    DownloadPlan,
    DownloadSpec,
    FileObservation,
    FileProgress,
    FileState,
    ManifestFile,
    MonitorError,
    ProgressSnapshot,
)


class ProgressEngine:
    def __init__(
        self,
        rate_window: float = 2.0,
        measuring_window: float = 0.75,
        verifier: IntegrityVerifier | None = None,
    ) -> None:
        if rate_window <= 0 or measuring_window < 0:
            raise ValueError("timing windows must be positive")
        self._rate_window = rate_window
        self._measuring_window = measuring_window
        self._histories: dict[str, deque[tuple[float, int]]] = {}
        self._rate_history: deque[float] = deque(maxlen=24)
        self._verifier = verifier or IntegrityVerifier()

    @overload
    def update(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        observations: tuple[FileObservation, ...],
        now: float,
        final: bool = False,
    ) -> ProgressSnapshot: ...

    @overload
    def update(
        self,
        spec: DownloadPlan,
        manifest: tuple[FileObservation, ...],
        observations: float,
        now: None = None,
        final: bool = False,
    ) -> ProgressSnapshot: ...

    @overload
    def update(
        self,
        spec: DownloadPlan,
        manifest: tuple[FileObservation, ...],
        observations: None = None,
        now: float = ...,
        final: bool = False,
    ) -> ProgressSnapshot: ...

    def update(
        self,
        spec: DownloadSpec | DownloadPlan,
        manifest: tuple[ManifestFile, ...] | tuple[FileObservation, ...],
        observations: tuple[FileObservation, ...] | float | None = None,
        now: float | None = None,
        final: bool = False,
    ) -> ProgressSnapshot:
        if isinstance(spec, DownloadPlan):
            plan = spec
            current_observations = cast(tuple[FileObservation, ...], manifest)
            if isinstance(observations, (int, float)):
                if now is not None:
                    raise TypeError("plan update received time both positionally and by keyword")
                now = float(observations)
            elif observations is not None:
                raise TypeError("plan update expects observations followed by an observation time")
        else:
            if observations is None or isinstance(observations, (int, float)):
                raise TypeError(
                    "legacy update requires manifest, observations, and observation time"
                )
            plan = DownloadPlan(spec, spec.revision, cast(tuple[ManifestFile, ...], manifest))
            current_observations = observations

        if now is None:
            raise TypeError("update requires an observation time")

        spec = plan.spec
        manifest = plan.manifest
        expected_names = {item.filename for item in manifest}
        for stale in self._histories.keys() - expected_names:
            del self._histories[stale]

        observed = {item.filename: item for item in current_observations}
        progress: list[FileProgress] = []
        errors: list[MonitorError] = []
        for item in manifest:
            current = observed.get(item.filename)
            was_observed = current is not None
            if current is None:
                current = FileObservation(item.filename, item.expected_bytes, 0, None, None, now)
            rate, elapsed = self._record(item.filename, now, current.visible_bytes)
            state, error = self._state(
                plan, item, current, rate, elapsed, final, was_observed=was_observed
            )
            if error is not None:
                errors.append(error)
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
        self._rate_history.append(max(0.0, aggregate_rate or 0.0))
        return ProgressSnapshot(
            spec=spec,
            files=tuple(progress),
            observed_at=now,
            downloaded_bytes=downloaded,
            expected_bytes=expected,
            rate_bytes_per_second=aggregate_rate,
            eta_seconds=aggregate_eta,
            requested_revision=plan.requested_revision,
            verified_files=sum(item.state is FileState.VERIFIED for item in progress),
            complete_unverified_files=sum(
                item.state is FileState.COMPLETE_UNVERIFIED for item in progress
            ),
            failed_files=sum(item.state is FileState.FAILED for item in progress),
            rate_history=tuple(self._rate_history),
            errors=tuple(errors),
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

    def _state(
        self,
        plan: DownloadPlan,
        manifest_file: ManifestFile,
        observation: FileObservation,
        rate: float | None,
        elapsed: float,
        final: bool,
        *,
        was_observed: bool,
    ) -> tuple[FileState, MonitorError | None]:
        expected = manifest_file.expected_bytes
        if final and observation.expected_bytes != expected:
            return FileState.FAILED, self._integrity_error(
                "manifest_size_mismatch",
                f"{manifest_file.filename} observation size metadata differs from the manifest",
            )
        if observation.visible_bytes > expected:
            return FileState.FAILED, self._integrity_error(
                "oversized_file", f"{manifest_file.filename} exceeds its expected size"
            )
        if final and not was_observed:
            return FileState.FAILED, self._integrity_error(
                "missing_file", f"{manifest_file.filename} is missing after download"
            )
        if final and observation.final_bytes is None:
            return FileState.FAILED, self._integrity_error(
                "incomplete_file", f"{manifest_file.filename} is incomplete after download"
            )
        if observation.final_bytes is not None:
            if observation.final_bytes != expected:
                if final:
                    return FileState.FAILED, self._integrity_error(
                        "incomplete_file",
                        f"{manifest_file.filename} does not match its expected final size",
                    )
                return FileState.INCONSISTENT, None
            if manifest_file.sha256 is None:
                return FileState.COMPLETE_UNVERIFIED, None
            result = self._verify(plan, manifest_file, final)
            if result.state is FileState.FAILED:
                detail = result.error or "SHA-256 digest does not match repository metadata"
                return FileState.FAILED, self._integrity_error("integrity_mismatch", detail)
            if final and result.state is not FileState.VERIFIED:
                return FileState.FAILED, self._integrity_error(
                    "verification_incomplete",
                    f"{manifest_file.filename} could not be verified after download",
                )
            return result.state, None
        if observation.partial_bytes is None:
            return FileState.QUEUED, None
        if observation.partial_bytes >= expected:
            return FileState.FINALIZING, None
        if rate is None or elapsed < self._measuring_window:
            return FileState.MEASURING, None
        return (FileState.DOWNLOADING if rate > 0 else FileState.WAITING), None

    def _verify(
        self, plan: DownloadPlan, manifest_file: ManifestFile, final: bool
    ) -> VerificationResult:
        path = plan.spec.local_dir / manifest_file.filename
        if final:
            return self._verifier.verify_now(path, manifest_file.sha256)
        return self._verifier.request(path, manifest_file.sha256)

    @staticmethod
    def _integrity_error(code: str, message: str) -> MonitorError:
        return MonitorError(code, message, category=ErrorCategory.INTEGRITY)

    def close(self) -> None:
        self._verifier.close()
