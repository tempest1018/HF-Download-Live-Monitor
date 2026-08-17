# Safety, Adaptive TUI, and ARM64 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make downloads revision-pinned, preflighted, integrity-verified, safely supervised, visually engaging in flexible terminals, and distributable on x86-64 and ARM64.

**Architecture:** Repository preflight produces a resolved `DownloadPlan`; filesystem observations feed a bounded integrity verifier and immutable progress snapshots; the runner owns the child lifecycle and forces final reconciliation. Presentation remains downstream of snapshots, with Adaptive Focus for interactive terminals and stable plain/JSON fallbacks.

**Tech Stack:** Python 3.10+, dataclasses, huggingface_hub, Rich, Typer, pytest, Ruff, Pyright, PyInstaller, Docker, GitHub Actions.

---

## File map

- Create `src/hf_download_live_monitor/errors.py`: stable error categories and exit-code mapping.
- Create `src/hf_download_live_monitor/preflight.py`: destination and disk-capacity validation.
- Create `src/hf_download_live_monitor/integrity.py`: bounded SHA-256 scheduling and cache.
- Create `src/hf_download_live_monitor/layout.py`: terminal-size/view-mode decisions without rendering side effects.
- Create `src/hf_download_live_monitor/controls.py`: optional non-blocking interactive key actions and display state.
- Modify `src/hf_download_live_monitor/models.py`: resolved plans, digests, verification states, and snapshot metadata.
- Modify `src/hf_download_live_monitor/repository.py`: authenticated resolution and LFS metadata extraction.
- Modify `src/hf_download_live_monitor/filesystem.py`: stable file identity metadata.
- Modify `src/hf_download_live_monitor/engine.py`: explicit integrity state and throughput history.
- Modify `src/hf_download_live_monitor/app.py`: accept preflight plans and force final observation.
- Modify `src/hf_download_live_monitor/runner.py`: guaranteed terminate/kill/reap cleanup.
- Modify `src/hf_download_live_monitor/renderers.py`: Adaptive Focus, compact/plain, and JSON schema v2.
- Modify `src/hf_download_live_monitor/cli.py`: display/access controls and stable exit codes.
- Create `tests/test_errors.py`, `tests/test_preflight.py`, `tests/test_integrity.py`, and `tests/test_layout.py`.
- Create `tests/test_controls.py` for view/detail/event/help/cancellation key behavior and safe fallback.
- Modify focused existing tests matching each changed module.
- Create `tests/integration/test_simulated_download.py` and `tests/fixtures/incremental_downloader.py`.
- Create `Dockerfile.test` and `scripts/run_container_simulation.py` for reproducible end-to-end validation.
- Modify `.github/workflows/ci.yml`, `.github/workflows/release.yml`, and `scripts/build_standalone.py` for architecture-labelled builds.
- Modify `README.md`, `docs/user-manual.md`, `docs/architecture.md`, `docs/json-schema.md`, and `CHANGELOG.md`.

### Task 1: Stable error contract

**Files:**
- Create: `src/hf_download_live_monitor/errors.py`
- Modify: `src/hf_download_live_monitor/models.py`
- Create: `tests/test_errors.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing taxonomy tests**

```python
from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import MonitorError


def test_each_public_error_category_has_a_distinct_nonzero_exit_code() -> None:
    codes = {exit_code_for(category) for category in ErrorCategory}
    assert 0 not in codes
    assert len(codes) == len(ErrorCategory)


def test_monitor_error_serializes_only_safe_fields() -> None:
    error = MonitorError("token_invalid", "authentication failed", category=ErrorCategory.ACCESS)
    assert error.to_dict() == {
        "category": "access",
        "code": "token_invalid",
        "message": "authentication failed",
        "recoverable": False,
    }
```

- [ ] **Step 2: Confirm the tests fail**

Run: `python -m pytest tests/test_errors.py tests/test_models.py -q`
Expected: FAIL because `errors.py`, `ErrorCategory`, and `MonitorError.to_dict` do not exist.

- [ ] **Step 3: Implement the public categories and mapping**

```python
class ErrorCategory(str, Enum):
    USAGE = "usage"
    ACCESS = "access"
    REPOSITORY = "repository"
    DESTINATION = "destination"
    DOWNLOADER = "downloader"
    MONITOR = "monitor"
    INTEGRITY = "integrity"
    CANCELLED = "cancelled"


