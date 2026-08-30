# Security policy

## Local history

History is opt-in and local-only. Default records exclude filenames, command lines,
process identifiers, credentials, content, and readable repository or destination
identifiers. Treat an identifier-enabled database and the pseudonym key as private local
state. Use `hf-download-live-monitor history purge --yes` to remove all managed history
state before transferring or retiring a machine. Sanitized reports should contain only
the application version, OS/architecture, error code, checksums, and pass/fail results.

Please report suspected vulnerabilities privately to the project maintainers rather than opening a public issue. Until a public reporting address is configured, do not include credentials, private repository names, command lines, or downloaded data in reports.

Supported releases receive fixes on the newest 0.x line. HF Download Live Monitor stores no credentials, includes no telemetry, and delegates authentication to `huggingface_hub`. Diagnostic output should still be reviewed before sharing because local paths and repository names may be sensitive.

## Signature verification

Official commits, release tags, and release artifacts are signed with the project GPG key:

```text
BF31 7715 C9E7 B15A 750F  481A 5C53 F257 69B6 CA89
```

Import the public key bundled in [`SIGNING_KEY.asc`](SIGNING_KEY.asc), confirm the full
fingerprint, and verify an artifact's detached signature:

```console
gpg --import SIGNING_KEY.asc
gpg --fingerprint BF317715C9E7B15A750F481A5C53F25769B6CA89
gpg --verify artifact.asc artifact
```

Treat a signature as valid only when the fingerprint matches exactly. A valid signature proves
which key signed the file; it does not replace checksum verification or operating-system safety
checks.
