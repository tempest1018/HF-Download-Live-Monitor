import json
from pathlib import Path

from hf_download_live_monitor.history_models import HistoryCheckpoint, HistoryOutcome, HistoryRecord
from hf_download_live_monitor.history_renderers import history_record_to_dict, records_to_jsonl
from hf_download_live_monitor.models import RepoType


def record_with_identifiers() -> HistoryRecord:
    checkpoint = HistoryCheckpoint.start(
        session_id="session-1",
        mode="watch",
        repo_type=RepoType.MODEL,
        repository_hmac="a" * 64,
        destination_hmac="b" * 64,
        repository_label="repository-aaaaaaaa",
        destination_label="destination-bbbbbbbb",
        repository="private/repo",
        local_dir=Path("C:/Private/Model"),
        include_identifiers=True,
        observed_at_utc=100.0,
    ).finish(HistoryOutcome.COMPLETED, 120.0)
    return HistoryRecord(checkpoint)


def test_sanitized_record_omits_identifiers_and_hashes() -> None:
    payload = history_record_to_dict(record_with_identifiers())
    encoded = json.dumps(payload)
    assert payload["schema_version"] == 1
    assert payload["kind"] == "history_record"
    assert "private/repo" not in encoded
    assert "C:/Private" not in encoded
    assert "a" * 64 not in encoded


def test_identifier_output_requires_explicit_argument() -> None:
    payload = history_record_to_dict(record_with_identifiers(), include_identifiers=True)
    assert payload["repository"]["identifier"] == "private/repo"  # type: ignore[index]


def test_jsonl_is_one_record_per_line() -> None:
    output = records_to_jsonl((record_with_identifiers(), record_with_identifiers()))
    assert len(output.splitlines()) == 2
    assert all(json.loads(line)["kind"] == "history_record" for line in output.splitlines())
