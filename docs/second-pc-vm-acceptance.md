# Second-PC/VM acceptance guide

Use a fresh machine or VM snapshot and a public, tiny Hugging Face fixture. Do not use a
private repository or production download directory. Replace every placeholder below
with a non-personal test path.

1. Download the release artifact, `SHA256SUMS`, signature, and attestation from GitHub.
   Verify the SHA-256 value, verify the GPG-signed tag against fingerprint
   `BF317715C9E7B15A750F481A5C53F25769B6CA89`, confirm the commit is reachable from
   protected `main`, and verify the GitHub artifact attestation.
2. Confirm the artifact architecture matches Windows x86_64/ARM64, Linux x86_64/ARM64,
   or macOS x86_64/ARM64. Run `hf-download-live-monitor --version` and `--help`.
3. Set `HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR` to an empty absolute test directory. Run a
   one-shot public monitor without history and confirm the directory remains absent.
4. Run `history enable`, then a simulated/public-tiny monitor with `--record-history`.
   Check `history status`, `history list`, `history show SESSION_ID`, and a sanitized
   `history export --jsonl`. Confirm output has no username, home path, token, private
   repository, filename, process identity, or downloaded content.
5. Exercise `history delete SESSION_ID`, record again, then run `history purge --yes`.
   Confirm the database, `-wal`, `-shm`, and pseudonym key are gone while downloads remain.
6. Uninstall the app using the platform's normal method and restore the VM snapshot if
   desired.

When reporting results, sanitize them and share only step pass/fail, application version,
OS and architecture, sanitized error code, and artifact checksums. Never share usernames,
home paths, private repository names, tokens, process lists, system variables, databases,
keys, or downloaded content.
