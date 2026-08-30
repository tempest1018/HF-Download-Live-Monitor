from pathlib import Path

import pytest

from hf_download_live_monitor.history_models import (
    DEFAULT_HISTORY_MAX_BYTES,
    HistoryCheckpoint,
    HistoryConfig,
    HistoryOutcome,
    HistoryQuery,
)
from hf_download_live_monitor.models import RepoType


def test_history_defaults_are_privacy_first() -> None:
    config = HistoryConfig.defaults()
    assert config.enabled is False
    assert config.retention_days == 30
    assert config.max_size_bytes == 64 * 1024 * 1024 == DEFAULT_HISTORY_MAX_BYTES
    assert config.include_identifiers is False


@pytest.mark.parametrize("retention", [0, -1])
def test_history_config_rejects_nonpositive_retention(retention: int) -> None:
    with pytest.raises(ValueError, match="retention"):
        HistoryConfig(retention_days=retention)


def test_history_config_allows_explicit_unlimited_retention() -> None:
    assert HistoryConfig(retention_days=None).retention_days is None


def test_checkpoint_rejects_direct_identifiers_without_opt_in() -> None:
    with pytest.raises(ValueError, match="identifier opt-in"):
        HistoryCheckpoint.start(
            session_id="session-1",
            mode="watch",
            repo_type=RepoType.MODEL,
            repository_hmac="a" * 64,
            destination_hmac="b" * 64,
            repository_label="repository-aaaaaaaa",
            destination_label="destination-bbbbbbbb",
            repository="private/repo",
            local_dir=Path("C:/private/model"),
            include_identifiers=False,
            observed_at_utc=1_800_000_000.0,
        )


def test_history_query_is_bounded() -> None:
    assert HistoryQuery(limit=1_000).limit == 1_000
    with pytest.raises(ValueError, match="limit"):
        HistoryQuery(limit=1_001)


def test_history_outcome_vocabulary_is_stable() -> None:
    assert {item.value for item in HistoryOutcome} == {
        "completed",
        "failed",
        "lost",
        "cancelled",
        "interrupted",
    }
