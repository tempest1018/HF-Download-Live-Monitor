from pathlib import Path


def test_standalone_spec_and_builder_exist_without_local_paths() -> None:
    spec = Path("hf_live_monitor.spec").read_text(encoding="utf-8")
    builder = Path("scripts/build_standalone.py").read_text(encoding="utf-8")
    assert "hf_live_monitor.__main__" in spec
    assert "PyInstaller" in builder
    assert "C:\\Users" not in spec + builder


def test_release_workflow_uses_oidc_checksums_and_all_platforms() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish" in workflow
    assert "windows-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "macos-latest" in workflow
    assert "SHA256" in workflow or "sha256" in workflow
    assert "python -m pytest" in workflow
