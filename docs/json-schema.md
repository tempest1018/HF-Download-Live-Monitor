# Structured output schema

`--json` emits one document and `--jsonl` emits one document per observation. Every document contains `schema_version`, currently integer `1`.

Top-level fields are `observed_at`, `repository`, `downloaded_bytes`, `expected_bytes`, `rate_bytes_per_second`, `eta_seconds`, `files`, and `errors`. Rates and ETAs are nullable. Byte counts are non-negative integers. Repository fields are `id`, `type`, `revision`, and `local_dir`.

Each file contains `filename`, `expected_bytes`, `downloaded_bytes`, `state`, `rate_bytes_per_second`, and `eta_seconds`. States are `queued`, `measuring`, `downloading`, `waiting`, `finalizing`, `complete`, or `inconsistent`.

Consumers must reject unsupported major schema versions and tolerate additional fields within version 1.
