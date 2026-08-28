# Privacy-First Local History Design

## Purpose

HF Download Live Monitor will optionally retain useful download outcomes and diagnostics
between runs without introducing telemetry, cloud synchronization, or a requirement to
store repository names and local paths. History belongs to the person running the
application: it remains on that computer, is disabled by default, and can be inspected,
exported, selectively deleted, or completely removed at any time.

This phase also prepares a repeatable acceptance procedure for testing the distributed
application on a second physical PC or virtual machine after automated verification is
green. Publishing a new release remains a separate, explicitly authorized operation.

## Non-goals

- No remote history service, account, telemetry, synchronization, or network request.
- No downloader pause, throttle, scheduling, or remote process control.
- No storage of credentials, command lines, environment variables, downloaded content,
  filenames, file digests, authentication state, or per-file observations.
- No analytics database or indefinitely growing high-frequency event log.
- No automatic deletion or replacement of a database that appears corrupt.

## User control and defaults

History is opt-in. A new installation performs no history writes during `watch`, `run`,
or `attach` until the user runs `history enable` or supplies `--record-history` for that
invocation. `history disable` stops future recording without deleting existing records.

The default retention period after opt-in is 30 days and the default database-size cap
is 64 MiB. Users may configure a positive retention period, select unlimited retention
explicitly, and change the cap. Retention and capacity cleanup occur inside the same
local database transaction as a subsequent history write. Cleanup removes the oldest
terminal sessions first and never removes an active session merely to satisfy the cap.
If the cap cannot be respected without deleting active data, recording pauses and emits
a sanitized diagnostic; download monitoring continues.

The following operations are required:

- `history status` reports whether recording is enabled, the resolved database location,
  retention policy, size cap, schema version, record count, and health without exposing
  recorded identifiers.
- `history enable`, `history disable`, and `history configure` change local settings.
- `history list` displays bounded summary rows and supports terminal-state and time filters.
- `history show SESSION_ID` displays one summary and its sanitized diagnostics.
- `history export` emits sanitized JSON by default.
- `history delete SESSION_ID` removes one record and its dependent data transactionally.
- `history clear --before DATE` removes a user-selected range after confirmation.
- `history purge --yes` removes the database, SQLite sidecars, and local pseudonym key,
  returning the application to never-enabled state. Interactive use asks for confirmation;
  non-interactive use requires `--yes`.
- `history vacuum` reclaims unused database pages after explicit user invocation.

Destructive commands report exactly what they removed. A disabled or purged history store
must not be silently recreated by ordinary monitoring.

## Storage location and permissions

The implementation uses Python's standard-library `sqlite3`; Phase 8 adds no runtime
dependency. Default state roots follow platform conventions:

- Windows: `%LOCALAPPDATA%\HF Download Live Monitor\history`
- macOS: `~/Library/Application Support/HF Download Live Monitor/history`
- Linux and WSL: `$XDG_STATE_HOME/hf-download-live-monitor/history`, falling back to
  `~/.local/state/hf-download-live-monitor/history`

`HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR` may override the directory for portable and test
use. CLI `--history-path` may override the database file for one invocation. Relative
paths are rejected. The application creates only the resolved history directory, database,
SQLite sidecars, and pseudonym-key file. Unix permissions are `0700` for the directory and
`0600` for files. Windows relies on the current user's profile ACL and does not weaken an
existing ACL. Symlinked database or key files are rejected.

The database uses WAL mode, foreign keys, a bounded busy timeout, and transactional
migrations. Only one process performs a migration at a time. Concurrent monitor processes
may write independent sessions. A locked or unavailable store never delays the monitor
beyond the configured short history-write budget.

## Privacy model

Repository names and local paths are pseudonymized by default with HMAC-SHA-256 using a
random 256-bit key generated locally with `secrets.token_bytes`. The key is stored beside
the database with restrictive permissions and is never exported automatically. Display
labels use non-reversible prefixes such as `repository-a1b2c3d4` and
`destination-91e2f3a4`. This supports grouping repeated downloads without making common
public repository names vulnerable to an offline dictionary attack.

`--include-identifiers` is valid only when recording is enabled for that invocation. It
stores the readable repository name and normalized local path for newly recorded sessions.
The flag never changes the global default. Identifier-bearing export requires both
`--include-identifiers` and interactive confirmation, or `--yes` in non-interactive use.
Sanitized export omits those columns even when a record contains them.

All errors pass through the existing redaction boundary before persistence. Stored
diagnostics contain only stable category and code values plus a bounded, redacted message.
Control characters are removed, messages are length-limited, and token-shaped data is
rejected after redaction. History output and exports apply the same defense again.

## Schema and migrations

Schema version 1 contains these logical tables:

- `metadata(schema_version, created_at_utc)` contains exactly one schema record.
- `settings(key, value)` contains recording, retention, and size-cap configuration.
- `sessions` contains a random session ID, mode, pseudonym labels and HMAC values, optional
  readable identifiers, repository type, requested/resolved revision classification
  without the revision value, start/update/end UTC timestamps, terminal outcome, aggregate
  expected/downloaded byte counts, average and peak transfer rate, total waiting seconds,
  longest no-progress interval, verified/unverified/failed file counts, and interruption
  state.
- `diagnostics` contains a foreign key to `sessions`, UTC timestamp, error category, stable
  code, bounded redacted message, and recoverability flag.

The schema stores neither process IDs nor process start tokens after completion. Active
rows use a random persistent session ID unrelated to the supervisor's process-derived ID.
Session summaries are checkpointed at most once every five seconds and on meaningful state
changes. On the next successful open, stale active rows are marked `interrupted`; they are
not represented as successful, failed, or integrity-verified downloads.

