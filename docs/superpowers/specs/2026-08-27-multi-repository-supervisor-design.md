# Continuous Multi-Repository Supervisor Design

Date: 2026-08-27
Status: proposed

## Objective

Make `hf-download-live-monitor attach --all` a durable, privacy-safe supervisor that
discovers and monitors multiple concurrent Hugging Face downloads until interrupted.
Downloads may appear, complete, fail, restart, or become inaccessible while the monitor
is running. Existing `watch`, `run`, single-PID `attach`, and `attach --all --once`
behavior remain compatible.

## User experience

The default interactive command is:

```powershell
hf-download-live-monitor attach --all
```

The dashboard has one aggregate header and one row per tracked repository. The header
shows active, finalizing, completed, failed, and lost session counts; aggregate bytes;
combined throughput; and discovery health. A selected repository expands into the
existing file, integrity, and diagnostic panels. `j`/`k` and the arrow keys change the
selection, `v` changes density, `?` toggles help, and `q` exits the monitor without
terminating attached downloaders. Narrow terminals reduce each row to repository,
percentage or bytes, rate, and lifecycle state. Reduced-motion, ASCII, and no-color
modes apply to the complete dashboard.

Completed sessions remain visible for 15 seconds by default so their final result is
observable. `--retention SECONDS` changes that interval; zero removes a finalized
session after its final event is rendered. `--max-sessions` defaults to 32 and rejects
values below one. Candidates beyond the cap are ignored deterministically by process
identity and produce a sanitized warning.

Non-interactive output follows explicit contracts:

- `--plain` emits timestamped lifecycle changes and rate-limited progress lines without
  repainting or duplicating unchanged state.
- `--jsonl` emits every lifecycle event and at most one routine progress event per
  session per second. Final events are never suppressed.
- `--json` emits no intermediate documents and writes exactly one aggregate final
  document during orderly shutdown.
- `--once` preserves the existing atomic snapshot behavior and does not enter the
  continuous supervisor loop.

## Architecture

`DownloadSupervisor` is a coordinator above the existing repository, observer, progress
engine, integrity, and rendering boundaries. It owns no platform-specific discovery
logic and does not allow worker threads to render. Each iteration performs four ordered
stages:

1. collect completed initialization or observation results;
2. discover process identities when their monotonic deadline is due;
3. advance session lifecycle state and schedule bounded work;
4. create one immutable `SupervisorSnapshot` and pass it to one renderer.

New modules have narrow responsibilities:

- `supervisor_models.py` defines frozen process identities, session lifecycle records,
  aggregate snapshots, diagnostics, and JSON-safe events.
- `supervisor.py` owns discovery reconciliation, scheduling, retention, shutdown, and
  deterministic event sequencing.
- `supervisor_renderers.py` renders aggregate snapshots and events while reusing the
  existing single-repository panels where practical.

The CLI constructs these components through a dedicated `_make_supervisor()` path.
Existing `WatchApplication` remains the single-repository runtime and is not overloaded
with multi-session branching.

## Identity and lifecycle

Process providers add an opaque start token to `ProcessRecord`. A unified psutil-backed
provider supplies PID, creation time, command arguments, and working directory on
Windows, Linux, and macOS without shelling out through PowerShell for every scan. The
existing native providers remain narrow fallbacks where psutil cannot inspect a process;
Linux can derive its start token from `/proc/<pid>/stat`. Unsupported or permission-
denied records are skipped individually. `ProcessIdentity(pid, start_token)` prevents
PID reuse from joining an old download session. Raw command lines are parsed into an
allowlisted `DownloadCandidate` and then discarded.

The session key combines process identity, repository type, repository ID, normalized
local directory, and requested revision. Sorting uses repository ID, normalized local
directory, PID, and start token so output does not depend on thread completion order.

Sessions move only through these states:

```text
discovered -> preparing -> active -> finalizing -> completed
                                     |             failed
                                     |             lost
                                     +-----------> finalizing
```

Preparation resolves repository metadata and immutable revision without blocking the
coordinator. A process that disappears enters `finalizing`; the supervisor forces a
fresh filesystem observation and completes all eligible integrity work. It becomes
`completed` only when the manifest is satisfied, `failed` for a confirmed integrity or
monitor failure, and `lost` when the process vanished before sufficient evidence was
available. Reappearance with a different start token creates a new session.

## Data and event contracts

`SupervisorSnapshot` contains a monotonic observation time, aggregate counters,
discovery health, and an ordered tuple of `SessionSnapshot` values. Each session contains
only its stable ID, PID, sanitized repository identity, local directory, lifecycle,
latest `ProgressSnapshot`, and structured diagnostics.

Supervisor JSON is a new contract rather than a silent extension of single-download
schema version 2:

- aggregate final documents use `schema_version: 1` and `kind: "supervisor_snapshot"`;
- JSONL events use `schema_version: 1`, `kind: "supervisor_event"`, a run-scoped UUID,
  and a strictly increasing `sequence` number;