_EXIT_CODES = {category: index for index, category in enumerate(ErrorCategory, start=2)}


def exit_code_for(category: ErrorCategory) -> int:
    return _EXIT_CODES[category]
```

Add `category: ErrorCategory = ErrorCategory.MONITOR` and `to_dict()` to `MonitorError`; keep the existing `code`, `message`, and `recoverable` API.

- [ ] **Step 4: Run focused validation**

Run: `python -m pytest tests/test_errors.py tests/test_models.py -q && python -m pyright`
Expected: PASS with no type errors.

- [ ] **Step 5: Commit**

```powershell
git add src/hf_download_live_monitor/errors.py src/hf_download_live_monitor/models.py tests/test_errors.py tests/test_models.py
git commit -m "feat: define stable monitor error contract"
```

### Task 2: Immutable repository resolution and LFS metadata

**Files:**
- Modify: `src/hf_download_live_monitor/models.py`
- Modify: `src/hf_download_live_monitor/repository.py`
- Modify: `tests/test_repository.py`

- [ ] **Step 1: Write failing resolution tests**

```python
def test_prepare_resolves_revision_and_extracts_lfs_sha256(tmp_path: Path) -> None:
    api = FakeApi(
        sha="a" * 40,
        siblings=[Sibling("model.bin", 12, {"size": 12, "sha256": "b" * 64})],
    )
    requested = DownloadSpec("owner/repo", tmp_path, revision="main")
    plan = HubRepository(api).prepare(requested)
    assert plan.requested_revision == "main"
    assert plan.spec.revision == "a" * 40
    assert plan.manifest == (ManifestFile("model.bin", 12, sha256="b" * 64),)
    assert api.calls == [("owner/repo", "main", True)]


def test_prepare_classifies_gated_access() -> None:
    with pytest.raises(MonitorError) as caught:
        HubRepository(RaisingApi(GatedRepoError("approval required"))).prepare(SPEC)
    assert caught.value.category is ErrorCategory.ACCESS
    assert caught.value.code == "gated_repository"
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_repository.py -q`
Expected: FAIL because `prepare`, `DownloadPlan`, and `ManifestFile.sha256` are absent.

- [ ] **Step 3: Add resolved plan models and repository preparation**

```python
@dataclass(frozen=True, slots=True)
class DownloadPlan:
    spec: DownloadSpec
    requested_revision: str
    manifest: tuple[ManifestFile, ...]


@dataclass(frozen=True, slots=True)
class ManifestFile:
    filename: str
    expected_bytes: int
    sha256: str | None = None
```

Implement `HubRepository.prepare(spec)` with one `files_metadata=True` info call, require a 40-character hexadecimal `info.sha`, create a new spec using `dataclasses.replace(spec, revision=info.sha)`, extract `lfs.sha256` only when it is 64 hexadecimal characters, apply `select_manifest`, and map gated/401/403 to `ACCESS`, missing/revision/metadata errors to `REPOSITORY`, and 429/5xx to recoverable repository errors. Retain `manifest(spec)` as a compatibility wrapper returning `prepare(spec).manifest` until callers migrate.

- [ ] **Step 4: Validate focused behavior**

Run: `python -m pytest tests/test_repository.py tests/test_selection.py -q && python -m pyright`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/hf_download_live_monitor/models.py src/hf_download_live_monitor/repository.py tests/test_repository.py
git commit -m "feat: pin repository plans to immutable revisions"
```

### Task 3: Destination and disk-space preflight

**Files:**
- Create: `src/hf_download_live_monitor/preflight.py`
- Create: `tests/test_preflight.py`
- Modify: `src/hf_download_live_monitor/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing capacity tests**

```python
def test_preflight_rejects_insufficient_disk_without_starting(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, expected=(1_000, 2_000))
    with pytest.raises(MonitorError) as caught:
        validate_destination(plan, disk_usage=lambda _: Usage(total=9_000, used=8_000, free=1_000))
    assert caught.value.category is ErrorCategory.DESTINATION
    assert caught.value.code == "insufficient_disk_space"
    assert "required=3300" in caught.value.message


