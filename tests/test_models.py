from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hf_download_live_monitor.models import DownloadSpec, FileObservation, RepoType


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
