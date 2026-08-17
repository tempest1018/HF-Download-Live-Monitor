import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.models import DownloadPlan, DownloadSpec, ManifestFile, MonitorError
from hf_download_live_monitor.preflight import PreflightResult, validate_destination


def _plan(root: Path, *files: tuple[str, int]) -> DownloadPlan:
    return DownloadPlan(
        DownloadSpec("owner/repo", root, revision="a" * 40),
        "main",
        tuple(ManifestFile(name, size) for name, size in files),
    )


def _usage(free: int):
    return lambda _: SimpleNamespace(total=10_000, used=10_000 - free, free=free)


def test_result_is_frozen_and_slotted() -> None:
    result = PreflightResult(1, 2, 3)
    with pytest.raises((AttributeError, TypeError)):
        result.required_bytes = 4  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


def test_requires_remaining_bytes_plus_rounded_up_reserve(tmp_path: Path) -> None:
    result = validate_destination(
        _plan(tmp_path / "out", ("model.bin", 3000)), disk_usage=_usage(3300)
    )
    assert result == PreflightResult(required_bytes=3300, available_bytes=3300, reserve_bytes=300)


def test_exact_existing_file_gets_full_credit(tmp_path: Path) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "done.bin").write_bytes(b"x" * 1000)
    result = validate_destination(
        _plan(root, ("done.bin", 1000), ("todo.bin", 2000)), disk_usage=_usage(2200)
    )
    assert result == PreflightResult(2200, 2200, 200)


@pytest.mark.parametrize("actual", [999, 1001])
def test_nonexact_existing_file_gets_no_credit(tmp_path: Path, actual: int) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "model.bin").write_bytes(b"x" * actual)
    result = validate_destination(_plan(root, ("model.bin", 1000)), disk_usage=_usage(1100))
    assert result.required_bytes == 1100


def test_zero_remaining_needs_no_reserve(tmp_path: Path) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "empty").touch()
    result = validate_destination(_plan(root, ("empty", 0)), disk_usage=_usage(0))
    assert result == PreflightResult(0, 0, 0)


@pytest.mark.parametrize("ratio", [-0.01, math.inf, -math.inf, math.nan])
def test_rejects_invalid_reserve_ratio(tmp_path: Path, ratio: float) -> None:
    with pytest.raises(ValueError, match="reserve ratio"):
        validate_destination(_plan(tmp_path, ("x", 1)), reserve_ratio=ratio)


def test_insufficient_space_is_destination_error(tmp_path: Path) -> None:
    root = tmp_path / "out"
    with pytest.raises(MonitorError) as caught:
        validate_destination(_plan(root, ("model.bin", 3000)), disk_usage=_usage(3299))
    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "insufficient_disk_space"
    assert "3300" in caught.value.message
    assert "3299" in caught.value.message
    assert str(root.resolve()) in caught.value.message


def test_create_failure_is_redacted_destination_error(tmp_path: Path) -> None:
    blocker = tmp_path / "secret-token=hf_abc"
    blocker.write_text("not a directory")
    with pytest.raises(MonitorError) as caught:
        validate_destination(_plan(blocker / "out", ("x", 1)), disk_usage=_usage(2))
    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "destination_unwritable"
    assert "hf_abc" not in caught.value.message


def test_probe_is_cleaned_up_when_disk_usage_fails(tmp_path: Path) -> None:
    root = tmp_path / "out"

    def fail(_: Path):
        raise OSError("token=hf_secret")

    with pytest.raises(MonitorError) as caught:
        validate_destination(_plan(root, ("x", 1)), disk_usage=fail)
    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "destination_unwritable"
    assert "hf_secret" not in caught.value.message
    assert list(root.iterdir()) == []


def test_unsafe_manifest_path_is_destination_error(tmp_path: Path) -> None:
    with pytest.raises(MonitorError) as caught:
        validate_destination(_plan(tmp_path, ("../escape", 1)), disk_usage=_usage(2))
    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "destination_unwritable"
    assert "escape" not in caught.value.message