def test_existing_exact_files_reduce_required_bytes(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, expected=(1_000, 2_000))
    (tmp_path / "first.bin").write_bytes(b"x" * 1_000)
    result = validate_destination(plan, reserve_ratio=0.10, disk_usage=fake_usage(2_300))
    assert result.required_bytes == 2_200
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_preflight.py -q`
Expected: FAIL because `preflight.py` is absent.

- [ ] **Step 3: Implement deterministic preflight**

```python
@dataclass(frozen=True, slots=True)
class PreflightResult:
    required_bytes: int
    available_bytes: int
    reserve_bytes: int


def validate_destination(
    plan: DownloadPlan,
    *,
    reserve_ratio: float = 0.10,
    disk_usage: Callable[[Path], Usage] = shutil.disk_usage,
) -> PreflightResult:
    plan.spec.local_dir.mkdir(parents=True, exist_ok=True)
    probe = plan.spec.local_dir / ".hf-download-live-monitor-write-test"
    try:
        probe.touch(exist_ok=False)
        probe.unlink()
    except OSError as exc:
        raise MonitorError(
            "destination_unwritable", redact_text(str(exc)), category=ErrorCategory.DESTINATION
        ) from exc
    remaining = sum(_remaining_bytes(plan.spec.local_dir, item) for item in plan.manifest)
    reserve = math.ceil(remaining * reserve_ratio)
    available = disk_usage(plan.spec.local_dir).free
    required = remaining + reserve
    if available < required:
        raise MonitorError(
            "insufficient_disk_space",
            f"required={required} available={available} destination={plan.spec.local_dir}",
            category=ErrorCategory.DESTINATION,
        )
    return PreflightResult(required, available, reserve)
```

Credit an existing final file only when its size is exactly expected; give no credit to oversized or uncertain partial files. Call this preflight from `run_download` before constructing `ManagedDownload`.

- [ ] **Step 4: Validate behavior and token redaction**

Run: `python -m pytest tests/test_preflight.py tests/test_cli.py tests/test_security.py -q`
Expected: PASS and no secret values in captured output.

- [ ] **Step 5: Commit**

```powershell
git add src/hf_download_live_monitor/preflight.py src/hf_download_live_monitor/cli.py tests/test_preflight.py tests/test_cli.py
git commit -m "feat: preflight destinations and disk capacity"
```

### Task 4: Bounded SHA-256 verification

**Files:**
- Create: `src/hf_download_live_monitor/integrity.py`
- Create: `tests/test_integrity.py`
- Modify: `src/hf_download_live_monitor/filesystem.py`
- Modify: `src/hf_download_live_monitor/models.py`
- Modify: `tests/test_filesystem.py`

- [ ] **Step 1: Write failing verifier tests**

```python
def test_matching_digest_becomes_verified(tmp_path: Path) -> None:
    path = tmp_path / "model.bin"
    path.write_bytes(b"model")
    expected = hashlib.sha256(b"model").hexdigest()
    verifier = IntegrityVerifier(max_workers=1)
    assert verifier.verify_now(path, expected).state is FileState.VERIFIED


def test_changed_identity_invalidates_cached_result(tmp_path: Path) -> None:
    path = tmp_path / "model.bin"
    path.write_bytes(b"first")
    verifier = IntegrityVerifier(max_workers=1)
    first = verifier.verify_now(path, hashlib.sha256(b"first").hexdigest())
    path.write_bytes(b"second")
    second = verifier.verify_now(path, hashlib.sha256(b"second").hexdigest())
    assert first.identity != second.identity


