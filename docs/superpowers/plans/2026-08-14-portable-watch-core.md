# Portable Watch Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a packaged, tested `hf-download-live-monitor watch` application that accurately monitors explicit Hugging Face model, dataset, or Space downloads on Windows, WSL, Linux, and macOS with interactive, plain, JSON, and JSONL output.

**Architecture:** Normalize CLI input into immutable domain models, obtain and filter a repository manifest through public Hugging Face APIs, observe local files through an isolated compatibility layer, and feed deterministic observations into a state engine. Renderers consume immutable snapshots and never access processes, the network, or arbitrary filesystem paths.

**Tech Stack:** Python 3.10+, `huggingface_hub`, Typer, Rich, pytest, Ruff, Pyright, Hatchling, GitHub Actions.

---

## Delivery decomposition

This is the first of three implementation plans:

1. Portable core and explicit `watch` mode (this plan).
2. Native Windows/POSIX `attach` process discovery and multiple-download orchestration.
3. Managed `run` mode, packaging hardening, standalone executables, and public release automation.

Each phase leaves the repository usable and fully tested. Git commit steps are intentionally omitted until repository author identity and workflow are chosen.

## File map

- `pyproject.toml`: build metadata, dependencies, command entry point, and tool configuration.
- `src/hf_download_live_monitor/__init__.py`: public package version access.
- `src/hf_download_live_monitor/__main__.py`: `python -m hf_download_live_monitor` entry point.
- `src/hf_download_live_monitor/cli.py`: Typer command surface and exit behavior.
- `src/hf_download_live_monitor/models.py`: immutable specifications, observations, states, snapshots, and errors.
- `src/hf_download_live_monitor/security.py`: secret redaction and contained path resolution.
- `src/hf_download_live_monitor/selection.py`: repository filename and glob selection rules.
- `src/hf_download_live_monitor/repository.py`: public Hub metadata adapter.
- `src/hf_download_live_monitor/compat.py`: local-dir cache-path compatibility.
- `src/hf_download_live_monitor/filesystem.py`: race-resistant filesystem observation.
- `src/hf_download_live_monitor/engine.py`: deterministic rates, ETAs, states, and aggregate calculations.
- `src/hf_download_live_monitor/renderers.py`: interactive, plain, JSON, and JSONL rendering.
- `src/hf_download_live_monitor/app.py`: watch-loop orchestration and retry policy.
- `tests/`: focused unit and integration tests mirroring package responsibilities.
- `README.md`, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`: distribution documentation.
- `.github/workflows/ci.yml`: cross-platform validation and package smoke tests.

### Task 1: Establish the installable package

**Files:**
- Create: `pyproject.toml`
- Create: `src/hf_download_live_monitor/__init__.py`
- Create: `src/hf_download_live_monitor/__main__.py`
- Create: `src/hf_download_live_monitor/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write a failing CLI smoke test**

```python
from typer.testing import CliRunner

from hf_download_live_monitor.cli import cli


runner = CliRunner()


def test_help_exposes_watch_command() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "watch" in result.stdout
```

- [ ] **Step 2: Run the test and confirm the package does not exist**

Run: `python -m pytest tests/test_cli.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'hf_download_live_monitor'`.

- [ ] **Step 3: Add package metadata and the minimal command**

Define Hatchling metadata in `pyproject.toml`, require Python `>=3.10`, add runtime dependencies `huggingface-hub>=0.27,<2`, `rich>=13.9,<15`, `typer>=0.15,<1`, and expose `hf-download-live-monitor = "hf_download_live_monitor.cli:run"`. Add test, lint, typing, and build dependencies under a `dev` optional dependency. Configure pytest with `pythonpath = ["src"]`, Ruff for Python 3.10, and Pyright for strict checking of `src/hf_download_live_monitor`.

Implement this initial command surface:

```python
import typer

cli = typer.Typer(no_args_is_help=True, help="Monitor Hugging Face downloads.")


@cli.command()
def watch(repo: str, local_dir: str = typer.Option(..., "--local-dir")) -> None:
    """Watch an explicit Hugging Face local directory."""
    typer.echo(f"Watching {repo} in {local_dir}")


def run() -> None:
    cli()
```

