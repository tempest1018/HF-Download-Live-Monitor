from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import build_standalone


def _command(name: str, git_subdirectory: str) -> str | None:
    discovered = shutil.which(name)
    bundled = Path("C:/Program Files/Git") / git_subdirectory / f"{name}.exe"
    if discovered is not None:
        return discovered
    return str(bundled) if bundled.is_file() else None


_BASH = _command("bash", "bin")
_SHA256SUM = _command("sha256sum", "usr/bin")


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

    def fake_build(arguments: list[str]) -> None:
        output = Path(arguments[arguments.index("--distpath") + 1])
        output.mkdir(parents=True)
        (output / "hf-download-live-monitor").write_bytes(payload)

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


def test_builder_preserves_python_distributions_in_shared_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_standalone.platform, "system", lambda: "Linux")
    monkeypatch.setattr(build_standalone.platform, "machine", lambda: "x86_64")
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "hf_download_live_monitor-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")

    def fake_build(arguments: list[str]) -> None:
        output = Path("dist")
        if "--distpath" in arguments:
            output = Path(arguments[arguments.index("--distpath") + 1])
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True)
        (output / "hf-download-live-monitor").write_bytes(b"standalone")

    monkeypatch.setattr(build_standalone, "run_pyinstaller", fake_build)

    assert build_standalone.main() == 0
    assert wheel.read_bytes() == b"wheel"


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


def _workflow_triggers(workflow: dict[str, object]) -> dict[str, object]:
    # PyYAML implements YAML 1.1, where the unquoted GitHub key `on` resolves to True.
    assert True in workflow
    triggers = workflow[True]
    assert isinstance(triggers, dict)
    return triggers


def test_ci_keeps_x64_matrix_and_adds_native_arm64_validation() -> None:
    workflow = _workflow("ci")
    assert set(_workflow_triggers(workflow)) == {"push", "pull_request", "workflow_dispatch"}
    assert _workflow_triggers(workflow)["push"] == {"branches": ["main"]}
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert jobs["test"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "windows-latest"],
        "python": ["3.10", "3.11", "3.12", "3.13"],
    }
    linux_arm_job = jobs["linux-arm64"]
    assert linux_arm_job["runs-on"] == "ubuntu-24.04-arm"
    assert "if" not in linux_arm_job
    expensive_arm_job = jobs["windows-macos-arm64"]
    assert expensive_arm_job["strategy"]["fail-fast"] is False
    assert expensive_arm_job["strategy"]["matrix"]["include"] == [
        {"runner": "windows-11-arm", "os": "windows", "arch": "arm64"},
        {"runner": "macos-15", "os": "macos", "arch": "arm64"},
    ]
    assert expensive_arm_job["runs-on"] == "${{ matrix.runner }}"
    condition = expensive_arm_job["if"]
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/main'" in condition
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert jobs["package"]["needs"] == ["quality", "test", "macos-smoke", "linux-arm64"]
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
    assert "python-distributions" in rendered
    assert "merge-multiple: true" in rendered
    assert rendered.count("actions/attest-build-provenance@v3") == 2


def test_tag_release_only_stages_a_signed_validated_draft() -> None:
    workflow = _workflow("release")
    assert _workflow_triggers(workflow) == {"push": {"tags": ["v*"]}}
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    validation = jobs["validate-release-assets"]
    assert validation["needs"] == ["verify-and-build-python", "standalone"]
    assert jobs["stage-github-release"]["needs"] == ["validate-release-assets"]
    assert "publish-pypi" not in jobs
    assert "finalize-github-release" not in jobs
    rendered = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in rendered
    assert "gpg --batch --import SIGNING_KEY.asc" in rendered
    assert 'git verify-tag "$GITHUB_REF_NAME"' in rendered
    assert "scripts/validate_release_bundle.py release-assets" in rendered
    assert "--write-aggregate" in rendered
    assert "attestations: write" in rendered
    assert "id-token: write" in rendered
    for name in [
        "hf-download-live-monitor-windows-x86_64.exe",
        "hf-download-live-monitor-windows-arm64.exe",
        "hf-download-live-monitor-linux-x86_64",
        "hf-download-live-monitor-linux-arm64",
        "hf-download-live-monitor-macos-x86_64",
        "hf-download-live-monitor-macos-arm64",
    ]:
        assert name in rendered
    assert 'gh release create "$GITHUB_REF_NAME" --draft' in rendered
    assert "--generate-notes" in rendered
    assert "find release-assets -type f -print0" in rendered
    assert "--json isDraft" in rendered
    assert "--clobber" in rendered
    assert 'if [[ "$release_state" == "false" ]]; then' in rendered
    assert "exit 1" in rendered
    assert "--draft=false" not in rendered
    assert "pypa/gh-action-pypi-publish" not in rendered