def test_missing_digest_is_complete_but_not_verified(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    assert IntegrityVerifier().verify_now(path, None).state is FileState.COMPLETE_UNVERIFIED
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_integrity.py tests/test_filesystem.py -q`
Expected: FAIL because integrity states and verifier are absent.

- [ ] **Step 3: Implement identity, chunked hashing, and bounded scheduling**

```python
@dataclass(frozen=True, slots=True)
class FileIdentity:
    size: int
    modified_ns: int


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
```

Use `ThreadPoolExecutor(max_workers=1)` by default. Key cached results by `(resolved path, FileIdentity, expected digest)`. Expose `request(path, expected)` returning pending or completed status, `verify_now` for final reconciliation/tests, and `close()` to join work. Extend `FileObservation` with `identity: FileIdentity | None`. Replace ambiguous `COMPLETE` with `SIZE_MATCHED`, `VERIFYING`, `VERIFIED`, `COMPLETE_UNVERIFIED`, and `FAILED`, retaining download-phase states.

- [ ] **Step 4: Run verifier and filesystem tests**

Run: `python -m pytest tests/test_integrity.py tests/test_filesystem.py tests/test_models.py -q && python -m pyright`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/hf_download_live_monitor/integrity.py src/hf_download_live_monitor/filesystem.py src/hf_download_live_monitor/models.py tests/test_integrity.py tests/test_filesystem.py tests/test_models.py
git commit -m "feat: verify completed files with sha256"
```

### Task 5: Integrity-aware snapshots and forced final observation

**Files:**
- Modify: `src/hf_download_live_monitor/engine.py`
- Modify: `src/hf_download_live_monitor/app.py`
- Modify: `tests/test_engine.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write failing engine and final-pass tests**

```python
def test_size_match_with_digest_is_not_complete_before_verification() -> None:
    snapshot = engine.update(PLAN, observations=(exact_observation("model.bin"),), now=1.0)
    assert snapshot.files[0].state in {FileState.SIZE_MATCHED, FileState.VERIFYING}
    assert snapshot.verified_files == 0


def test_stop_condition_forces_one_more_observation() -> None:
    observer = SequenceObserver([partial_observation(), final_observation()])
    application = make_application(observer=observer)
    assert application.run(PLAN, stop_when=lambda: True) == 0
    assert observer.calls == 2
    assert application.renderer.snapshots[-1].downloaded_bytes == EXPECTED_BYTES
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_engine.py tests/test_app.py -q`
Expected: FAIL because the engine does not accept a plan/verifier and the app exits before a final pass.

- [ ] **Step 3: Integrate verifier and explicit final reconciliation**

Change `ProgressEngine.update(plan, observations, now, *, final=False)`. On exact final size, request verification; when `final=True`, use `verify_now`. Oversized files or mismatched hashes become `FAILED` with an integrity `MonitorError`. Add snapshot fields `requested_revision`, `resolved_revision`, `verified_files`, `complete_unverified_files`, `failed_files`, and a bounded `rate_history` tuple.

Use one explicit immutable snapshot contract:

```python
@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    spec: DownloadSpec
    requested_revision: str
    files: tuple[FileProgress, ...]
    observed_at: float
    downloaded_bytes: int
    expected_bytes: int
    rate_bytes_per_second: float | None
    eta_seconds: float | None
    verified_files: int = 0
    complete_unverified_files: int = 0
    failed_files: int = 0
    rate_history: tuple[float, ...] = ()
    errors: tuple[MonitorError, ...] = ()

    @property
    def resolved_revision(self) -> str:
        return self.spec.revision
```

Refactor the application loop:

```python
while not once and not _should_stop(stop_when):
    self._observe_render(plan, final=False)
    self._sleeper(self._refresh)
snapshot = self._observe_render(plan, final=True)
return exit_code_for(ErrorCategory.INTEGRITY) if snapshot.failed_files else 0
```

For `once=True`, perform exactly one final observation. Always close both renderer and engine/verifier resources.

- [ ] **Step 4: Validate state transitions**

Run: `python -m pytest tests/test_engine.py tests/test_app.py tests/test_integrity.py -q`
Expected: PASS, including digest mismatch and absent-digest cases.

- [ ] **Step 5: Commit**

```powershell
git add src/hf_download_live_monitor/engine.py src/hf_download_live_monitor/app.py tests/test_engine.py tests/test_app.py
git commit -m "feat: reconcile and verify final download state"
```

### Task 6: Guaranteed child cleanup

**Files:**
- Modify: `src/hf_download_live_monitor/runner.py`
- Modify: `tests/test_runner.py`

- [ ] **Step 1: Write failing cleanup tests**

```python
@pytest.mark.parametrize("failure", [MonitorError("observe_failed", "boom"), RuntimeError("boom")])
def test_monitor_failure_terminates_and_reaps_child(failure: Exception) -> None:
    process = FakeProcess(running=True)
    managed = ManagedDownload(RaisingApplication(failure), process_factory=lambda _: process)
    with pytest.raises(type(failure)):
        managed.run(SPEC)
    assert process.calls == ["terminate", "wait:5.0"]


def test_unresponsive_child_is_killed_and_reaped() -> None:
    process = FakeProcess(running=True, terminate_times_out=True)
    with pytest.raises(RuntimeError):
        ManagedDownload(
            RaisingApplication(RuntimeError("boom")), process_factory=lambda _: process
        ).run(SPEC)
    assert process.calls == ["terminate", "wait:5.0", "kill", "wait:None"]
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_runner.py -q`
Expected: FAIL because only `KeyboardInterrupt` triggers cleanup.

- [ ] **Step 3: Centralize terminate/kill/reap semantics**

```python
def _stop_and_reap(process: ChildProcess, grace: float = 5.0) -> int:
    if process.poll() is not None:
        return process.wait()
    process.terminate()
    try:
        return process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()
```

Wrap application execution in `try/except BaseException`; on any exception call `_stop_and_reap`, attach cleanup failure as a note with `exc.add_note(...)`, then re-raise the original. On normal monitor completion, `wait()` naturally. Build the child command only from `plan.spec`, ensuring the resolved SHA is passed with `--revision` even when the requested revision was `main`.

- [ ] **Step 4: Validate runner behavior**

Run: `python -m pytest tests/test_runner.py tests/test_hf_command.py -q`
Expected: PASS across natural exit, exception, interrupt, timeout, kill, and cleanup-error cases.

- [ ] **Step 5: Commit**

```powershell
git add src/hf_download_live_monitor/runner.py tests/test_runner.py tests/test_hf_command.py
git commit -m "fix: guarantee downloader cleanup on monitor failure"
```

### Task 7: Flexible layout policy

**Files:**
- Create: `src/hf_download_live_monitor/layout.py`
- Create: `tests/test_layout.py`
- Modify: `src/hf_download_live_monitor/models.py`

- [ ] **Step 1: Write failing layout tests**

```python
@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (59, LayoutClass.NARROW),
        (60, LayoutClass.NORMAL),
        (109, LayoutClass.NORMAL),
        (110, LayoutClass.WIDE),
    ],
)
def test_layout_breakpoints(width: int, expected: LayoutClass) -> None:
    assert choose_layout(width, ViewMode.BALANCED) is expected