Make `__main__.py` call `run()` and expose a static development version from `__init__.py` using `importlib.metadata` with a `PackageNotFoundError` fallback.

- [ ] **Step 4: Install development dependencies and run the smoke test**

Run: `python -m pip install -e ".[dev]"` followed by `python -m pytest tests/test_cli.py -v`.

Expected: one passing test and `hf-download-live-monitor --help` lists `watch`.

### Task 2: Define stable domain models

**Files:**
- Create: `src/hf_download_live_monitor/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write tests for normalized immutable models**

Test that `RepoType.parse("dataset")` returns `RepoType.DATASET`, invalid values raise `ValueError`, `DownloadSpec` rejects an empty repository, and frozen `FileObservation` instances cannot be mutated. Construct the models with explicit keyword arguments so the public field names are fixed by the tests.

- [ ] **Step 2: Run the focused tests and verify import failure**

Run: `python -m pytest tests/test_models.py -v`

Expected: collection fails because `hf_download_live_monitor.models` does not exist.

- [ ] **Step 3: Implement the domain types**

Create frozen, slotted dataclasses and string enums:

```python
class RepoType(str, Enum):
    MODEL = "model"
    DATASET = "dataset"
    SPACE = "space"


class FileState(str, Enum):
    QUEUED = "queued"
    MEASURING = "measuring"
    DOWNLOADING = "downloading"
    WAITING = "waiting"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    INCONSISTENT = "inconsistent"


@dataclass(frozen=True, slots=True)
class DownloadSpec:
    repo: str
    local_dir: Path
    repo_type: RepoType = RepoType.MODEL
    revision: str = "main"
    filenames: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
