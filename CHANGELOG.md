# Changelog

All notable changes follow Keep a Changelog and semantic versioning.

## Unreleased

## 0.2.0 - 2026-08-27

### Added

- Continuously discover and monitor concurrent Hugging Face downloads with
  `attach --all`, an adaptive aggregate dashboard, privacy-safe structured events,
  bounded workers, stable process identities, and Linux published-artifact acceptance.

## 0.1.2 - 2026-08-27

### Fixed

- Restore the host library search path before a frozen Linux or AIX standalone launches
  the external Hugging Face CLI, preventing bundled OpenSSL libraries from breaking the
  child process on newer host runtimes.

## 0.1.1 - 2026-08-27

### Added

- Add read-only published-release acceptance across six native targets and an isolated
  public-wheel installation.

### Fixed

- Emit the final snapshot from continuous JSON mode instead of the first observation.
- Reject malformed or negative repository file-size metadata safely.
- Map renderer shutdown failures to the documented monitor error category and exit code.
- Remove timing assumptions from asynchronous integrity-verification tests.

### Security

- Verify release tags with an independently stored, fingerprint-pinned public key and
  require protected-main ancestry before publishing.
- Protect `main` with required signed commits and CI checks, and protect stable release
  tags against deletion or backward movement.

## 0.1.0 - 2026-08-24

### Added

- Add portable watch mode for models, datasets, and Spaces.
- Add Windows and POSIX attachment with PID and interactive selection.
- Add Adaptive Focus with responsive layouts, three density modes, keyboard controls,
  reduced motion, ASCII, no-color, and non-interactive fallbacks.
- Add destination and disk-capacity preflight, immutable revision resolution, LFS
  SHA-256 verification, and stable public error categories.
- Add deterministic real-child and Docker download simulations.

### Changed

- Rename the project, distribution, command, imports, and artifacts to HF Download
  Live Monitor.
- Upgrade structured output to schema version 2 with requested/resolved revisions and
  explicit verified, complete-unverified, and failed integrity counts.
- Pin managed downloads and metadata queries to a resolved commit SHA.

### Fixed

- Guarantee downloader termination, escalation, and reaping when monitoring fails or
  is cancelled.
- Force final filesystem observation and verification after downloader exit.
- Reconcile and render a final observation on Ctrl+C after managed child cleanup,
  preserving integrity failure precedence over cancellation when cleanup succeeds and
  downloader cleanup-failure precedence when it does not.
- Bound background integrity work and invalidate cached hashes when file identity
  changes.

### Security

- Keep authentication in the Hugging Face credential flow and redact credentials
  from errors, process inspection, logs, and structured output.
- Refuse to start when repository access, metadata, destination safety, or capacity
  checks fail.

### Distribution

- Add architecture-labelled standalone artifacts and SHA-256 checksum files for
  Windows, Linux, and macOS on x86-64 and ARM64.
- Configure native ARM64 CI validation; release assets are available only after their
  GitHub jobs pass. Retain source/wheel packages as the portable fallback.
