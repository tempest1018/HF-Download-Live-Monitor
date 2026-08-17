import hashlib
from pathlib import Path

import pytest

from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.models import (
    DownloadPlan,
    DownloadSpec,
    FileObservation,
    FileState,
    ManifestFile,
)

SPEC = DownloadSpec("owner/repo", Path("out"))
MANIFEST = (ManifestFile("model.bin", 100),)
PLAN = DownloadPlan(SPEC, "requested", MANIFEST)


def observation(
    now: float,
    visible: int,
    *,
    expected: int = 100,
    final: int | None = None,
    partial: int | None = None,
) -> tuple[FileObservation, ...]:
    return (FileObservation("model.bin", expected, visible, final, partial, now),)


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
    [
        (100, FileState.COMPLETE_UNVERIFIED),
        (90, FileState.INCONSISTENT),
        (110, FileState.FAILED),
    ],
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


def test_digest_file_is_not_complete_before_verification(tmp_path: Path) -> None:
    content = b"expected"
    target = tmp_path / "model.bin"
    target.write_bytes(content)
    plan = DownloadPlan(
        DownloadSpec("owner/repo", tmp_path, revision="a" * 40),
        "main",
        (ManifestFile("model.bin", len(content), hashlib.sha256(content).hexdigest()),),
    )

    snapshot = ProgressEngine().update(
        plan,
        observation(1.0, len(content), expected=len(content), final=len(content)),
        now=1.0,
    )

    assert snapshot.files[0].state in {FileState.SIZE_MATCHED, FileState.VERIFYING}
    assert snapshot.files[0].state is not FileState.COMPLETE


def test_final_digest_match_is_verified(tmp_path: Path) -> None:
    content = b"expected"
    (tmp_path / "model.bin").write_bytes(content)
    plan = DownloadPlan(
        DownloadSpec("owner/repo", tmp_path, revision="a" * 40),
        "main",
        (ManifestFile("model.bin", len(content), hashlib.sha256(content).hexdigest()),),
    )

    snapshot = ProgressEngine().update(
        plan,
        observation(1.0, len(content), expected=len(content), final=len(content)),
        now=1.0,
        final=True,
    )

    assert snapshot.files[0].state is FileState.VERIFIED
    assert snapshot.verified_files == 1
    assert snapshot.failed_files == 0
    assert snapshot.requested_revision == "main"
    assert snapshot.resolved_revision == "a" * 40


def test_final_digest_mismatch_is_integrity_failure(tmp_path: Path) -> None:
    content = b"unexpected"
    (tmp_path / "model.bin").write_bytes(content)
    plan = DownloadPlan(
        DownloadSpec("owner/repo", tmp_path),
        "main",
        (ManifestFile("model.bin", len(content), "0" * 64),),
    )

    snapshot = ProgressEngine().update(
        plan,
        observation(1.0, len(content), expected=len(content), final=len(content)),
        now=1.0,
        final=True,
    )

    assert snapshot.files[0].state is FileState.FAILED
    assert snapshot.failed_files == 1
    assert snapshot.errors[0].category.value == "integrity"


def test_exact_file_without_digest_is_complete_unverified(tmp_path: Path) -> None:
    (tmp_path / "model.bin").write_bytes(b"x")
    plan = DownloadPlan(
        DownloadSpec("owner/repo", tmp_path), "main", (ManifestFile("model.bin", 1),)
    )

    snapshot = ProgressEngine().update(
        plan, observation(1.0, 1, expected=1, final=1), now=1.0, final=True
    )

    assert snapshot.files[0].state is FileState.COMPLETE_UNVERIFIED
    assert snapshot.complete_unverified_files == 1


def test_oversized_file_is_failed_and_downloaded_bytes_are_capped(tmp_path: Path) -> None:
    plan = DownloadPlan(
        DownloadSpec("owner/repo", tmp_path), "main", (ManifestFile("model.bin", 100),)
    )

    snapshot = ProgressEngine().update(plan, observation(1.0, 110, final=110), now=1.0, final=True)

    assert snapshot.files[0].state is FileState.FAILED
    assert snapshot.downloaded_bytes == 100
    assert snapshot.failed_files == 1
    assert snapshot.errors[0].category.value == "integrity"


def test_rate_history_is_nonnegative_and_bounded() -> None:
    engine = ProgressEngine()
    for index in range(30):
        snapshot = engine.update(
            PLAN,
            observation(float(index), index % 2, partial=index % 2),
            now=float(index),
        )

    assert len(snapshot.rate_history) == 24
    assert all(sample >= 0 for sample in snapshot.rate_history)


def test_engine_close_is_idempotent() -> None:
    engine = ProgressEngine()
    engine.close()
    engine.close()