```

Also define `ManifestFile`, `FileObservation`, `FileProgress`, `ProgressSnapshot`, and `MonitorError` with explicit byte counts, monotonic timestamps, optional rate/ETA fields, and stable error codes. Validate non-negative byte counts and non-empty identifiers in `__post_init__`.

- [ ] **Step 4: Run model tests and static typing**

Run: `python -m pytest tests/test_models.py -v` and `python -m pyright src/hf_download_live_monitor/models.py`.

Expected: all tests pass and Pyright reports zero errors.

### Task 3: Add credential redaction and safe path resolution

**Files:**
- Create: `src/hf_download_live_monitor/security.py`
- Create: `tests/test_security.py`

- [ ] **Step 1: Write security tests**

Cover `--token secret`, `--token=secret`, `HF_TOKEN=secret`, bearer authorization values, query-string tokens, paths containing `..`, absolute repository paths, sibling-prefix tricks such as `download-other`, and a valid nested filename. Assert that no returned or formatted value contains the original secret.

- [ ] **Step 2: Verify the focused tests fail**

Run: `python -m pytest tests/test_security.py -v`

Expected: collection fails because the security functions do not exist.

- [ ] **Step 3: Implement secure helpers**

Implement `redact_args(args: Sequence[str]) -> tuple[str, ...]`, `redact_text(value: str) -> str`, and `resolve_repo_path(root: Path, filename: str) -> Path`. Normalize repository separators, reject absolute and parent-traversing paths, resolve without requiring the target to exist, and verify containment with `Path.is_relative_to`.

- [ ] **Step 4: Run security tests and Ruff**

Run: `python -m pytest tests/test_security.py -v` and `python -m ruff check src/hf_download_live_monitor/security.py tests/test_security.py`.

Expected: all cases pass with no lint findings.

### Task 4: Implement requested-file selection

**Files:**
- Create: `src/hf_download_live_monitor/selection.py`
- Create: `tests/test_selection.py`

- [ ] **Step 1: Write selection tests**

Use a fixed manifest containing `README.md`, `config.json`, `weights/a.bin`, and `weights/b.safetensors`. Test full selection, explicit filenames, include-only patterns, exclude-only patterns, includes followed by excludes, forward-slash normalization, stable manifest ordering, and an explicit filename absent from the manifest.

- [ ] **Step 2: Run the tests and observe missing implementation**

Run: `python -m pytest tests/test_selection.py -v`

Expected: import failure for `select_manifest`.

- [ ] **Step 3: Implement deterministic selection**

Implement:

```python
def select_manifest(
    manifest: Sequence[ManifestFile],
    *,
    filenames: Sequence[str] = (),
    includes: Sequence[str] = (),
    excludes: Sequence[str] = (),
) -> tuple[ManifestFile, ...]:
```

Use `fnmatch.fnmatchcase` on normalized POSIX repository paths. Explicit filenames form the initial set when present; include patterns further constrain it; exclude patterns always win. Raise a classified `MonitorError` listing requested filenames absent from metadata.

- [ ] **Step 4: Run focused and model tests**

Run: `python -m pytest tests/test_selection.py tests/test_models.py -v`.

Expected: all tests pass.

### Task 5: Add the public Hugging Face metadata adapter

**Files:**
- Create: `src/hf_download_live_monitor/repository.py`
- Create: `tests/test_repository.py`

- [ ] **Step 1: Write adapter tests with a fake API**

Provide fake sibling objects covering direct `size`, dictionary LFS size, object LFS size, and missing size. Assert the correct API method is selected for model, dataset, and Space repositories; revision and `files_metadata=True` are forwarded; paths and sizes become `ManifestFile` values; missing metadata raises `metadata_unavailable`; authentication and repository exceptions retain classified codes without tokens.

- [ ] **Step 2: Verify tests fail without the adapter**

Run: `python -m pytest tests/test_repository.py -v`.

Expected: import failure for `HubRepository`.

- [ ] **Step 3: Implement the injected API adapter**

Create `HubRepository(api: HfApi | None = None)` with `manifest(spec: DownloadSpec) -> tuple[ManifestFile, ...]`. Dispatch to `model_info`, `dataset_info`, or `space_info`; extract size defensively; sort by filename; pass results through `select_manifest`; translate known `huggingface_hub` HTTP errors into stable `MonitorError` codes.

- [ ] **Step 4: Run adapter tests without network access**

Run: `python -m pytest tests/test_repository.py -v`.

Expected: all tests pass and no test performs an HTTP request.

### Task 6: Isolate cache compatibility and filesystem observation

**Files:**
- Create: `src/hf_download_live_monitor/compat.py`
- Create: `src/hf_download_live_monitor/filesystem.py`
- Create: `tests/test_compat.py`
- Create: `tests/test_filesystem.py`

- [ ] **Step 1: Write cache-path and observation tests**

Build temporary local-dir trees containing nested final files and short-hash incomplete files. Cover missing, partial, complete, zero-byte, undersized-final, multiple-incomplete, disappearing-during-stat, and traversal cases. Assert the newest valid incomplete file is selected and no exception escapes a race.

- [ ] **Step 2: Run tests and confirm missing modules**

Run: `python -m pytest tests/test_compat.py tests/test_filesystem.py -v`.

Expected: collection fails for the new modules.

- [ ] **Step 3: Implement cache compatibility**

Expose `incomplete_candidates(local_dir: Path, filename: str) -> tuple[Path, ...]`. Reproduce the official URL-safe SHA-1 short name for `<filename>.metadata`, translate POSIX repository paths to native components, and search only the corresponding metadata directory. Keep this convention in one module and document the compatible `huggingface_hub` range.

- [ ] **Step 4: Implement one-pass safe observation**

Create `FileSystemObserver.observe(spec, manifest, now) -> tuple[FileObservation, ...]`. Index `.incomplete` files beneath `.cache/huggingface/download` once per call, resolve every final path through `resolve_repo_path`, catch expected `OSError` races, and emit immutable observations containing expected, final, partial, and visible byte counts.

- [ ] **Step 5: Run filesystem tests and verify no writes outside pytest temp directories**

Run: `python -m pytest tests/test_compat.py tests/test_filesystem.py -v`.

Expected: all tests pass.

### Task 7: Build the deterministic progress engine

**Files:**
- Create: `src/hf_download_live_monitor/engine.py`
- Create: `tests/test_engine.py`

- [ ] **Step 1: Write state, rate, and ETA tests**

Drive `ProgressEngine` with explicit timestamps and observations. Cover queued, measuring, downloading, waiting, finalizing, complete, inconsistent final size, resumed initial bytes, decreasing bytes after retry, bounded histories, aggregate rate calculated from aggregate deltas, ETA suppression before stable samples, and history removal after files leave the manifest.

- [ ] **Step 2: Verify the engine tests fail**

Run: `python -m pytest tests/test_engine.py -v`

Expected: import failure for `ProgressEngine`.

- [ ] **Step 3: Implement the state engine**

Create `ProgressEngine(rate_window: float = 2.0, measuring_window: float = 0.75)` with `update(spec, manifest, observations, now) -> ProgressSnapshot`. Store bounded deques keyed by repository filename. Treat the first sample as baseline, clamp negative deltas to zero, use the oldest sample inside the rolling window, and produce an ETA only for positive stable rates. Mark final files complete only at exact expected size; mark other final sizes inconsistent.

- [ ] **Step 4: Run engine tests and full unit suite**

Run: `python -m pytest tests/test_engine.py -v` followed by `python -m pytest -q`.

Expected: all tests pass.

### Task 8: Implement stable structured and plain renderers

**Files:**
- Create: `src/hf_download_live_monitor/renderers.py`
- Create: `tests/test_renderers.py`

- [ ] **Step 1: Write renderer contract tests**

Construct a fixed `ProgressSnapshot`. Assert plain output has no ANSI escapes, renders ASCII when requested, fits representative widths, and includes totals and classified errors. Assert JSON has `schema_version: 1`, stable enum strings, integer byte counts, nullable rates/ETAs, and no secret fields. Assert JSONL emits one valid JSON object per event line.

- [ ] **Step 2: Confirm renderer tests fail**

Run: `python -m pytest tests/test_renderers.py -v`

Expected: import failure for renderer classes.

- [ ] **Step 3: Implement renderer interfaces**

Define a `Renderer` protocol with `render(snapshot: ProgressSnapshot) -> None`. Implement `PlainRenderer`, `JsonRenderer`, `JsonLinesRenderer`, and `RichRenderer`. Use Rich `Live` and `Table` only inside `RichRenderer`; build structured dictionaries through a shared `snapshot_to_dict` function to prevent schema drift.

- [ ] **Step 4: Run renderer tests under UTF-8 and forced ASCII**

Run: `python -m pytest tests/test_renderers.py -v`.

Expected: all renderer contracts pass.

### Task 9: Orchestrate watch mode and retry behavior

**Files:**
- Create: `src/hf_download_live_monitor/app.py`
- Create: `tests/test_app.py`
- Modify: `src/hf_download_live_monitor/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write orchestration tests with injected dependencies**