def test_narrow_layout_suppresses_nonessential_panels() -> None:
    policy = layout_policy(width=50, mode=ViewMode.DETAILED, reduced_motion=True)
    assert policy.show_sparkline is False
    assert policy.columns == 1
    assert policy.animate is False
    assert policy.show_completed_files is True
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_layout.py -q`
Expected: FAIL because `layout.py` is absent.

- [ ] **Step 3: Implement pure layout decisions**

```python
class ViewMode(str, Enum):
    COMPACT = "compact"
    BALANCED = "balanced"
    DETAILED = "detailed"


class LayoutClass(str, Enum):
    NARROW = "narrow"
    NORMAL = "normal"
    WIDE = "wide"


def choose_layout(width: int, mode: ViewMode) -> LayoutClass:
    if width < 60:
        return LayoutClass.NARROW
    return LayoutClass.WIDE if width >= 110 and mode is not ViewMode.COMPACT else LayoutClass.NORMAL
```

Add a frozen `LayoutPolicy` containing columns, sparkline/event/preflight/file visibility, animation, and label-abbreviation decisions. Keep it independent of Rich so it is exhaustively testable.

- [ ] **Step 4: Validate all width/mode combinations**

Run: `python -m pytest tests/test_layout.py -q && python -m pyright`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/hf_download_live_monitor/layout.py src/hf_download_live_monitor/models.py tests/test_layout.py
git commit -m "feat: add adaptive terminal layout policy"
```

### Task 8: Adaptive Focus and fallback renderers

