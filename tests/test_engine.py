from pathlib import Path

import pytest

from hf_live_monitor.engine import ProgressEngine
from hf_live_monitor.models import (
    DownloadSpec,
    FileObservation,
    FileState,
    ManifestFile,
)

SPEC = DownloadSpec("owner/repo", Path("out"))
MANIFEST = (ManifestFile("model.bin", 100),)


def observation(
    now: float,
    visible: int,
    *,
    final: int | None = None,
    partial: int | None = None,
) -> tuple[FileObservation, ...]:
    return (FileObservation("model.bin", 100, visible, final, partial, now),)


def test_first_partial_sample_is_measuring_without_resume_speed() -> None:
    snapshot = ProgressEngine().update(SPEC, MANIFEST, observation(1.0, 40, partial=40), now=1.0)
    assert snapshot.files[0].state is FileState.MEASURING
    assert snapshot.files[0].rate_bytes_per_second is None


def test_increasing_partial_calculates_rate_and_eta() -> None:
    engine = ProgressEngine(rate_window=2.0)
    engine.update(SPEC, MANIFEST, observation(1.0, 40, partial=40), now=1.0)
    snapshot = engine.update(SPEC, MANIFEST, observation(2.0, 60, partial=60), now=2.0)
    progress = snapshot.files[0]
    assert progress.state is FileState.DOWNLOADING
    assert progress.rate_bytes_per_second == pytest.approx(20.0)
    assert progress.eta_seconds == pytest.approx(2.0)
    assert snapshot.rate_bytes_per_second == pytest.approx(20.0)


def test_unchanged_partial_becomes_waiting_after_measurement_window() -> None:
    engine = ProgressEngine(measuring_window=0.75)
    engine.update(SPEC, MANIFEST, observation(1.0, 40, partial=40), now=1.0)
    snapshot = engine.update(SPEC, MANIFEST, observation(2.0, 40, partial=40), now=2.0)
    assert snapshot.files[0].state is FileState.WAITING


@pytest.mark.parametrize(
    ("final", "state"),
    [(100, FileState.COMPLETE), (90, FileState.INCONSISTENT), (110, FileState.INCONSISTENT)],
)
def test_final_size_must_match_expected(final: int, state: FileState) -> None:
    snapshot = ProgressEngine().update(
        SPEC, MANIFEST, observation(1.0, final, final=final), now=1.0
    )
    assert snapshot.files[0].state is state


def test_expected_bytes_in_partial_is_finalizing() -> None:
    snapshot = ProgressEngine().update(SPEC, MANIFEST, observation(1.0, 100, partial=100), now=1.0)
    assert snapshot.files[0].state is FileState.FINALIZING


def test_decreasing_bytes_never_reports_negative_rate() -> None:
    engine = ProgressEngine()
    engine.update(SPEC, MANIFEST, observation(1.0, 80, partial=80), now=1.0)
    snapshot = engine.update(SPEC, MANIFEST, observation(2.0, 20, partial=20), now=2.0)
    assert snapshot.files[0].rate_bytes_per_second == 0.0
