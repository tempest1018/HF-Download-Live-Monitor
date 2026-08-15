import pytest

from hf_live_monitor.models import ManifestFile, MonitorError
from hf_live_monitor.selection import select_manifest

MANIFEST = tuple(
    ManifestFile(name, size)
    for name, size in (
        ("README.md", 10),
        ("config.json", 20),
        ("weights/a.bin", 30),
        ("weights/b.safetensors", 40),
    )
)


def names(files: tuple[ManifestFile, ...]) -> list[str]:
    return [item.filename for item in files]


def test_select_manifest_returns_stably_sorted_full_manifest() -> None:
    assert names(select_manifest(reversed(MANIFEST))) == sorted(names(MANIFEST))


def test_explicit_filenames_form_initial_selection() -> None:
    selected = select_manifest(MANIFEST, filenames=("weights\\a.bin", "config.json"))
    assert names(selected) == ["config.json", "weights/a.bin"]


def test_include_patterns_constrain_selection() -> None:
    selected = select_manifest(MANIFEST, includes=("weights/*",))
    assert names(selected) == ["weights/a.bin", "weights/b.safetensors"]


def test_excludes_always_win() -> None:
    selected = select_manifest(
        MANIFEST,
        includes=("weights/*",),
        excludes=("*.bin",),
    )
    assert names(selected) == ["weights/b.safetensors"]


def test_missing_explicit_filename_is_classified() -> None:
    with pytest.raises(MonitorError) as caught:
        select_manifest(MANIFEST, filenames=("missing.bin",))
    assert caught.value.code == "requested_file_missing"