def test_github_publication_is_manual_validated_and_build_free() -> None:
    workflow = _workflow("publish-github-release")
    triggers = _workflow_triggers(workflow)
    assert set(triggers) == {"workflow_dispatch"}
    tag = triggers["workflow_dispatch"]["inputs"]["tag"]
    assert tag["required"] is True
    assert tag["type"] == "string"
    jobs = workflow["jobs"]
    assert list(jobs) == ["publish"]
    publish = jobs["publish"]
    assert publish["environment"] == "github-release"
    assert publish["permissions"] == {"contents": "write"}
    rendered = Path(".github/workflows/publish-github-release.yml").read_text(encoding="utf-8")
    for required in (
        "fetch-depth: 0",
        "SIGNING_KEY.asc",
        "git verify-tag",
        "gh release download",
        "scripts/validate_release_bundle.py",
        "--json isDraft,isPrerelease,tagName",
        '--draft=false --prerelease=false --latest',
    ):
        assert required in rendered
    for forbidden in (
        "python -m build",
        "PyInstaller",
        "gh release upload",
        "--clobber",
        "pypa/gh-action-pypi-publish",
    ):
        assert forbidden not in rendered


def test_pypi_promotion_is_manual_oidc_only_and_build_free() -> None:
    workflow = _workflow("publish-pypi")
    triggers = _workflow_triggers(workflow)
    assert set(triggers) == {"workflow_dispatch"}
    assert triggers["workflow_dispatch"]["inputs"]["tag"]["required"] is True
    promote = workflow["jobs"]["promote"]
    assert promote["environment"] == "pypi"
    assert promote["permissions"] == {"contents": "read", "id-token": "write"}
    rendered = Path(".github/workflows/publish-pypi.yml").read_text(encoding="utf-8")
    for required in (
        "git verify-tag",
        "gh release download",
        "scripts/validate_release_bundle.py",
        "--json isDraft,isPrerelease,tagName",
        "pypa/gh-action-pypi-publish@release/v1",
        "packages-dir: python-distributions/",
    ):
        assert required in rendered
    for forbidden in (
        "python -m build",
        "PyInstaller",
        "gh release edit",
        "gh release upload",
        "contents: write",
        "skip-existing",
    ):
        assert forbidden not in rendered


@pytest.mark.skipif(_BASH is None or _SHA256SUM is None, reason="bash or sha256sum is unavailable")
def test_relative_aggregate_checksum_is_verifiable_when_assets_are_colocated(
    tmp_path: Path,
) -> None:
    assets = tmp_path / "release-assets"
    assets.mkdir()
    (assets / "program").write_bytes(b"executable")
    (assets / "program.sha256").write_text("per-file checksum", encoding="ascii")
    subprocess.run(
        [
            _BASH,
            "-c",
            "cd release-assets && "
            "find . -type f ! -name '*.sha256' ! -name 'SHA256SUMS' -print0 | "
            "sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS",
        ],
        cwd=tmp_path,
        check=True,
    )
    checksum = (assets / "SHA256SUMS").read_text(encoding="ascii")
    assert "release-assets" not in checksum
    assert "program.sha256" not in checksum


def test_standalone_spec_and_builder_have_no_local_paths() -> None:
    spec = Path("hf_download_live_monitor.spec").read_text(encoding="utf-8")
    builder = Path("scripts/build_standalone.py").read_text(encoding="utf-8")
    assert "hf_download_live_monitor.__main__" in spec
    assert 'name="hf-download-live-monitor"' in spec
    assert "PyInstaller" in builder
    assert "C:\\Users" not in spec + builder