Use fake repository, observer, renderer, clock, and sleeper objects. Test `--once`, repeated refreshes, Ctrl+C, metadata retry at 1/2/4 seconds capped at 30 seconds, continued local rendering after transient metadata failure when a cached manifest exists, and clean classified failure when no manifest has ever been available.

- [ ] **Step 2: Write CLI tests for the complete watch surface**

Cover repository type, revision, repeated filename/include/exclude options, refresh and rate-window validation, `--plain`, `--json`, `--jsonl`, `--once`, `--no-color`, `--ascii`, verbosity, automatic plain output when the terminal is not interactive, and incompatible renderer flags.

- [ ] **Step 3: Run focused tests and confirm failures**

Run: `python -m pytest tests/test_app.py tests/test_cli.py -v`.

Expected: failures identify the missing watch application and options.

- [ ] **Step 4: Implement `WatchApplication`**

Inject repository, observer, engine, renderer, monotonic clock, and sleeper. Load the manifest, perform observations, render snapshots, use bounded exponential retry with injectable jitter, and return stable exit codes. Ensure `KeyboardInterrupt` exits cleanly and closes Rich live output through a context manager.

- [ ] **Step 5: Wire CLI validation and renderer selection**

Convert CLI values to `DownloadSpec`, validate positive timing options, select structured output only when requested, otherwise choose Rich for interactive output and plain text for redirected output. Translate `MonitorError` into safe messages and documented exit codes without a traceback unless verbose diagnostics are enabled.

- [ ] **Step 6: Run orchestration, CLI, and complete tests**

Run: `python -m pytest tests/test_app.py tests/test_cli.py -v` followed by `python -m pytest -q`.

Expected: all tests pass.

