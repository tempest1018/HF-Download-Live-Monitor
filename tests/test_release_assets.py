from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from scripts import build_standalone


def test_artifact_naming_can_be_imported_without_pyinstaller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "PyInstaller", None)
    monkeypatch.setitem(sys.modules, "PyInstaller.__main__", None)
    spec = importlib.util.spec_from_file_location(
        "build_standalone_without_pyinstaller", Path("scripts/build_standalone.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.artifact_name("Linux", "aarch64").endswith("linux-arm64")


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("AMD64", "x86_64"),
        ("amd64", "x86_64"),
        ("x86_64", "x86_64"),
        ("X64", "x86_64"),
        ("ARM64", "arm64"),
        ("arm64", "arm64"),
        ("aarch64", "arm64"),
        ("AARCH64", "arm64"),
    ],
)
def test_normalized_architecture(machine: str, expected: str) -> None:
    assert build_standalone.normalized_architecture(machine) == expected


@pytest.mark.parametrize("machine", ["", "i686", "riscv64"])
def test_normalized_architecture_rejects_unsupported_values(machine: str) -> None:
    with pytest.raises(ValueError, match="unsupported architecture"):
        build_standalone.normalized_architecture(machine)


@pytest.mark.parametrize(
    ("system", "expected"),
    [("Windows", "windows"), ("Linux", "linux"), ("Darwin", "macos")],
)
def test_normalized_system(system: str, expected: str) -> None:
    assert build_standalone.normalized_system(system) == expected


def test_normalized_system_rejects_unsupported_values() -> None:
    with pytest.raises(ValueError, match="unsupported system"):
        build_standalone.normalized_system("FreeBSD")


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", "hf-download-live-monitor-windows-x86_64.exe"),
        ("Windows", "ARM64", "hf-download-live-monitor-windows-arm64.exe"),
        ("Linux", "x86_64", "hf-download-live-monitor-linux-x86_64"),
        ("Linux", "aarch64", "hf-download-live-monitor-linux-arm64"),
        ("Darwin", "x64", "hf-download-live-monitor-macos-x86_64"),
        ("Darwin", "arm64", "hf-download-live-monitor-macos-arm64"),
    ],
)
def test_artifact_name(system: str, machine: str, expected: str) -> None:
    assert build_standalone.artifact_name(system, machine) == expected


def test_builder_relabels_default_output_and_streams_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_standalone.platform, "system", lambda: "Linux")
    monkeypatch.setattr(build_standalone.platform, "machine", lambda: "aarch64")
    payload = b"standalone executable\n" * 100

    def fake_build(_arguments: list[str]) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "hf-download-live-monitor").write_bytes(payload)

    monkeypatch.setattr(build_standalone, "run_pyinstaller", fake_build)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _self: pytest.fail("checksums must stream instead of calling read_bytes"),
    )

    assert build_standalone.main() == 0
    artifact = tmp_path / "dist" / "hf-download-live-monitor-linux-arm64"
    assert artifact.read_text(encoding="ascii").startswith("standalone executable")
    assert not (tmp_path / "dist" / "hf-download-live-monitor").exists()
    assert artifact.with_name(f"{artifact.name}.sha256").read_text(encoding="ascii") == (
        f"{hashlib.sha256(payload).hexdigest()}  {artifact.name}\n"
    )


def test_builder_removes_stale_labelled_outputs_when_build_output_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_standalone.platform, "system", lambda: "Windows")
    monkeypatch.setattr(build_standalone.platform, "machine", lambda: "AMD64")
    dist = tmp_path / "dist"
    dist.mkdir()
    artifact = dist / "hf-download-live-monitor-windows-x86_64.exe"
    artifact.write_text("stale", encoding="ascii")
    checksum = artifact.with_name(f"{artifact.name}.sha256")
    checksum.write_text("stale", encoding="ascii")
    monkeypatch.setattr(build_standalone, "run_pyinstaller", lambda _arguments: None)

    with pytest.raises(FileNotFoundError):
        build_standalone.main()

    assert not artifact.exists()
    assert not checksum.exists()