- event types are `session_added`, `session_ready`, `progress`, `session_finalized`,
  `session_removed`, `discovery_warning`, and `supervisor_stopped`;
- every event includes `observed_at`, while session events include the stable session ID;
- `supervisor_stopped` is always the final JSONL event during orderly shutdown.

Rates and ETAs are computed per session. Aggregate rate is the sum of current finite,
non-negative session rates. No aggregate ETA is reported because parallel downloads do
not share a meaningful completion clock. Unknown totals show transferred bytes and rate
without inventing a percentage or ETA.

## Scheduling and resource bounds

Filesystem refresh retains the existing `--refresh` default of 0.25 seconds. Process
discovery uses `--discovery-refresh`, defaulting to one second, and backs off
monotonically to five seconds when no sessions are active. Discovery returns immediately
to one second when a candidate is found or a lifecycle transition occurs.

A bounded executor with at most four workers handles repository preparation and forced
finalization. At most one outstanding task exists per session and results return through
the coordinator. Routine filesystem observation remains coordinator-owned initially to
preserve deterministic state and avoid multiplying disk pressure. Existing bounded hash
scheduling and identity-based cache behavior remain in force. The design performs one
process scan per discovery cycle and one filesystem index per due session refresh.

The supervisor uses monotonic time for deadlines, retention, rate limiting, and tests.
Injected clock, sleeper, provider, observer, repository, and executor boundaries make
the complete lifecycle deterministic under unit tests.

## Failure and shutdown behavior

Discovery failures never erase current sessions. The last successful candidate set is
retained, discovery health becomes degraded, and warnings are deduplicated with bounded
exponential backoff. A later successful scan clears the warning. One session's metadata,
filesystem, integrity, or rendering data cannot mutate another session.

Initialization and observation errors are converted to existing stable error categories
where possible and stored as sanitized structured diagnostics. Unexpected errors use a
generic monitor diagnostic; raw exception text is sent only through the existing
redaction boundary. Renderer failure stops the supervisor with the documented monitor
exit code after attempting final reconciliation. Cleanup errors are attached as
sanitized diagnostics without replacing the primary failure.

On `q`, Ctrl+C, or termination, new discovery stops first. Outstanding bounded tasks are
cancelled when safe or awaited with a finite timeout, active sessions receive one forced
final observation, one final snapshot/event is rendered, controls and renderer close,
and no attached downloader is terminated. A second interrupt forces prompt monitor-only
exit.

## Privacy and security

The supervisor never persists raw process command lines, environment variables, tokens,
authorization headers, passphrases, or downloader output. Discovery parses only known
`hf download` arguments required to construct `DownloadSpec`. Unknown arguments are
ignored unless they make repository or destination identity ambiguous, in which case the
candidate is rejected with a generic diagnostic.

Paths are normalized for identity but displayed according to existing safe output rules.
Diagnostics pass through `redact_text` before entering models. JSON and plain output use
the same sanitized model as the Rich renderer. Reports contain no raw process records.
The supervisor is observational: it does not pause, resume, throttle, signal, or kill an
attached downloader.

## Testing strategy

Development is test-driven and adds:

- pure lifecycle tests for discovery, preparation, PID reuse, disappearance,
  finalization, retention, caps, ordering, and shutdown;
- fake-clock scheduling tests proving discovery backoff, refresh deadlines, progress
  rate limits, and absence of busy loops;
- privacy tests with tokens and credentials in command lines and exceptions;
- renderer contract tests for Rich, narrow, reduced-motion, ASCII, plain, JSONL, and
  final JSON behavior;
- integration tests with two or more deterministic child downloads that start and finish
  in different orders, including one failure and one inaccessible process;
- Docker tests for dynamic discovery and process disappearance;
- provider and integration coverage on Windows, Linux, and macOS, with x86_64 and ARM64
  build and smoke coverage through the existing matrix;
- published-artifact acceptance extended with a bounded multi-download scenario.

Tests assert that structured stdout remains uncontaminated, event sequences are strict,
final states are never dropped, attached children survive monitor cancellation, and
polling stays within configured bounds.

## Documentation and compatibility

README, user manual, architecture documentation, CLI help, JSON examples, and release
acceptance documentation will describe continuous `attach --all`. The current warning
that rejects continuous multi-download display is removed only after its replacement is
covered by integration and published-artifact tests. Existing options keep their names
and meanings. New supervisor schema and event contracts are documented separately from
single-download schema version 2.

## Non-goals

This phase does not add persistent history, pause/resume, bandwidth throttling,
scheduling, remote agents, non-Hugging-Face backends, or downloader termination. Those
features require separate security and lifecycle designs.

## Acceptance criteria

The phase is complete when one continuous `attach --all` process can discover at least
two downloads started after the monitor, track them independently through different
outcomes, add a replacement process that reuses a PID identity safely, emit deterministic
privacy-safe Rich/plain/JSONL/final-JSON output, exit without terminating downloaders,
and pass the full local, Docker, native CI, release, and published-artifact acceptance
matrices.