### Task 10: Add distribution documentation and policies

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `CHANGELOG.md`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `.gitignore`
- Create: `docs/json-schema.md`
- Create: `docs/architecture.md`
- Create: `tests/test_docs.py`

- [ ] **Step 1: Write documentation integrity tests**

Assert every documented CLI invocation succeeds under `CliRunner`, every referenced local documentation file exists, the package name and supported Python version match `pyproject.toml`, and the JSON example validates against the renderer contract.

- [ ] **Step 2: Run documentation tests and observe missing files**

Run: `python -m pytest tests/test_docs.py -v`

Expected: failures list the absent distribution documents.

- [ ] **Step 3: Write complete user and contributor documentation**

Document installation through pipx, uv, and pip; model/dataset/Space examples; explicit filenames and filters; output modes; platform behavior; privacy; exit codes; troubleshooting; development setup; architecture boundaries; structured schema versioning; and the roadmap for attach and run modes. Use the standard MIT license text with year 2026 and copyright holder `HF Download Live Monitor contributors`.

- [ ] **Step 4: Add safe repository exclusions**

Ignore Python caches, virtual environments, build outputs, coverage, type-checker caches, editor metadata, local environment files, logs, generated recordings, and standalone binaries. Do not ignore source fixtures or lock files categorically.

- [ ] **Step 5: Run documentation and full tests**

Run: `python -m pytest tests/test_docs.py -v` followed by `python -m pytest -q`.

Expected: all documentation contracts and tests pass.

### Task 11: Add cross-platform CI and package validation

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `tests/test_package.py`

- [ ] **Step 1: Add a package metadata test**

Assert the distribution exposes `hf-download-live-monitor`, required files are included in the source distribution, and imports do not perform network or filesystem writes.

- [ ] **Step 2: Implement the CI workflow**

Create jobs for Ruff and Pyright plus a test matrix for Python 3.10 through 3.13 on Windows and Ubuntu, with a macOS smoke job. Build wheel and source distributions once, run `twine check`, install each artifact into a clean environment, execute `hf-download-live-monitor --help`, and upload artifacts only after all validation succeeds. Grant workflow contents read-only permissions.

- [ ] **Step 3: Run every local quality gate**

Run:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m pyright
python -m pytest -q
python -m build
python -m twine check dist\*
```

Expected: every command exits zero.

- [ ] **Step 4: Test the built artifact in an isolated environment**

Create a temporary virtual environment outside the source tree, install the built wheel, run `hf-download-live-monitor --help`, run a JSON `--once` invocation against a fake-adapter integration fixture, then remove only that verified temporary environment.

Expected: installed-command behavior matches source-tree tests and the repository remains free of generated runtime data other than ignored `dist` and tool caches.

### Task 12: Retire the prototype without losing provenance

**Files:**
- Move: `hf_live_file_monitor.py` to `docs/prototypes/hf_live_file_monitor.py`
- Move: `hf_live_file_monitor_checksum.txt` to `docs/prototypes/hf_live_file_monitor_checksum.txt`
- Modify: `README.md`
- Create: `tests/test_prototype_checksum.py`

- [ ] **Step 1: Preserve and verify the original artifact**

Write a test that reads the preserved checksum, hashes the prototype bytes with SHA-256, and asserts the lowercase digest matches. This documents provenance without exposing the legacy script as the supported command.

- [ ] **Step 2: Move the two original files with their contents unchanged**

Use filesystem moves scoped to the two exact files. Update the README to label them historical and direct all users to `hf-download-live-monitor`.

- [ ] **Step 3: Run the provenance and complete validation suites**

Run: `python -m pytest tests/test_prototype_checksum.py -v`, then all quality-gate commands from Task 11.

Expected: checksum preservation passes and every project validation command exits zero.

## Completion criteria

The phase is complete only when the installed `hf-download-live-monitor watch` command works from a clean wheel installation; models, datasets, Spaces, revisions, explicit files, include/exclude filters, resumptions, partials, finalization, and inconsistent files are covered by deterministic tests; interactive, plain, JSON, and JSONL output contracts pass; credential and containment tests pass; and Windows, Linux, and macOS CI definitions cover the advertised explicit-watch support.
