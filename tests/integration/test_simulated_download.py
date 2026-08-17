from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import DownloadPlan, DownloadSpec, FileState, ManifestFile
from scripts.run_container_simulation import run_simulation


def _plan(destination: Path, content: bytes) -> DownloadPlan:
    spec = DownloadSpec("local/simulation", destination, filenames=("model.bin",))
    manifest = ManifestFile("model.bin", len(content), hashlib.sha256(content).hexdigest())
    return DownloadPlan(spec, "main", (manifest,))


def test_real_child_is_monitored_until_verified(tmp_path: Path) -> None:
    content = b"verified model content" * 4096

    result = run_simulation(_plan(tmp_path, content), content=content, chunk_size=1024, delay=0.002)

    assert result.exit_code == 0
    assert any(0 < item.downloaded_bytes < len(content) for item in result.snapshots)
    assert result.snapshots[-1].files[0].state is FileState.VERIFIED
    assert result.child_reaped


def test_corrupt_child_finishes_with_integrity_failure(tmp_path: Path) -> None:
    content = b"expected content" * 2048

    result = run_simulation(
        _plan(tmp_path, content),
        content=content,
        chunk_size=512,
        delay=0.001,
        corrupt=True,
    )

    assert result.exit_code == exit_code_for(ErrorCategory.INTEGRITY)
    assert result.snapshots[-1].files[0].state is FileState.FAILED
    assert result.child_reaped


def test_monitor_exception_stops_and_reaps_real_child(tmp_path: Path) -> None:
    content = b"long-running content" * 8192

    with pytest.raises(RuntimeError, match="simulated renderer failure") as caught:
        run_simulation(
            _plan(tmp_path, content),
            content=content,
            chunk_size=128,
            delay=0.01,
            fail_render=True,
        )

    process = caught.value.__dict__["simulation_process"]
    assert process.poll() is not None
