# Structured output schema

`--json` emits one final document and `--jsonl` emits one document per observation.
Every document contains `"schema_version": 2`.

## Version 2 example

```json
{
  "schema_version": 2,
  "observed_at": 1786965600.5,
  "repository": {
    "id": "owner/repository",
    "type": "model",
    "requested_revision": "main",
    "resolved_revision": "0123456789abcdef0123456789abcdef01234567",
    "local_dir": "/downloads/repository"
  },
  "downloaded_bytes": 1048576,
  "expected_bytes": 1048576,
  "rate_bytes_per_second": 343756.0,
  "eta_seconds": 0.0,
  "integrity": {
    "verified_files": 1,
    "complete_unverified_files": 0,
    "failed_files": 0
  },
  "files": [
    {
      "filename": "model.bin",
      "expected_bytes": 1048576,
      "downloaded_bytes": 1048576,
      "state": "verified",
      "rate_bytes_per_second": 0.0,
      "eta_seconds": 0.0
    }
  ],
  "errors": []
}
```

Rates and ETAs are nullable. Byte counts are non-negative integers. Errors contain
stable `category`, `code`, `message`, and `recoverable` fields and never contain raw
tokens or authorization headers.

File states are `queued`, `in_progress`, `size_matched`, `verifying`, `verified`,
`complete_unverified`, and `failed`. Only `verified` represents a successful SHA-256
comparison. `complete_unverified` means the size matched but no supported digest was
available.

## Version 1 migration

| Version 1 | Version 2 |
| --- | --- |
| `repository.revision` | `repository.requested_revision` and `repository.resolved_revision` |
| Top-level byte/rate/ETA fields | Retained at top level |
| `complete` | `verified` or `complete_unverified`, depending on digest availability |
| `inconsistent` | `failed` with an integrity error |
| No aggregate integrity object | `integrity` count object |

Consumers must reject unsupported major schema versions and tolerate additional
fields within version 2. Do not infer integrity from byte totals alone.
