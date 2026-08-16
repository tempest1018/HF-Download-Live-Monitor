# HF Download Live Monitor Design

Date: 2026-08-14
Status: Approved

## Product identity

HF Download Live Monitor is a privacy-conscious, cross-platform terminal application for observing Hugging Face downloads. The Python distribution is named `hf-download-live-monitor`, the command is `hf-download-live-monitor`, and the source package is `hf_download_live_monitor`. The project uses the MIT license and supports Python 3.10 and newer.

Native Windows, WSL, and Linux are first-class targets. macOS supports explicit monitoring and managed downloads in the initial release; automatic process attachment may be added when it can be implemented reliably.

## Goals

- Accurately monitor complete or filtered model, dataset, and Space downloads.
- Support attaching to existing downloads, watching an explicit destination, and launching a managed `hf download` child process.
- Provide responsive interactive output, stable plain output, and versioned JSON or JSON Lines output.
- Remain useful when process inspection or remote repository metadata is unavailable.
- Protect credentials and local process information by default.
- Be testable, maintainable, and distributable through standard Python tooling.

## Non-goals for the initial release

- Reimplementing the Hugging Face download engine.
- A desktop GUI or web service.
- Telemetry or analytics.
- Persisting Hugging Face credentials.
- Depending on filesystem notifications for correctness.
- Committing generated standalone executables to the repository.

## Commands and behavior

### `attach`

`hf-download-live-monitor attach` discovers active Hugging Face CLI downloads. When exactly one supported process exists, monitoring begins automatically. When several exist, an interactive terminal offers a selection; `--all` monitors every supported process. In non-interactive use, ambiguity is reported with a meaningful exit code unless selection is supplied explicitly.

Process discovery supports native Windows and POSIX systems, including WSL. It recognizes executable-path and Python module variants of the Hugging Face CLI, parses separated and equals-style options, resolves relative destinations against the child process working directory, and redacts credentials before data leaves the discovery boundary.

### `watch`

`hf-download-live-monitor watch REPO --local-dir PATH` observes a known destination without inspecting local processes. Repository type, revision, selected filenames, include patterns, and exclude patterns can be supplied explicitly. This is the portable fallback for restricted systems, remote sessions, containers, and unsupported process layouts.

### `run`

`hf-download-live-monitor run REPO [HF DOWNLOAD OPTIONS]` launches the official Hugging Face CLI as a managed child. Compatible download arguments are forwarded without exposing secrets, while the monitor tracks the exact PID and exit status. The first interrupt requests graceful child termination; a repeated interrupt forces termination. The monitor propagates a meaningful final exit status.

### Common behavior

- Interactive terminals receive a live display with per-file and aggregate progress, rates, ETAs, states, elapsed time, repository information, and completion summaries.
- Redirected output automatically uses stable plain text.
- `--json` emits a single versioned snapshot or summary as appropriate.
- `--jsonl` emits versioned event records suitable for automation.
- `--once` performs one observation and exits.
- Refresh interval, rate window, sorting, completed-file visibility, color, Unicode, and verbosity are configurable.
- ANSI control sequences are never emitted to non-interactive output.
- Narrow terminals and ASCII-only environments remain readable.

## Architecture

The application is divided into focused typed modules:

- `cli`: arguments, validation, command dispatch, and exit codes.
- `models`: immutable download specifications, observations, snapshots, rates, states, and events.
- `processes`: platform-specific process discovery through a normalized interface.
- `hf_command`: parsing and normalization of supported Hugging Face CLI invocations.
- `repository`: metadata retrieval and requested-file selection.
- `filesystem`: safe observation of final and partial files.
- `engine`: deterministic state transitions, totals, rates, ETAs, and completion.
- `runner`: managed Hugging Face child processes and signal handling.
- `renderers`: interactive, plain, JSON, and JSON Lines presentation.
- `security`: credential redaction and path-containment validation.
- `compat`: isolated Hugging Face cache-layout compatibility.
- `app`: orchestration independent of presentation details.

Data flows in one direction:

1. Process discovery or explicit CLI input creates a normalized download specification.
2. Repository metadata and command filters create the requested-file manifest.
3. Filesystem observation creates a point-in-time set of file measurements.
4. The state engine combines observations with prior state and an injected clock.
5. A renderer consumes immutable snapshots or events.

Renderers do not inspect processes, query the Hub, or traverse arbitrary filesystem paths. The engine has no terminal dependency. Hugging Face private cache conventions are isolated in `compat` and guarded by compatibility tests.

## Download and file states

The engine distinguishes at least:

- queued: requested but no local bytes are visible;
- measuring: bytes exist but the rate window is not mature;
- downloading: observed bytes are increasing;
- waiting: partial bytes exist but are not currently increasing;
- finalizing: expected bytes are present while the final path transition is pending;
- complete: a final file exists with the expected size;
- inconsistent: final size or observed state conflicts with repository metadata;
- failed: the managed process or a definitive operation reports failure;
- stopped: the associated process exited before verified completion.

Existing final files are not considered complete unless their expected sizes agree. Zero-byte files are valid when the repository metadata reports zero bytes. Completed files can remain visible, be hidden, or expire from the live view after a configured interval.