**Files:**
- Modify: `src/hf_download_live_monitor/renderers.py`
- Modify: `tests/test_renderers.py`
- Create: `src/hf_download_live_monitor/controls.py`
- Create: `tests/test_controls.py`
- Modify: `src/hf_download_live_monitor/app.py`
- Modify: `src/hf_download_live_monitor/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing renderer contract tests**

```python
def test_narrow_adaptive_focus_contains_essential_state_only() -> None:
    console = Console(file=io.StringIO(), width=50, force_terminal=True, color_system=None)
    renderer = RichRenderer(console, view_mode=ViewMode.BALANCED, reduced_motion=True)
    renderer.render(SNAPSHOT)
    output = console.file.getvalue()
    assert "95.00%" in output
    assert "VERIFIED" in output
    assert "config.json" in output
    assert "Recent events" not in output


def test_json_schema_v2_distinguishes_complete_and_verified() -> None:
    payload = snapshot_to_dict(SNAPSHOT)
    assert payload["schema_version"] == 2
    assert payload["repository"]["requested_revision"] == "main"
    assert payload["repository"]["resolved_revision"] == "a" * 40
    assert payload["integrity"]["verified_files"] == 2


@pytest.mark.parametrize(
    ("key", "expected"),
    [("v", ViewMode.DETAILED), ("d", True), ("e", True), ("?", True)],
)
def test_interactive_keys_update_display_state(key: str, expected: object) -> None:
    updated = DisplayState().apply_key(key)
    assert expected in {
        updated.view_mode,
        updated.show_details,
        updated.show_events,
        updated.show_help,
    }


def test_q_requests_graceful_cancellation() -> None:
    assert DisplayState().apply_key("q").cancel_requested is True
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_renderers.py tests/test_cli.py -q`
Expected: FAIL because Adaptive Focus, controls, and schema v2 fields are absent.

- [ ] **Step 3: Build Adaptive Focus from focused render functions**

Implement `_header_panel`, `_aggregate_panel`, `_attention_table`, `_preflight_panel`, `_events_panel`, and `_footer`. `RichRenderer.render` reads `console.width` every refresh, obtains a `LayoutPolicy`, and composes with `Group`, `Columns`, `Panel`, `ProgressBar`, and `Table`. Use state text plus symbols; select ASCII symbols when requested. Store at most 24 rate samples and render a sparkline only when policy permits.

Add constructor settings:

```python
def __init__(
    self,
    console: Console | None = None,
    *,
    view_mode: ViewMode = ViewMode.BALANCED,
    ascii_only: bool = False,
    reduced_motion: bool = False,
) -> None: ...
```

Implement `DisplayState.apply_key()` as a pure frozen-state transition. `KeyboardController.poll()` calls an injected non-blocking `read_key` and returns the unchanged state when no key is available. Platform readers use `msvcrt.kbhit/getwch` on Windows and `select.select` plus a temporary cbreak terminal on POSIX. Initialization or polling errors disable keyboard input for the remainder of the run and preserve rendering. The application polls after each render, uses the display state for the next render, and converts `cancel_requested` into the stable cancellation exit category after final reconciliation. `close()` always restores any modified POSIX terminal settings.

Add CLI flags `--view [compact|balanced|detailed]` and `--reduced-motion`. Continue selecting `PlainRenderer` when stdout is not a TTY. Make `--no-color` and `--ascii` work in both live and plain output. Map `MonitorError.category` through `exit_code_for`.

- [ ] **Step 4: Validate rendering at representative widths**

Run: `python -m pytest tests/test_renderers.py tests/test_controls.py tests/test_cli.py tests/test_layout.py tests/test_app.py -q`
Expected: PASS at widths 40, 59, 60, 80, 109, 110, and 160 with Unicode/ASCII, color/no-color, and reduced-motion cases.

- [ ] **Step 5: Commit**

```powershell
git add src/hf_download_live_monitor/renderers.py src/hf_download_live_monitor/controls.py src/hf_download_live_monitor/app.py src/hf_download_live_monitor/cli.py tests/test_renderers.py tests/test_controls.py tests/test_app.py tests/test_cli.py
git commit -m "feat: introduce adaptive focus terminal dashboard"
```

### Task 9: Real simulated-download integration and Docker validation

**Files:**
- Create: `tests/fixtures/incremental_downloader.py`
- Create: `tests/integration/test_simulated_download.py`
- Create: `scripts/run_container_simulation.py`
- Create: `Dockerfile.test`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing end-to-end test**

```python
def test_real_child_is_monitored_until_verified(tmp_path: Path) -> None:
    source = b"verified model content" * 4096
    plan = local_plan(tmp_path, "model.bin", source)
    result = run_simulation(plan, chunk_size=1024, delay=0.002)
    assert result.exit_code == 0
    assert result.snapshots[-1].files[0].state is FileState.VERIFIED
    assert any(snapshot.downloaded_bytes < len(source) for snapshot in result.snapshots)
    assert result.child_reaped is True
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/integration/test_simulated_download.py -q`
Expected: FAIL because the simulation fixture/harness is absent.

- [ ] **Step 3: Implement deterministic child and container harness**

The fixture accepts destination, byte count, chunk size, delay, and optional corruption flag, writes to the Hugging Face-style `.incomplete` path, flushes each chunk, then atomically replaces the final path. The integration harness injects a local repository plan while using the real `subprocess.Popen`, observer, engine, verifier, and renderers.

Use this container definition:

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN python -m pip install --no-cache-dir -e ".[dev]"
CMD ["python", "scripts/run_container_simulation.py", "--repeat", "4"]
```

