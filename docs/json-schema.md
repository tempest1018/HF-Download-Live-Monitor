# Structured output schema

`--json` emits one document for the first render. With `--once`, that render is a final
one-shot observation. `--jsonl` emits one document per observation. Every document
contains `"schema_version": 2`.

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
  "errors": [
    {
      "category": "monitor",
      "code": "temporary_observation_error",
      "message": "a redacted, user-safe diagnostic",
      "recoverable": true
    }
  ]
}
```

Rates and ETAs are nullable. Byte counts are non-negative integers. Errors contain
stable `category`, `code`, `message`, and `recoverable` fields and never contain raw
tokens or authorization headers.

File states are `queued`, `measuring`, `downloading`, `waiting`, `finalizing`,
`complete`, `inconsistent`, `size_matched`, `verifying`, `verified`,
`complete_unverified`, and `failed`. These are the exact runtime vocabulary. Only
`verified` represents a successful SHA-256 comparison. `complete_unverified` means
the size matched but no supported digest was available; consumers must not relabel it
as verified.

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

## Stream semantics and confidentiality

`--json` emits exactly one snapshot: the first render. With `--once`, that first render
is also a final one-shot observation. `--jsonl` emits one complete schema-v2 JSON
object per observation. It includes the forced final observation for the managed
downloader stop condition, dashboard `q`, and handled Ctrl+C. Managed Ctrl+C attempts
to stop and reap the child before that final reconciliation; the child is confirmed
stopped and reaped when cleanup succeeds. `watch` and `attach` have no managed child
but still emit their final observation. With successful managed cleanup, cancellation
returns exit code `9` unless a final integrity failure takes precedence with exit code
`8`. On cleanup failure, including a second interrupt during cleanup, child reaping is
not confirmed. The failure is retained while final reconciliation and serialization
still run; cleanup failure takes precedence over snapshot integrity and maps to
downloader exit code `6`. Lines are independently parsable, and their order is
observation order.

Repository tokens, authorization headers, and credentials are never schema fields.
Error serialization redacts known token-assignment and bearer-authorization patterns
without changing the internal error. Avoid putting secrets in arbitrary repository IDs,
paths, or filenames because those user-supplied values are ordinary schema data. Within
schema version 2, fields may be added compatibly; existing field meanings and types are
stable. A breaking removal, rename, type change, or semantic change requires a new
schema version.
