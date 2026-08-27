from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import DownloadPlan, DownloadSpec, FileState, ManifestFile
from scripts.run_container_simulation import run_simulation


def test_dockerignore_covers_private_and_machine_local_state() -> None:
    root = Path(__file__).parents[2]
    patterns = {
        line.strip()
        for line in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required = {
        ".cache/",
        "**/.cache/",
        ".huggingface/",
        "**/.huggingface/",
        "hf_home/",
        "**/hf_home/",
        "downloads/",
        "**/downloads/",
        "models/",
        "**/models/",
        "data/",
        "**/data/",
        ".env*",
        "**/.env*",
        "*.token",
        "**/*.token",
        "*.key",
        "**/*.key",
        "*.pem",
        "**/*.pem",
        "**/*auth*",
        "**/*credential*",
        "**/*secret*",
    }

    assert required <= patterns


def test_dockerfile_caches_dependencies_before_copying_source() -> None:
    dockerfile = (Path(__file__).parents[2] / "Dockerfile.test").read_text(encoding="utf-8")

    dependency_install = dockerfile.index("# Dependency ranges mirror pyproject.toml")
    source_copy = dockerfile.index("COPY src/ src/")
    project_install = dockerfile.index("--no-deps -e .")

    assert dependency_install < source_copy < project_install
    assert "--no-build-isolation" not in dockerfile
    assert "test_multi_download_supervisor.py" in dockerfile


def _plan(destination: Path, content: bytes) -> DownloadPlan:
    spec = DownloadSpec("local/simulation", destination, filenames=("model.bin",))
    manifest = ManifestFile("model.bin", len(content), hashlib.sha256(content).hexdigest())
    return DownloadPlan(spec, "main", (manifest,))


def test_real_child_is_monitored_until_verified(tmp_path: Path) -> None:
    content = b"verified model content" * 4096

    result = run_simulation(_plan(tmp_path, content), content=content, chunk_size=1024, delay=0.002)

    assert result.exit_code == 0
    assert result.handshake_acknowledged
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


def test_child_handshake_times_out_without_continue_marker(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"handshake content")
    ready = tmp_path / "ready"
    fixture = Path(__file__).parents[1] / "fixtures" / "incremental_downloader.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(fixture),
            str(tmp_path / "destination"),
            "model.bin",
            str(source),
            "--chunk-size",
            "4",
            "--delay",
            "0",
            "--ready-marker",
            str(ready),
            "--continue-marker",
            str(tmp_path / "continue"),
            "--handshake-timeout",
            "0.05",
        ],
        check=False,
        timeout=5,
    )

    assert ready.is_file()
    assert completed.returncode != 0
    assert not (tmp_path / "destination" / "model.bin").exists()