`run_container_simulation.py` runs four fresh temporary destinations, checks at least one intermediate snapshot, asserts a verified final snapshot, and prints one JSON summary per run.

- [ ] **Step 4: Run local and Docker simulations four times**

Run: `python -m pytest tests/integration/test_simulated_download.py -q`
Expected: PASS.

Run: `docker build -f Dockerfile.test -t hf-download-live-monitor:test .`
Expected: image builds successfully.

Run: `docker run --rm hf-download-live-monitor:test`
Expected: four JSON summaries with `"verified": true`, followed by exit code 0.

- [ ] **Step 5: Commit**

```powershell
git add tests/fixtures/incremental_downloader.py tests/integration/test_simulated_download.py scripts/run_container_simulation.py Dockerfile.test pyproject.toml
git commit -m "test: exercise verified downloads end to end"
```

### Task 10: ARM64-labelled CI and release artifacts

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `scripts/build_standalone.py`
- Modify: `tests/test_release_assets.py`
- Modify: `tests/test_package.py`

- [ ] **Step 1: Write failing artifact-name tests**

```python
@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", "hf-download-live-monitor-windows-x86_64.exe"),
        ("Windows", "ARM64", "hf-download-live-monitor-windows-arm64.exe"),
        ("Linux", "x86_64", "hf-download-live-monitor-linux-x86_64"),
        ("Linux", "aarch64", "hf-download-live-monitor-linux-arm64"),
        ("Darwin", "x86_64", "hf-download-live-monitor-macos-x86_64"),
        ("Darwin", "arm64", "hf-download-live-monitor-macos-arm64"),
    ],
)
def test_artifact_name(system: str, machine: str, expected: str) -> None:
    assert artifact_name(system, machine) == expected
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_release_assets.py tests/test_package.py -q`
Expected: FAIL because architecture-aware naming is absent.

- [ ] **Step 3: Add normalized platform/architecture naming**

```python
def normalized_architecture(machine: str) -> str:
    value = machine.lower()
    if value in {"amd64", "x86_64"}:
        return "x86_64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    raise ValueError(f"unsupported architecture: {machine}")
```

Rename the PyInstaller output and its checksum using normalized OS/architecture. In CI, keep the full Python OS/version matrix and add Linux ARM64 package/simulation validation using an ARM64 job or `docker buildx`/QEMU when no native runner is available. In release, use explicit matrix entries for Windows x86-64/ARM64, Linux x86-64/ARM64, and macOS x86-64/ARM64; mark any unavailable native standalone target as a wheel-only fallback in generated release notes rather than publishing a mislabeled executable. Upload artifact names containing both OS and architecture.

- [ ] **Step 4: Validate workflows and local build contract**