## Repository and selection semantics

The repository layer supports models, datasets, and Spaces through public Hugging Face APIs. It accounts for explicit filenames, include patterns, exclude patterns, revision identifiers, and repository type. Aggregate totals cover only the requested manifest.

Metadata is cached by repository, type, and revision. Transient failures use bounded exponential backoff with jitter. If metadata becomes unavailable, the application continues with clearly labeled local-only observations when it can do so safely; it does not invent totals or completion claims.

## Filesystem and performance

Polling is the correctness baseline across all platforms. Each refresh indexes visible partial files once and correlates them with the requested manifest, avoiding one glob operation per repository file. Refresh frequency may reduce while idle but cannot hide state transitions.

All filesystem races are expected: files may disappear, be renamed, finalize, or become locked between observation steps. These cases produce a subsequent observation or classified warning instead of an unhandled exception. Histories are bounded and removed after files leave the active set. Large repositories containing thousands of files receive dedicated performance tests.

Resolved repository paths must remain within the monitored destination. Platform separators, long Windows paths, symlinks, and malicious repository filenames are handled defensively.

## Rates and ETAs

Rates use monotonic time and bounded rolling histories. Resumed bytes present in the first observation are treated as existing progress rather than new transfer speed. Aggregate rate is derived from byte deltas over the same observation interval rather than summing incompatible samples. ETAs appear only after sufficient stable data exists and are explicitly unavailable otherwise.

## Error handling

Errors are classified into process discovery, command parsing, authentication, repository, revision, rate limiting, connectivity, destination access, metadata compatibility, download failure, and inconsistent local state. Interactive output presents concise corrective guidance; verbose logs retain safe diagnostic context; structured renderers emit stable machine-readable error codes.

Degraded operation is preferred when accurate partial information remains available. Authentication failures, missing metadata, or unsupported process inspection must not silently produce false progress or completion.

## Security and privacy

- Tokens are redacted from arguments, logs, errors, process representations, and structured output.
- The application has no telemetry and performs no undisclosed network operations.
- Credentials are delegated to normal Hugging Face configuration and are never persisted by the monitor.
- Complete process command lines are not emitted by default.
- Repository filenames are validated before path resolution, and resolved paths are checked for containment.
- Process metadata and private repository names are treated as sensitive diagnostic data.
- Release workflows use short-lived trusted publishing rather than stored PyPI upload credentials.

## Testing

The suite includes:

- unit tests for command parsing, selection, states, rates, ETAs, formatting, containment, and redaction;
- fake-clock and filesystem-fixture tests for deterministic progress behavior;
- Windows, Linux, WSL-style, and macOS process fixtures;
- race tests covering disappearing, renamed, locked, resumed, and finalized files;
- model, dataset, Space, revision, filter, and concurrent-download cases;
- renderer snapshots over terminal widths and Unicode capabilities;
- versioned JSON schema compatibility tests;
- managed-child signal and exit propagation tests;
- integration tests using fabricated Hugging Face cache trees;
- opt-in end-to-end tests against a deliberately pinned tiny public repository;
- compatibility tests across supported Python and `huggingface_hub` versions;
- clean installation and executable smoke tests.

Routine CI does not require credentials or depend on mutable external repositories.

## Repository and distribution

The project uses a `src/hf_download_live_monitor` package layout with tests, documentation, scripts, and GitHub Actions workflows. `pyproject.toml` defines metadata, dependencies, development tools, and the console entry point.

Ruff provides formatting and linting, Pyright provides static type checking, and pytest provides tests. CI runs these checks, builds wheels and source distributions, validates their metadata, and installs and executes the built command in clean environments on Windows and Linux. macOS validates the supported explicit modes.

The primary distribution is PyPI, with `pipx install hf-download-live-monitor` recommended and `uv tool install` and `pip` supported. Semantic versions are derived from release tags. Version `0.1.0` is the first public milestone; real-world stabilization precedes `1.0.0`.

Standalone Windows, Linux, and macOS executables are a later 0.x milestone. They are generated, tested, checksummed, and attached by CI. Package-manager listings such as Winget, Scoop, and Homebrew follow only after the underlying releases are stable.

The repository includes an MIT license, README, changelog, security policy, contribution guide, issue templates, pull-request template, command and schema reference, platform guidance, troubleshooting, privacy documentation, and an architectural overview.

## Release quality gates

A release is permitted only when:

- formatting, linting, typing, tests, and package checks pass;
- clean-environment installations execute the packaged command successfully;
- credential-redaction and path-containment tests pass;
- documented compatibility matches the tested matrix;
- generated artifacts contain no local caches, credentials, logs, or development environments;
- release notes and checksums are produced from the final artifacts.

## Delivery sequence

1. Establish the package, domain models, CLI surface, and development quality tools.
2. Implement command parsing, security utilities, repository selection, and deterministic state calculations.
3. Implement portable explicit watch mode and all renderers.
4. Add Windows and POSIX process discovery for attach mode.
5. Add managed run mode and signal handling.
6. Complete integration, performance, package, documentation, and CI validation.
7. Publish the Python package after repository ownership and publishing credentials are configured.
8. Add standalone executables after package-based real-world stabilization.
