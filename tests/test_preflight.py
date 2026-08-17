import hashlib
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from hf_download_live_monitor import preflight as preflight_module
from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.models import DownloadPlan, DownloadSpec, ManifestFile, MonitorError
from hf_download_live_monitor.preflight import PreflightResult, validate_destination


def _plan(root: Path, *files: tuple[str, int] | tuple[str, int, str]) -> DownloadPlan:
    manifest = tuple(
        ManifestFile(item[0], item[1], item[2] if len(item) == 3 else None) for item in files
    )
    return DownloadPlan(
        DownloadSpec("owner/repo", root, revision="a" * 40),
        "main",
        manifest,
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
    content = b"x" * 1000
    (root / "done.bin").write_bytes(content)
    result = validate_destination(
        _plan(
            root,
            ("done.bin", 1000, hashlib.sha256(content).hexdigest()),
            ("todo.bin", 2000),
        ),
        disk_usage=_usage(2200),
    )
    assert result == PreflightResult(2200, 2200, 200)


def test_equal_size_file_with_wrong_digest_gets_no_credit(tmp_path: Path) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "model.bin").write_bytes(b"wrong")
    expected_digest = hashlib.sha256(b"right").hexdigest()

    result = validate_destination(
        _plan(root, ("model.bin", 5, expected_digest)), disk_usage=_usage(6)
    )

    assert result == PreflightResult(6, 6, 1)


def test_equal_size_file_without_digest_gets_no_credit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "model.bin").write_bytes(b"same")
    monkeypatch.setattr(
        preflight_module,
        "_read_local_metadata",
        lambda root, filename: (False, None),
    )

    result = validate_destination(
        _plan(root, ("model.bin", 4)), reserve_ratio=0, disk_usage=_usage(4)
    )

    assert result == PreflightResult(4, 4, 0)


def test_stale_metadata_blocks_matching_digest_credit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "out"
    root.mkdir()
    content = b"same"
    (root / "model.bin").write_bytes(content)
    monkeypatch.setattr(
        preflight_module,
        "_read_local_metadata",
        lambda root, filename: (True, "b" * 40),
    )

    result = validate_destination(
        _plan(root, ("model.bin", 4, hashlib.sha256(content).hexdigest())),
        reserve_ratio=0,
        disk_usage=_usage(4),
    )

    assert result.required_bytes == 4


def test_matching_resolved_revision_metadata_gets_credit_without_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "model.bin").write_bytes(b"same")
    monkeypatch.setattr(
        preflight_module,
        "_read_local_metadata",
        lambda root, filename: (True, "a" * 40),
    )

    result = validate_destination(
        _plan(root, ("model.bin", 4)), reserve_ratio=0, disk_usage=_usage(0)
    )

    assert result.required_bytes == 0


def test_absent_metadata_allows_matching_digest_credit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "out"
    root.mkdir()
    content = b"same"
    (root / "model.bin").write_bytes(content)
    monkeypatch.setattr(
        preflight_module,
        "_read_local_metadata",
        lambda root, filename: (False, None),
    )

    result = validate_destination(
        _plan(root, ("model.bin", 4, hashlib.sha256(content).hexdigest())),
        reserve_ratio=0,
        disk_usage=_usage(0),
    )

    assert result.required_bytes == 0


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
    assert "required=3300" in caught.value.message
    assert "available=3299" in caught.value.message
    assert f"destination={root.resolve()}" in caught.value.message


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


def test_probe_is_cleaned_up_when_probe_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "out"

    def fail_after_create(self: Path, *args: object, **kwargs: object):
        self.touch()
        raise OSError("token=hf_secret")

    monkeypatch.setattr(Path, "open", fail_after_create)

    with pytest.raises(MonitorError) as caught:
        validate_destination(_plan(root, ("x", 1)), disk_usage=_usage(2))

    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "destination_unwritable"
    assert "hf_secret" not in caught.value.message
    assert list(root.iterdir()) == []


def test_probe_cleanup_failure_is_redacted_destination_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "out"

    def fail_unlink(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("token=hf_secret")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(MonitorError) as caught:
        validate_destination(_plan(root, ("x", 1)), disk_usage=_usage(2))

    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "destination_unwritable"
    assert "hf_secret" not in caught.value.message
    assert "probe cleanup" in caught.value.message


def test_probe_cleanup_failure_does_not_mask_earlier_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "out"

    def fail_unlink(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("token=hf_cleanup_secret")

    def fail_usage(_: Path):
        raise OSError("token=hf_disk_secret")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(MonitorError) as caught:
        validate_destination(_plan(root, ("x", 1)), disk_usage=fail_usage)

    assert caught.value.code == "destination_unwritable"
    assert "hf_disk_secret" not in caught.value.message
    assert "<redacted>" in caught.value.message
    assert isinstance(caught.value.__cause__, MonitorError)
    assert "hf_cleanup_secret" not in str(caught.value.__cause__)


def test_destination_resolution_failure_is_redacted_destination_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _plan(tmp_path / "out", ("x", 1))

    def fail_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        raise OSError("token=hf_secret")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    with pytest.raises(MonitorError) as caught:
        validate_destination(plan, disk_usage=_usage(2))

    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "destination_unwritable"
    assert "hf_secret" not in caught.value.message
    assert "<redacted>" in caught.value.message


def test_unsafe_manifest_path_is_destination_error(tmp_path: Path) -> None:
    with pytest.raises(MonitorError) as caught:
        validate_destination(_plan(tmp_path, ("../escape", 1)), disk_usage=_usage(2))
    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "destination_unwritable"
    assert "escape" not in caught.value.message