Migrations run under `BEGIN IMMEDIATE`, update the schema version only after success, and
roll back completely on failure. Opening a database with a newer unsupported schema is a
read-only error. The application never guesses or destructively repairs malformed schema.

## Application integration

The subsystem has three narrow boundaries:

1. `history_models.py` owns immutable configuration, session-summary, query, and export
   contracts with validation independent of SQLite.
2. `history_store.py` owns platform path resolution, SQLite schema/migrations, transactions,
   retention, capacity enforcement, CRUD, and explicit purge/vacuum operations.
3. `history_recorder.py` converts existing `ProgressSnapshot`, `SessionSnapshot`, and
   lifecycle transitions into aggregate privacy-safe checkpoints. Monitoring code depends
   only on a recorder protocol and a no-op implementation.

The CLI constructs a recorder after resolving effective policy. `WatchApplication`,
`ManagedDownload`, and `DownloadSupervisor` report start/checkpoint/finalize transitions
through the protocol. They do not issue SQL or decide retention. Recorder exceptions are
converted to bounded `history_unavailable`, `history_busy`, or `history_write_failed`
diagnostics and do not change the monitor's exit code or downloader lifecycle.

`history` is a Typer command group whose read and mutation commands call the store directly.
Human output uses Rich/plain tables consistent with the existing CLI. `--json` returns one
versioned document; list/export streaming uses JSON Lines only when explicitly selected.
History structured output receives its own schema version and does not change progress
schema version 2 or supervisor schema version 1.

## Waiting diagnostics

The recorder derives waiting information from aggregate byte movement and current file
states. A checkpoint may classify a session as `progressing`, `waiting_for_data`,
`finalizing`, or `interrupted`. Final summaries include total waiting time and the longest
no-progress interval. Human output explains that waiting can mean downloader retry,
rate-limiting, filesystem delay, or finalization; it never claims a cause without direct
evidence. Diagnostic messages remain recommendations rather than downloader control.

## Failure and recovery behavior

History is fail-open with respect to monitoring:

- Missing state while disabled produces no files.
- Lock contention beyond the bounded timeout skips that checkpoint and reports a sanitized
  warning once per run.
- Disk-full, permission, migration, and I/O failures disable recording for that run without
  altering download monitoring or its exit code.
- Corruption causes ordinary recording and queries to stop. The original files remain
  untouched. `history status` reports `corrupt` without echoing raw SQLite messages.
- `history recover --output ABSOLUTE_PATH` uses SQLite's supported backup/recovery behavior
  to create a separate candidate database and never overwrites the source. The candidate is
  validated before the command reports success.
- `history reset --preserve-corrupt --yes` moves corrupt database files to a timestamped
  backup inside the history directory and creates a clean enabled store. Reset without
  preservation is rejected; irreversible removal uses `history purge --yes` instead.

## Testing and verification

Unit and integration tests use temporary absolute directories and injected clocks/keys.
They must prove:

- disabled-by-default monitoring creates no history directory or database;
- opt-in enablement, per-invocation override, and identifier opt-in semantics;
- platform path resolution and symlink rejection;
- schema creation, transactional migration, newer-schema refusal, WAL configuration,
  foreign keys, lock timeout, and concurrent writers;
- aggregate checkpoint/finalization for watch, run, single attach, and continuous attach;
- crash recovery marks stale rows interrupted;
- 30-day retention, unlimited retention, 64 MiB cap behavior, and active-row protection;
- deterministic list/show filters, sanitized JSON/JSONL exports, and output schema versions;
- selective deletion, confirmed range clearing, vacuum, complete purge, and no silent
  recreation after purge;
- corruption detection and non-overwriting recovery/reset behavior;
- credential, command-line, filename, raw path, repository-name, and token-shaped canaries
  never appear in the default database or sanitized exports;
- history failures never alter monitor results, downloader cleanup, or structured progress
  output.

The full existing quality matrix, distribution builds, Twine validation, standalone smoke
tests, Docker simulation, and native ARM64 CI remain mandatory. Exact changed files and all
built artifacts receive the repository's privacy scan before any push or release action.

## Second-PC or VM acceptance

After implementation is merged and all online checks pass, create a versioned candidate
from the protected commit using the existing signed, draft-first release workflow. Before
publication, inspect the complete private draft bundle for personal paths, identities,
emails, credentials, tokens, private keys, history databases, and machine-local files.
Publishing still requires the user's explicit approval.

The user-facing acceptance document will cover Windows x86-64 and ARM64 where available,
plus a generic VM path. It will require a fresh machine or snapshot and verify:

1. checksum and GPG/provenance validation before execution;
2. `--help` and a simulated incremental download with history still disabled;
3. confirmation that no history directory exists before opt-in;
4. `history enable`, recorded simulated download, list/show, and sanitized export;
5. proof that default history and export contain no readable repository name or local path;
6. explicit identifier opt-in behavior;
7. selective deletion followed by complete purge and filesystem confirmation;
8. uninstall/removal guidance and a sanitized result report suitable for sharing.

The acceptance checklist must not ask the tester to reveal their username, home path,
private repository name, token, full process list, environment, or downloaded content.

## Completion criteria

Phase 8 is complete only when the approved commands and storage behavior are documented,
all automated gates pass locally and on GitHub, distribution artifacts pass privacy review,
and the second-PC/VM acceptance guide is ready. A public release or PyPI publication is not
implied by implementation completion and occurs only through its separately protected flow.