def _workflow(name: str) -> dict[str, object]:
    with Path(f".github/workflows/{name}.yml").open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    assert isinstance(loaded, dict)
    return loaded


def test_ci_keeps_x64_matrix_and_adds_native_arm64_validation() -> None:
    jobs = _workflow("ci")["jobs"]
    assert isinstance(jobs, dict)
    assert jobs["test"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "windows-latest"],
        "python": ["3.10", "3.11", "3.12", "3.13"],
    }
    arm_job = jobs["arm64-native"]
    assert arm_job["strategy"]["fail-fast"] is False
    assert arm_job["strategy"]["matrix"]["include"] == [
        {"runner": "ubuntu-24.04-arm", "os": "linux", "arch": "arm64"},
        {"runner": "windows-11-arm", "os": "windows", "arch": "arm64"},
        {"runner": "macos-15", "os": "macos", "arch": "arm64"},
    ]
    assert arm_job["runs-on"] == "${{ matrix.runner }}"
    rendered = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "normalized_architecture(platform.machine()) == 'arm64'" in rendered
    assert 'python-version: "3.13"' in rendered
    assert "tests/integration/test_simulated_download.py" in rendered


def test_release_workflow_has_exact_native_standalone_matrix_and_unique_assets() -> None:
    jobs = _workflow("release")["jobs"]
    assert isinstance(jobs, dict)
    standalone = jobs["standalone"]
    assert standalone["strategy"]["fail-fast"] is False
    assert standalone["strategy"]["matrix"]["include"] == [
        {
            "runner": "windows-latest",
            "os": "windows",
            "arch": "x86_64",
            "python_arch": "x64",
            "artifact": "hf-download-live-monitor-windows-x86_64.exe",
        },
        {
            "runner": "windows-11-arm",
            "os": "windows",
            "arch": "arm64",
            "python_arch": "arm64",
            "artifact": "hf-download-live-monitor-windows-arm64.exe",
        },
        {
            "runner": "ubuntu-latest",
            "os": "linux",
            "arch": "x86_64",
            "python_arch": "x64",
            "artifact": "hf-download-live-monitor-linux-x86_64",
        },
        {
            "runner": "ubuntu-24.04-arm",
            "os": "linux",
            "arch": "arm64",
            "python_arch": "arm64",
            "artifact": "hf-download-live-monitor-linux-arm64",
        },
        {
            "runner": "macos-15-intel",
            "os": "macos",
            "arch": "x86_64",
            "python_arch": "x64",
            "artifact": "hf-download-live-monitor-macos-x86_64",
        },
        {
            "runner": "macos-15",
            "os": "macos",
            "arch": "arm64",
            "python_arch": "arm64",
            "artifact": "hf-download-live-monitor-macos-arm64",
        },
    ]
    assert standalone["runs-on"] == "${{ matrix.runner }}"
    rendered = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "architecture: ${{ matrix.python_arch }}" in rendered
    assert "dist/${{ matrix.artifact }}" in rendered
    assert "name: standalone-${{ matrix.os }}-${{ matrix.arch }}" in rendered
    assert "! -name '*.sha256'" in rendered
    assert "! -name 'SHA256SUMS'" in rendered
    assert "python-distributions" in rendered
    assert "merge-multiple: true" in rendered
    assert "pypa/gh-action-pypi-publish@release/v1" in rendered


def test_standalone_spec_and_builder_have_no_local_paths() -> None:
    spec = Path("hf_download_live_monitor.spec").read_text(encoding="utf-8")
    builder = Path("scripts/build_standalone.py").read_text(encoding="utf-8")
    assert "hf_download_live_monitor.__main__" in spec
    assert 'name="hf-download-live-monitor"' in spec
    assert "PyInstaller" in builder
    assert "C:\\Users" not in spec + builder
