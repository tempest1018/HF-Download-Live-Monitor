from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.models import (
    DownloadSpec,
    FileIdentity,
    FileObservation,
    MonitorError,
    RepoType,
)


def test_file_identity_is_frozen_and_rejects_negative_values() -> None:
    identity = FileIdentity(1, 2)
    with pytest.raises(FrozenInstanceError):
        identity.size = 2  # type: ignore[misc]
    with pytest.raises(ValueError, match="non-negative"):
        FileIdentity(-1, 0)
    with pytest.raises(ValueError, match="non-negative"):
        FileIdentity(0, -1)


def test_file_observation_identity_defaults_to_none() -> None:
    observation = FileObservation("weights.bin", 1, 0, None, None, 1.0)
    assert observation.identity is None


def test_repo_type_parses_case_insensitively() -> None:
    assert RepoType.parse("DATASET") is RepoType.DATASET


def test_repo_type_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="repository type"):
        RepoType.parse("collection")


def test_download_spec_rejects_empty_repository() -> None:
    with pytest.raises(ValueError, match="repository"):
        DownloadSpec(repo=" ", local_dir=Path("downloads"))


def test_file_observation_is_immutable() -> None:
    observation = FileObservation(
        filename="weights.bin",
        expected_bytes=100,
        visible_bytes=50,
        final_bytes=None,
        partial_bytes=50,
        observed_at=1.0,
    )

    with pytest.raises(FrozenInstanceError):
        observation.visible_bytes = 75  # type: ignore[misc]


def test_file_observation_rejects_negative_bytes() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        FileObservation(
            filename="weights.bin",
            expected_bytes=100,
            visible_bytes=-1,
            final_bytes=None,
            partial_bytes=None,
            observed_at=1.0,
        )


def test_monitor_error_serializes_safe_public_fields() -> None:
    error = MonitorError(
        "token_invalid",
        "authentication failed",
        category=ErrorCategory.ACCESS,
    )

    assert error.to_dict() == {
        "category": "access",
        "code": "token_invalid",
        "message": "authentication failed",
        "recoverable": False,
    }


def test_monitor_error_preserves_positional_recoverable_and_string_behavior() -> None:
    error = MonitorError("temporary", "try again", True)

    assert error.recoverable is True
    assert error.category is ErrorCategory.MONITOR
    assert str(error) == "try again"


@pytest.mark.parametrize(
    ("code", "message", "match"),
    [
        (" ", "valid", "error code"),
        ("valid", " ", "error message"),
    ],
)
def test_monitor_error_preserves_validation(code: str, message: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        MonitorError(code, message)
