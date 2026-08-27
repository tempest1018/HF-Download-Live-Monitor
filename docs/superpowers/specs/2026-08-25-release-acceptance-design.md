# Published Release Acceptance Design

## Purpose

HF Download Live Monitor needs repeatable evidence that a public GitHub release works as
distributed, rather than merely proving that its source tree and build jobs pass. Release
acceptance therefore downloads immutable public assets, verifies their identity and
provenance, runs them on their native architectures, and records auditable results without
modifying the release.

The first target is `v0.1.0`. The mechanism must remain reusable for later stable tags.
PyPI publication is explicitly outside this scope.

## Entry point and authority

A manual `Release Acceptance` GitHub Actions workflow accepts one required `tag` input.
The input must match `vMAJOR.MINOR.PATCH`. The workflow has read-only repository and
attestation permissions and must never edit releases, tags, repository settings, or
packages.

Before platform testing begins, a validation job checks that:

- the requested tag exists and has a valid GPG signature under `SIGNING_KEY.asc`;
- the corresponding GitHub release is public and not a prerelease;
- the release tag exactly equals the requested tag;
- all 15 expected assets download into an initially empty flat directory;
- `scripts/validate_release_bundle.py` accepts the exact inventory, embedded package
  versions, adjacent checksums, and aggregate `SHA256SUMS`; and
- GitHub artifact attestations validate for the six executables, wheel, and source archive.

Failure stops the workflow before native execution. The validated complete bundle is
uploaded as a short-lived workflow artifact for downstream jobs, avoiding inconsistent
redownloads within one acceptance run.

`scripts/validate_release_bundle.py` is the single authoritative, versioned asset
contract. It defines six architecture-labelled executables, the adjacent `.sha256`
sidecar for each executable, one version-matched wheel, one version-matched source
archive, and `SHA256SUMS`: 15 files in total. The validator must reject both missing and
unexpected files. The workflow and documentation consume this contract rather than
maintaining a second asset manifest.

## Native executable matrix

Six matrix entries cover the published architectures:

- Windows x86_64 on `windows-latest`;
- Windows ARM64 on `windows-11-arm`;
- Linux x86_64 on `ubuntu-latest`;
- Linux ARM64 on `ubuntu-24.04-arm`;
- macOS x86_64 on `macos-15-intel`; and
- macOS ARM64 on `macos-15`.

Each job downloads the validated bundle artifact, selects only its architecture-labelled
executable, asserts the runner architecture, and verifies the executable SHA-256 against
both its adjacent checksum and `SHA256SUMS`. Unix executables receive execute permission
after download because workflow-artifact transport does not preserve it.

The executable is exercised through `--help`, `watch --help`, `attach --help`, and
`run --help`. A deterministic incremental-download fixture then drives the published
binary through an end-to-end monitor run in a temporary path containing both spaces and
Unicode. The test requires visible progress, successful final reconciliation, JSON output
that parses without stdout contamination, and the documented success exit code. Progress
and downloader diagnostics must use `stderr` or another explicitly separate channel;
`stdout` must contain only the JSON document in JSON mode. No Hugging Face credentials or
external model download are needed in the cross-platform matrix.

## Published wheel acceptance

A separate clean Python job creates a new virtual environment and installs the wheel from
the validated public bundle with normal dependency resolution. It must not install the
repository package. The installed console command is exercised through the same help
surface and a deterministic end-to-end simulated download. A package import/version check
must report the tag version and prove that the imported module resides beneath that
environment's `site-packages` directory. This catches missing package data, undeclared
dependencies, broken console entry points, and accidental source-tree imports.

The checkout remains available only for the fixture and acceptance assertions. Commands
under test run from a fresh temporary directory outside the checkout with `PYTHONPATH`
removed, and invoke the virtual environment's interpreter or console script by absolute
path. The installed application under test always comes from the public wheel.

## Local Windows acceptance

The Windows x86_64 asset is also downloaded directly from the public release into a unique
temporary directory on this PC. Local acceptance verifies the signed tag, checksums, and
attestation again, then tests:

- the standalone help and command surfaces;
- deterministic incremental monitoring in paths with spaces and Unicode;
- structured JSON output and stable exit behavior;
- cancellation and child cleanup; and
- the interactive Adaptive Focus dashboard in the current terminal.

A small public Hugging Face repository may be used for a genuine network download only
after deterministic tests pass. It must be pinned to an immutable revision, require no
token, remain small, and write only inside a temporary directory. Network unavailability
is reported separately from product failure; it does not weaken deterministic acceptance.

## Docker acceptance

Docker provides an additional Linux x86_64 consumer environment. The container receives
the published Linux executable and deterministic fixture as mounted read-only inputs,
runs the end-to-end simulation in a clean writable directory, and emits machine-readable
results. The image does not build or install the local project as the application under
test.

If Docker is unavailable locally, the native Linux GitHub job remains authoritative and
the missing local Docker evidence is reported explicitly rather than silently skipped.

## Reports and failure handling

Every job writes a compact text or JSON report containing the tag, asset name, runner OS,
runner architecture, checksum result, commands exercised, exit codes, and final outcome.
Reports contain no tokens, authorization headers, home-directory inventory, or unrelated
environment data. GitHub uploads reports even when a test fails.

The workflow summary identifies each platform as passed, failed, or unavailable. A
failure never mutates `v0.1.0`; corrections are developed and released as `v0.1.1`.
Acceptance succeeds only when validation, all six native jobs, and the clean-wheel job
pass. Local interactive, network, and Docker evidence is recorded alongside the GitHub
run but does not alter historical workflow results.

## Repository changes and tests

Implementation is limited to a manual workflow, focused acceptance helpers or fixtures,
workflow contract tests, and concise operator documentation. Tests must lock down:

- manual-only triggering and stable-tag input;
- read-only permissions and absence of release/package mutation commands;
- all six exact runner/artifact pairs;
- validation-before-execution dependencies;
- public-release, signature, checksum, inventory, and attestation gates;
- the shared validator as the only asset manifest, including rejection of an empty,
  incomplete, or extra-file bundle;
- clean wheel installation rather than editable/source installation, execution outside
  the checkout with no `PYTHONPATH`, and a `site-packages` origin assertion;
- deterministic simulation, Unicode/spaced paths, JSON parsing, and report upload; and
- strict JSON-only `stdout` with progress and diagnostics separated from it; and
- the explicit absence of PyPI publication.

The full Python test suite, Ruff formatting and lint, Pyright, workflow tests, local
published-asset tests, Docker simulation when available, and the dispatched GitHub matrix
must pass before acceptance is reported complete.
