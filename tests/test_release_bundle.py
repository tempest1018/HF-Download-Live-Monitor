from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.validate_release_bundle import (
    EXPECTED_STANDALONES,
    ReleaseValidationError,
    parse_stable_tag,
    project_version,
    sdist_version,
    validate_bundle,
    wheel_version,
    write_aggregate_checksums,
)


def _metadata() -> bytes:
    return b"Metadata-Version: 2.4\nName: hf-download-live-monitor\nVersion: 0.1.0\n"


def _wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hf_download_live_monitor-0.1.0.dist-info/METADATA", _metadata())


def _sdist(path: Path) -> None:
    with tarfile.open(path, "w:gz") as archive:
        payload = _metadata()
        info = tarfile.TarInfo("hf_download_live_monitor-0.1.0/PKG-INFO")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _complete_bundle(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "hf-download-live-monitor"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in EXPECTED_STANDALONES:
        artifact = assets / name
        artifact.write_bytes(name.encode())
        (assets / f"{name}.sha256").write_text(f"{_hash(artifact)}  {name}\n", encoding="ascii")
    _wheel(assets / "hf_download_live_monitor-0.1.0-py3-none-any.whl")
    _sdist(assets / "hf_download_live_monitor-0.1.0.tar.gz")
    write_aggregate_checksums(assets)
    return assets, project


def test_stable_tag_and_project_version_are_strict(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
    assert parse_stable_tag("v0.1.0") == "0.1.0"
    assert project_version(project) == "0.1.0"
    for invalid in ("0.1.0", "v0.1", "v0.1.0-rc.1", "v01.1.0"):
        with pytest.raises(ReleaseValidationError, match="stable tag"):
            parse_stable_tag(invalid)


def test_package_metadata_versions_are_read_from_archives(tmp_path: Path) -> None:
    wheel = tmp_path / "hf_download_live_monitor-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "hf_download_live_monitor-0.1.0.tar.gz"
    _wheel(wheel)
    _sdist(sdist)
    assert wheel_version(wheel) == "0.1.0"
    assert sdist_version(sdist) == "0.1.0"


def test_complete_bundle_validates_and_uses_flat_checksums(tmp_path: Path) -> None:
    assets, project = _complete_bundle(tmp_path)
    result = validate_bundle(assets, tag="v0.1.0", project_file=project)
    assert result.wheel.name == "hf_download_live_monitor-0.1.0-py3-none-any.whl"
    assert result.sdist.name == "hf_download_live_monitor-0.1.0.tar.gz"
    lines = (assets / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert lines == sorted(lines, key=lambda line: line.split("  ", 1)[1])
    assert all("/" not in line.split("  ", 1)[1] for line in lines)
    assert all(not line.endswith(".sha256") for line in lines)


@pytest.mark.parametrize("missing", [EXPECTED_STANDALONES[0], "SHA256SUMS"])
def test_bundle_rejects_missing_required_files(tmp_path: Path, missing: str) -> None:
    assets, project = _complete_bundle(tmp_path)
    (assets / missing).unlink()
    with pytest.raises(ReleaseValidationError):
        validate_bundle(assets, tag="v0.1.0", project_file=project)


def test_bundle_rejects_extra_nested_and_mismatched_files(tmp_path: Path) -> None:
    assets, project = _complete_bundle(tmp_path)
    (assets / "unexpected.exe").write_bytes(b"unexpected")
    with pytest.raises(ReleaseValidationError, match="unexpected"):
        validate_bundle(assets, tag="v0.1.0", project_file=project)
    (assets / "unexpected.exe").unlink()
    nested = assets / "nested"
    nested.mkdir()
    (nested / "payload").write_bytes(b"nested")
    with pytest.raises(ReleaseValidationError, match="flat"):
        validate_bundle(assets, tag="v0.1.0", project_file=project)
    (nested / "payload").unlink()
    nested.rmdir()
    with pytest.raises(ReleaseValidationError, match="version"):
        validate_bundle(assets, tag="v0.1.1", project_file=project)


def test_bundle_rejects_checksum_traversal_and_hash_mismatch(tmp_path: Path) -> None:
    assets, project = _complete_bundle(tmp_path)
    checksum = assets / f"{EXPECTED_STANDALONES[0]}.sha256"
    checksum.write_text(f"{'0' * 64}  ../escape\n", encoding="ascii")
    with pytest.raises(ReleaseValidationError, match="checksum"):
        validate_bundle(assets, tag="v0.1.0", project_file=project)
    artifact = assets / EXPECTED_STANDALONES[0]
    checksum.write_text(f"{'0' * 64}  {artifact.name}\n", encoding="ascii")
    with pytest.raises(ReleaseValidationError, match="mismatch"):
        validate_bundle(assets, tag="v0.1.0", project_file=project)


def test_bundle_rejects_aggregate_with_duplicate_name(tmp_path: Path) -> None:
    assets, project = _complete_bundle(tmp_path)
    manifest = assets / "SHA256SUMS"
    first = manifest.read_text(encoding="ascii").splitlines()[0]
    manifest.write_text(f"{first}\n{first}\n", encoding="ascii")
    with pytest.raises(ReleaseValidationError, match=r"duplicate|inventory"):
        validate_bundle(assets, tag="v0.1.0", project_file=project)