Run: `python -m pytest tests/test_release_assets.py tests/test_package.py -q`
Expected: PASS.

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); yaml.safe_load(open('.github/workflows/release.yml'))"`
Expected: no output and exit code 0 (install `PyYAML` only in the development validation environment, not runtime dependencies).

- [ ] **Step 5: Commit**

```powershell
git add .github/workflows/ci.yml .github/workflows/release.yml scripts/build_standalone.py tests/test_release_assets.py tests/test_package.py
git commit -m "build: add architecture-aware arm64 distributions"
```

### Task 11: Documentation and schema migration

**Files:**
- Modify: `README.md`
- Modify: `docs/user-manual.md`
- Modify: `docs/architecture.md`
- Modify: `docs/json-schema.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write failing documentation checks**

```python
@pytest.mark.parametrize(
    "required",
    ["Adaptive Focus", "ARM64", "complete_unverified", "verified", "--reduced-motion", "--view"],
)
def test_manual_documents_new_public_contract(required: str) -> None:
    manual = Path("docs/user-manual.md").read_text(encoding="utf-8")
    assert required in manual


def test_json_schema_documents_version_two() -> None:
    schema = Path("docs/json-schema.md").read_text(encoding="utf-8")
    assert '"schema_version": 2' in schema
    assert '"resolved_revision"' in schema
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_docs.py -q`
Expected: FAIL for missing milestone terminology and schema v2.

- [ ] **Step 3: Update all public documentation**

Document package and standalone installation by OS/architecture, ARM64 fallback boundaries, authentication/gated access, preflight calculations, integrity vocabulary, interactive keys, view modes, reduced motion, ASCII/no-color, stable exit categories, JSON schema v2, Docker simulation, cancellation, and troubleshooting. Add a schema-v1-to-v2 migration table. Add an unreleased changelog section grouping Added, Changed, Fixed, Security, and Distribution entries.

- [ ] **Step 4: Validate docs and links**

Run: `python -m pytest tests/test_docs.py tests/test_prototype_checksum.py -q`
Expected: PASS with prototype checksum unchanged.

- [ ] **Step 5: Commit**

```powershell
git add README.md docs/user-manual.md docs/architecture.md docs/json-schema.md CHANGELOG.md tests/test_docs.py
git commit -m "docs: explain verified adaptive arm64 downloads"
```

### Task 12: Four-pass release-grade verification

**Files:**
- Modify only files needed to repair failures found by these commands.

- [ ] **Step 1: Run static quality checks**

Run: `python -m ruff format --check . && python -m ruff check . && python -m pyright`
Expected: all commands exit 0 with no warnings.

- [ ] **Step 2: Run the complete test suite four independent times**

```powershell
1..4 | ForEach-Object {
  Write-Host "Full test pass $_/4"
  python -m pytest -q
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: four consecutive complete passes with identical test counts and no unexpected skips or warnings.

- [ ] **Step 3: Build and inspect Python distributions**

Run: `python -m build && python -m twine check dist/*`
Expected: source and wheel distributions build and both pass Twine validation.

- [ ] **Step 4: Run package and container smoke tests**

Run: `python scripts/build_standalone.py`
Expected: architecture-labelled executable and matching `.sha256` file exist.

Run: `docker build -f Dockerfile.test -t hf-download-live-monitor:test . && docker run --rm hf-download-live-monitor:test`
Expected: four verified simulations and exit code 0.

- [ ] **Step 5: Inspect the exact release delta**

Run: `git status --short && git diff --check HEAD~11..HEAD && git log --show-signature --oneline HEAD~11..HEAD`
Expected: clean working tree, no whitespace errors, and every implementation commit reports a good signature.

- [ ] **Step 6: Commit any verification-only repair**

If Step 1-5 required a repair, add only the repaired files and commit:

```powershell
git add -- <exact-repaired-paths>
git commit -m "fix: resolve final milestone verification findings"
```

If no repair was required, do not create an empty commit.

- [ ] **Step 7: Obtain explicit upload approval and push**

Before pushing, report the exact branch, commit range, changed files, ignored/private exclusions, test counts, artifact names, and signature status. Push only after explicit user approval, then monitor every GitHub Actions workflow through completion and repair/retest/re-push until all required checks are green.
