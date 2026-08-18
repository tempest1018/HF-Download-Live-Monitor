# Safety, Adaptive TUI, and ARM64 Design

## Status

Approved in conversation on 2026-08-17. This specification covers the next implementation milestone for HF Download Live Monitor.

## Goals

- Pin each download to an immutable Hugging Face commit.
- Verify downloaded LFS content with SHA-256, not size alone.
- Make downloader lifecycle and final-state observation reliable under failure.
- Reject downloads that cannot safely start because access, metadata, destination, or disk requirements are invalid.
- Replace the basic live table with an engaging, flexible Adaptive Focus interface.
- Add ARM64 to the supported distribution and CI surface.
- Preserve stable, machine-readable behavior for automation.

## Non-goals

- Replacing the Python implementation with a native GUI.
- Becoming a general-purpose Hugging Face download client.
- Adding a web service, account storage, or telemetry.
- Retaining compatibility with obsolete internal syntax when a clean public interface is preferable.

## User Experience: Adaptive Focus

Interactive runs use a terminal-native dashboard organized by immediate importance:

1. Repository identity and resolved immutable revision.
2. Aggregate progress, transfer rate, ETA, and a bounded throughput history.
3. Active, failed, or otherwise attention-worthy files.
4. Preflight and integrity status.
5. Collapsed summaries for completed and queued files.
6. A small keyboard-help footer.

The default view is balanced. Users can switch among compact, balanced, and detailed views without restarting. Optional panels include file details, throughput history, verification, and recent events. Completed files collapse automatically unless detailed mode is selected.

### Flexible layout

The renderer derives a layout class from the current terminal dimensions on every refresh:

- Narrow: one column, abbreviated labels, no decorative graph, active/problem files only.
- Normal: one column with aggregate metrics, sparkline, preflight summary, and active files.
- Wide: aggregate and telemetry panels may sit side by side, with additional file and event detail.

No essential state is conveyed by color or animation alone. The interface supports no-color output, Unicode and ASCII glyph sets, and reduced motion. Resizing must not crash or corrupt the display.

When stdout is redirected, the terminal is unsuitable, or a machine-oriented format is requested, the application uses a non-live renderer. Plain text remains readable in Docker logs and CI; JSON emits versioned structured snapshots without decorative output.

### Interaction

Keyboard input is optional and only enabled for an interactive terminal. Initial controls:

- `v`: cycle compact, balanced, and detailed views.
- `d`: toggle expanded file details.
- `e`: toggle recent events.
- `q`: request graceful cancellation.
- `?`: show concise help.

Failure to initialize keyboard input must degrade to a non-interactive live display rather than aborting the monitor.

## Architecture

The implementation keeps the Python core and strengthens boundaries between four concerns:

- Repository resolution and preflight.
- Filesystem observation and integrity verification.
- Downloader process lifecycle.
- Snapshot presentation.

Renderers consume immutable structured snapshots and never inspect the filesystem, call Hugging Face APIs, or control the child process directly. The same snapshot model feeds Adaptive Focus, compact live, plain-text, and JSON output.

## Immutable revision resolution

Before launch, the repository adapter resolves the user-provided revision (branch, tag, or commit) to a full immutable commit SHA through the authenticated Hugging Face API. All metadata queries and the downloader command use that resolved SHA. Both the requested revision and resolved SHA appear in snapshots and final output.

Resolution failure is a preflight failure; the downloader is not started. A resolved revision cannot silently move during the run.

## Repository and destination preflight

Preflight executes before child-process creation and validates:

1. Token availability when required, without printing or persisting the token.
2. Repository existence and caller access, including gated/private repository errors.
3. Requested revision resolution.
4. Metadata availability and a usable file manifest.
5. Destination path creation and writability.
6. Available disk capacity.

Authentication, missing repository, gated access, rate limiting, connectivity, and malformed metadata receive distinct actionable messages where the upstream response permits a distinction.

Disk demand is computed from the remaining expected bytes plus conservative workspace overhead and a configurable safety margin. Existing valid bytes reduce the requirement, but uncertain partial content does not receive optimistic credit. Insufficient capacity prevents launch and reports required, available, and destination values.

## Integrity model

Expected repository entries include path, expected size, and an optional LFS SHA-256 object identifier. File state is explicit:

- `queued`: no matching local content yet.
- `in_progress`: local content exists but is smaller than expected.
- `size_matched`: expected size is present and hashing is pending or unavailable.
- `verifying`: SHA-256 calculation is underway.
- `verified`: size and expected digest match.
- `complete_unverified`: size matches but the repository supplied no supported digest.
- `failed`: content exceeds expected size, digest mismatches, or another terminal integrity error occurs.

