"""Sanitized human and structured output for local history."""

from __future__ import annotations

import json
from typing import Any

from rich.table import Table

from hf_download_live_monitor.history_models import HistoryRecord
from hf_download_live_monitor.security import sanitize_persisted_diagnostic


def history_record_to_dict(
    record: HistoryRecord, *, include_identifiers: bool = False
) -> dict[str, Any]:
    checkpoint = record.checkpoint
    repository: dict[str, object] = {"label": checkpoint.repository_label}
    destination: dict[str, object] = {"label": checkpoint.destination_label}
    if include_identifiers:
        if checkpoint.repository_identifier is not None:
            repository["identifier"] = checkpoint.repository_identifier
        if checkpoint.destination_identifier is not None:
            destination["identifier"] = checkpoint.destination_identifier
    return {
        "schema_version": 1,
        "kind": "history_record",
        "session_id": checkpoint.session_id,
        "mode": checkpoint.mode,
        "repository": repository,
        "destination": destination,
        "repository_type": checkpoint.repo_type.value,
        "outcome": None if checkpoint.outcome is None else checkpoint.outcome.value,
        "timing": {
            "started_at_utc": checkpoint.started_at_utc,
            "updated_at_utc": checkpoint.updated_at_utc,
            "ended_at_utc": checkpoint.ended_at_utc,
            "waiting_seconds": checkpoint.waiting_seconds,
            "longest_wait_seconds": checkpoint.longest_wait_seconds,
        },
        "bytes": {
            "downloaded": checkpoint.downloaded_bytes,
            "expected": checkpoint.expected_bytes,
        },
        "rate": {"average": checkpoint.average_rate, "peak": checkpoint.peak_rate},
        "files": {
            "verified": checkpoint.verified_files,
            "complete_unverified": checkpoint.unverified_files,
            "failed": checkpoint.failed_files,
        },
        "diagnostics": [
            {
                "observed_at_utc": item.observed_at_utc,
                "category": item.category,
                "code": item.code,
                "message": sanitize_persisted_diagnostic(item.message),
                "recoverable": item.recoverable,
            }
            for item in record.diagnostics
        ],
    }


def records_to_jsonl(
    records: tuple[HistoryRecord, ...], *, include_identifiers: bool = False
) -> str:
    return "\n".join(
        json.dumps(
            history_record_to_dict(record, include_identifiers=include_identifiers),
            sort_keys=True,
        )
        for record in records
    )


def history_table(records: tuple[HistoryRecord, ...]) -> Table:
    table = Table(title="Local download history")
    table.add_column("Session")
    table.add_column("Repository")
    table.add_column("Mode")
    table.add_column("Outcome")
    table.add_column("Downloaded", justify="right")
    for record in records:
        item = record.checkpoint
        table.add_row(
            item.session_id,
            item.repository_label,
            item.mode,
            "active" if item.outcome is None else item.outcome.value,
            str(item.downloaded_bytes),
        )
    return table