Only `verified` is described as integrity-verified. Files without a supported digest may be complete but must never be labelled verified. Aggregate completion distinguishes byte completion from integrity completion.

Hashing must not block every refresh. The engine schedules hashing only for stable size-matched candidates and caches results using a file identity made from path, size, and high-resolution modification metadata. A changed identity invalidates the cached result. Hash work is bounded so large files do not freeze presentation or process supervision.

## Process lifecycle and final observation

The runner owns the downloader process from creation through reaping. Once started, every exit path must either observe natural termination or request termination, escalate after a bounded grace period if necessary, and reap the child.

Monitor errors, renderer errors, cancellation, keyboard interrupts, and unexpected exceptions all enter the same cleanup path. Cleanup errors are preserved without hiding the original cause.

After the downloader exits, the engine performs a forced final filesystem observation that bypasses normal polling throttles, completes eligible integrity checks, and emits a final snapshot. The command result combines downloader exit status with final monitor/integrity state; a successful child exit cannot mask missing or corrupt output.

## Error model and exit behavior

Public errors are grouped into stable categories with documented exit codes:

- Usage/configuration.
- Authentication or access.
- Repository/revision/metadata.
- Destination or disk capacity.
- Downloader failure.
- Monitor/render failure.
- Integrity failure.
- User cancellation.

Human output includes a concise cause and a corrective action. JSON output includes a stable category, code, message, and safe contextual fields. Secrets, authorization headers, and raw tokens are always excluded.

## Distribution and ARM64

The project remains installable as a normal Python package on supported Python versions. Release automation also produces standalone artifacts where the packaging tool supports the target reliably.

The release matrix covers:

- Windows x86-64 and ARM64.
- Linux x86-64 and ARM64.
- macOS x86-64 and ARM64.

Native hosted runners are preferred. Where GitHub does not provide a suitable native runner, an explicitly documented build strategy may use supported emulation or an external builder, but an emulated smoke test alone is not represented as a native validation. Artifact names include OS and architecture. Releases publish SHA-256 checksum files and installation guidance per platform.

Architecture-independent source and wheel distributions remain the portability fallback. Platform detection must never assume x86-64, and renderer behavior must not depend on architecture.

## Testing

The implementation uses test-driven changes and adds coverage for:

- Branch/tag-to-commit resolution and propagation to every downstream API/command.
- Public, private, gated, missing, unauthorized, and malformed repository responses.
- Disk calculations, margins, partial files, unwritable destinations, and insufficient space.
- Correct hashes, mismatches, absent hashes, cache invalidation, and large-file non-blocking behavior.
- Child cleanup after observer, renderer, and unexpected failures.
- Forced final observation and post-exit integrity failure.
- Narrow, normal, and wide layouts; runtime resizing; ASCII; no-color; and reduced motion.
- Plain-text and versioned JSON contracts.
- Package installation and CLI smoke tests on the OS/architecture matrix.
- Docker end-to-end simulation in which a real child writes files incrementally while the application monitors them.

High-risk end-to-end scenarios are repeated four clean times before release. CI artifacts and logs must be sufficient to diagnose architecture-specific failures.

## Documentation

The manual and README will explain:

- Installation from Python packages and standalone artifacts.
- ARM64 availability and any platform limitations.
- Authentication and gated-model access.
- Interactive controls and layout modes.
- JSON/plain-text automation usage.
- Integrity terminology and the difference between complete and verified.
- Disk preflight behavior, exit codes, cancellation, and troubleshooting.

## Delivery sequence

1. Introduce error categories and structured repository metadata.
2. Add immutable resolution and complete preflight.
3. Add integrity states and bounded SHA-256 verification.
4. Harden process cleanup and forced final observation.
5. Refactor renderer inputs into immutable snapshots.
6. Implement Adaptive Focus and fallback renderers.
7. Expand tests, documentation, ARM64 CI, packaging, and release checks.

Each stage must keep existing supported CLI workflows operational or intentionally migrate them with documented tests and release notes.

## Acceptance criteria

- No downloader starts before all mandatory preflight checks pass.
- Every download uses a full resolved commit SHA.
- Digest-bearing files are not complete until SHA-256 verification succeeds.
- The child is not left running after monitor failure or cancellation.
- A forced post-exit observation determines the final result.
- The dashboard remains coherent across narrow, normal, and wide terminals and has safe non-interactive fallbacks.
- ARM64 artifacts or an explicitly documented package fallback are validated for every promised platform.
- Unit, integration, simulated-download, packaging, and CI checks pass without unresolved warnings attributable to this milestone.
