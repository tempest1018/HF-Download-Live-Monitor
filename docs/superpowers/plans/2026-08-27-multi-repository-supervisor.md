# Continuous Multi-Repository Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make continuous `attach --all` safely discover, monitor, finalize, and render multiple concurrent Hugging Face downloads.

**Architecture:** Add a deterministic `DownloadSupervisor` coordinator above isolated per-session repository, observation, and progress-engine state. A unified psutil discovery provider supplies PID-reuse-safe identities, while one supervisor renderer owns all terminal or structured output.

**Tech Stack:** Python 3.10+, psutil, Rich, Typer, pytest, Pyright, Ruff, PyInstaller, GitHub Actions, Docker.

---

## File map

- Modify `src/hf_download_live_monitor/processes.py`: cross-platform process identity and psutil discovery.
- Modify `src/hf_download_live_monitor/attach.py`: sanitized, stable download candidates.
- Create `src/hf_download_live_monitor/supervisor_models.py`: frozen lifecycle, aggregate, and event models.
- Create `src/hf_download_live_monitor/supervisor.py`: reconciliation, scheduling, observation, finalization, retention, and shutdown.
- Create `src/hf_download_live_monitor/supervisor_renderers.py`: Rich, plain, JSONL, and final JSON supervisor outputs.
- Modify `src/hf_download_live_monitor/controls.py`: repository selection controls.
- Modify `src/hf_download_live_monitor/cli.py`: continuous `attach --all` routing and options.
- Modify `scripts/run_published_acceptance.py`: bounded multi-download artifact scenario.
- Modify `README.md`, `docs/user-manual.md`, and `docs/architecture.md`: operator and schema documentation.
- Create focused tests matching each production module and extend integration/release tests.

### Task 1: PID-reuse-safe cross-platform discovery

**Files:**
- Modify: `src/hf_download_live_monitor/processes.py`
- Test: `tests/test_processes.py`

- [ ] **Step 1: Write failing identity and psutil-provider tests**

Add tests that inject fake psutil processes and assert per-process isolation:

```python
def test_psutil_provider_records_stable_start_token_and_skips_denied_process() -> None:
    allowed = FakePsutilProcess(41, 1234.5, ["hf", "download", "owner/repo"], Path("out"))
    denied = FakePsutilProcess(42, error=PermissionError("denied"))
    records = PsutilProcessProvider(iterator=lambda: (denied, allowed)).discover()
    assert records == (
        ProcessRecord(41, ("hf", "download", "owner/repo"), Path("out"), "1234.500000000"),
    )


def test_process_identity_distinguishes_reused_pid() -> None:
    first = ProcessRecord(41, ("hf",), Path("out"), "100")
    second = ProcessRecord(41, ("hf",), Path("out"), "200")
    assert first.identity != second.identity
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run: `python -m pytest tests/test_processes.py -q`

Expected: FAIL because `PsutilProcessProvider`, `start_token`, and `identity` do not exist.

- [ ] **Step 3: Implement the minimal typed provider**

Add the following public shape and keep native providers as fallbacks:

```python
@dataclass(frozen=True, slots=True, order=True)
class ProcessIdentity:
    pid: int
    start_token: str


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    pid: int
    args: tuple[str, ...]
    cwd: Path
    start_token: str = "unknown"

    @property
    def identity(self) -> ProcessIdentity:
        return ProcessIdentity(self.pid, self.start_token)


class PsutilProcessProvider:
    def __init__(self, iterator: Callable[[], Iterable[object]] | None = None) -> None:
        self._iterator = iterator or psutil.process_iter

    def discover(self) -> tuple[ProcessRecord, ...]:
        records: list[ProcessRecord] = []
        for process in self._iterator():
            try:
                records.append(
                    ProcessRecord(
                        process.pid,
                        tuple(process.cmdline()),
                        Path(process.cwd()),
                        f"{process.create_time():.9f}",
                    )
                )
            except (OSError, ValueError, psutil.Error):
                continue
        return tuple(sorted(records, key=lambda item: item.identity))
```

Make `system_process_provider()` prefer this provider on Windows, Linux, and macOS.

- [ ] **Step 4: Verify focused and compatibility tests**

Run: `python -m pytest tests/test_processes.py tests/test_attach.py tests/test_hf_command.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the discovery unit**

```powershell
git add src/hf_download_live_monitor/processes.py tests/test_processes.py
git commit -S -m "feat: add stable cross-platform process identity"
```

### Task 2: Sanitized candidate identity

**Files:**
- Modify: `src/hf_download_live_monitor/attach.py`
- Test: `tests/test_attach.py`
- Test: `tests/test_security.py`

- [ ] **Step 1: Add failing candidate ordering and privacy tests**

```python
def test_discovery_discards_raw_arguments_and_orders_by_session_key() -> None:
    records = (
        ProcessRecord(9, ("hf", "download", "b/repo", "--token", "secret"), Path("b"), "2"),
        ProcessRecord(3, ("hf", "download", "a/repo"), Path("a"), "1"),
    )
    candidates = discover_downloads(FakeProvider(records))
    assert [item.spec.repo for item in candidates] == ["a/repo", "b/repo"]
    assert all(not hasattr(item, "args") for item in candidates)
    assert "secret" not in repr(candidates)
```

- [ ] **Step 2: Prove the tests fail**

Run: `python -m pytest tests/test_attach.py tests/test_security.py -q`

Expected: FAIL because candidates do not carry process identity or stable session keys.

- [ ] **Step 3: Implement immutable candidate/session keys**

```python
@dataclass(frozen=True, slots=True, order=True)
class SessionKey:
    repo_type: str
    repo: str
    local_dir: str
    revision: str
    process: ProcessIdentity


@dataclass(frozen=True, slots=True)
class DownloadCandidate:
    process: ProcessIdentity
    spec: DownloadSpec

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def key(self) -> SessionKey:
        return SessionKey(
            self.spec.repo_type.value,
            self.spec.repo,
            str(self.spec.local_dir.resolve(strict=False)),
            self.spec.revision,
            self.process,
        )
```

Sort by `candidate.key`; never retain `ProcessRecord.args` beyond parsing.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_attach.py tests/test_security.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit candidate identity**

```powershell
git add src/hf_download_live_monitor/attach.py tests/test_attach.py tests/test_security.py
git commit -S -m "feat: add privacy-safe download session keys"
```

### Task 3: Supervisor domain and serialization contracts

**Files:**
- Create: `src/hf_download_live_monitor/supervisor_models.py`
- Create: `tests/test_supervisor_models.py`

- [ ] **Step 1: Write failing frozen-model and serialization tests**

```python
def test_supervisor_event_contract_is_versioned_and_strictly_serializable() -> None:
    event = SupervisorEvent(7, "run-id", 10.0, EventType.SESSION_ADDED, "session-1")
    assert event.to_dict() == {
        "schema_version": 1,
        "kind": "supervisor_event",
        "sequence": 7,
        "run_id": "run-id",
        "observed_at": 10.0,
        "event": "session_added",
        "session_id": "session-1",
    }


def test_aggregate_snapshot_sums_only_finite_non_negative_rates() -> None:
    snapshot = SupervisorSnapshot.build(10.0, (session(rate=4.0), session(rate=None)))
    assert snapshot.rate_bytes_per_second == 4.0
    assert snapshot.active_sessions == 2
```

- [ ] **Step 2: Confirm the missing-model failures**

Run: `python -m pytest tests/test_supervisor_models.py -q`

Expected: FAIL on import.

- [ ] **Step 3: Implement the complete model vocabulary**

Define frozen `SessionLifecycle`, `EventType`, `DiscoveryHealth`, `SessionSnapshot`,
`SupervisorSnapshot`, and `SupervisorEvent`. Validate non-negative counts/rates and expose
only `to_dict()` payloads made of JSON-native values. Use `math.isfinite` before summing
rates and omit aggregate ETA.

```python
class SessionLifecycle(str, Enum):
    DISCOVERED = "discovered"
    PREPARING = "preparing"
    ACTIVE = "active"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"
```

- [ ] **Step 4: Verify model tests and strict typing**

Run: `python -m pytest tests/test_supervisor_models.py -q; python -m pyright`

Expected: PASS and zero Pyright errors.

- [ ] **Step 5: Commit domain models**

```powershell
git add src/hf_download_live_monitor/supervisor_models.py tests/test_supervisor_models.py
git commit -S -m "feat: define supervisor state and event contracts"
```

### Task 4: Deterministic discovery reconciliation and retention

**Files:**
- Create: `src/hf_download_live_monitor/supervisor.py`
- Create: `tests/test_supervisor.py`

- [ ] **Step 1: Write failing lifecycle tests with an injected clock**

```python
def test_supervisor_adds_new_sessions_and_prevents_pid_reuse_merge() -> None:
    provider = SequenceProvider(((candidate(7, "100"),), (candidate(7, "200"),)))
    supervisor = make_supervisor(provider=provider)
    supervisor.tick()
    supervisor.tick()
    assert [item.process.start_token for item in supervisor.snapshot.sessions] == ["100", "200"]


def test_finalized_session_is_removed_only_after_retention() -> None:
    clock = FakeClock()
    supervisor = make_supervisor(clock=clock, retention=15.0)
    supervisor.add_final(completed_session())
    clock.advance(14.9)
    supervisor.tick()
    assert len(supervisor.snapshot.sessions) == 1
    clock.advance(0.1)
    supervisor.tick()
    assert supervisor.snapshot.sessions == ()
```

- [ ] **Step 2: Run tests to establish red state**

Run: `python -m pytest tests/test_supervisor.py -q`

Expected: FAIL because `DownloadSupervisor` is missing.

- [ ] **Step 3: Implement coordinator state and event sequencing**

Use a private mutable `_SessionRuntime` only inside the coordinator. Public snapshots
remain frozen. `tick()` collects results, runs due discovery, reconciles keys, enforces
`max_sessions`, expires final sessions, sorts snapshots, and emits events through one
monotonic `_next_sequence()` method.

```python
def _next_event(self, event: EventType, session_id: str | None = None) -> SupervisorEvent:
    self._sequence += 1
    return SupervisorEvent(self._sequence, self._run_id, self._clock(), event, session_id)
```

Discovery errors retain existing sessions, mark degraded health, and emit one deduplicated
warning until a successful scan clears it.

- [ ] **Step 4: Verify lifecycle and no-busy-loop tests**

Run: `python -m pytest tests/test_supervisor.py -q`

Expected: PASS.

- [ ] **Step 5: Commit reconciliation core**

```powershell
git add src/hf_download_live_monitor/supervisor.py tests/test_supervisor.py
git commit -S -m "feat: reconcile multi-download session lifecycles"
```

### Task 5: Bounded preparation, observation, and finalization

**Files:**
- Modify: `src/hf_download_live_monitor/supervisor.py`
- Modify: `tests/test_supervisor.py`

- [ ] **Step 1: Add failing scheduling and outcome tests**

Cover one outstanding task per session, four-worker maximum, immutable revision
preparation, per-session engine isolation, forced final observation, confirmed integrity
failure, insufficient-evidence `lost`, and cancellation that never signals children.

```python
def test_disappeared_process_is_forced_to_final_state() -> None:
    supervisor = make_supervisor(provider=SequenceProvider(((candidate(),), ())))
    supervisor.tick()
    supervisor.complete_preparation(plan())
    supervisor.tick()
    supervisor.complete_observation(verified_snapshot(), final=True)
    assert supervisor.snapshot.sessions[0].lifecycle is SessionLifecycle.COMPLETED


def test_shutdown_does_not_terminate_attached_processes() -> None:
    process = RecordingProcessProvider()
    supervisor = make_supervisor(provider=process)
    supervisor.shutdown()
    assert process.signals == []
```

- [ ] **Step 2: Confirm focused failures**

Run: `python -m pytest tests/test_supervisor.py -q`

Expected: FAIL on missing preparation/finalization behavior.

- [ ] **Step 3: Implement bounded session work**

Inject an `Executor` protocol and default to `ThreadPoolExecutor(max_workers=4)`. Store at
most one `Future` per session. Give every active session its own `ProgressEngine`; submit
repository preparation and forced finalization only. Routine due observations execute in
stable session order. On shutdown, stop discovery, reconcile each active session once,
close engines, then close executor, controls, and renderer with finite waits.

- [ ] **Step 4: Run lifecycle, engine, integrity, and shutdown tests**

Run: `python -m pytest tests/test_supervisor.py tests/test_engine.py tests/test_integrity.py -q`

Expected: PASS.

- [ ] **Step 5: Commit bounded monitoring**

```powershell
git add src/hf_download_live_monitor/supervisor.py tests/test_supervisor.py
git commit -S -m "feat: monitor and finalize bounded download sessions"
```

### Task 6: Structured and plain supervisor output

**Files:**
- Create: `src/hf_download_live_monitor/supervisor_renderers.py`
- Create: `tests/test_supervisor_renderers.py`

- [ ] **Step 1: Write failing output-contract tests**

```python
def test_jsonl_sequences_lifecycle_and_never_suppresses_final_event() -> None:
    stream = StringIO()
    renderer = SupervisorJsonLinesRenderer(stream, progress_interval=1.0)
    renderer.render(event(1, EventType.PROGRESS, at=1.0))
    renderer.render(event(2, EventType.PROGRESS, at=1.1))
    renderer.render(event(3, EventType.SESSION_FINALIZED, at=1.2))
    assert [json.loads(line)["sequence"] for line in stream.getvalue().splitlines()] == [1, 3]


def test_final_json_writes_exactly_one_document_on_close() -> None:
    stream = StringIO()
    renderer = SupervisorJsonRenderer(stream)
    renderer.render(snapshot())
    renderer.render(newer_snapshot())
    renderer.close()
    assert json.loads(stream.getvalue())["kind"] == "supervisor_snapshot"
```

- [ ] **Step 2: Verify red state**

Run: `python -m pytest tests/test_supervisor_renderers.py -q`

Expected: FAIL on missing renderers.

- [ ] **Step 3: Implement output renderers over sanitized models**

Create a `SupervisorRenderer` protocol. Plain output deduplicates unchanged lifecycle and
rate-limits progress. JSONL tracks last progress emission per session but immediately
emits lifecycle/final events. Final JSON replaces its buffered snapshot and serializes it
once from `close()`.

```python
class SupervisorRenderer(Protocol):
    def render_snapshot(self, snapshot: SupervisorSnapshot) -> None: ...
    def render_event(self, event: SupervisorEvent) -> None: ...
    def close(self) -> None: ...


class SupervisorJsonRenderer:
    def render_snapshot(self, snapshot: SupervisorSnapshot) -> None:
        self._final = snapshot

    def close(self) -> None:
        if self._final is not None:
            json.dump(self._final.to_dict(), self._stream, sort_keys=True)
            self._stream.write("\n")
```

- [ ] **Step 4: Verify output tests and stdout isolation**

Run: `python -m pytest tests/test_supervisor_renderers.py tests/test_renderers.py -q`

Expected: PASS.

- [ ] **Step 5: Commit structured output**

```powershell
git add src/hf_download_live_monitor/supervisor_renderers.py tests/test_supervisor_renderers.py
git commit -S -m "feat: add deterministic supervisor output contracts"
```

### Task 7: Adaptive aggregate dashboard and controls

**Files:**
- Modify: `src/hf_download_live_monitor/supervisor_renderers.py`
- Modify: `src/hf_download_live_monitor/controls.py`
- Modify: `tests/test_supervisor_renderers.py`
- Modify: `tests/test_controls.py`

- [ ] **Step 1: Add failing selection and layout tests**

```python
def test_supervisor_selection_wraps_without_mutating_other_display_state() -> None:
    state = SupervisorDisplayState(selected_index=0, session_count=2)
    assert state.apply_key("j").selected_index == 1
    assert state.apply_key("k").selected_index == 1


@pytest.mark.parametrize("width,expected_columns", [(45, 4), (90, 6), (140, 8)])
def test_dashboard_adapts_repository_table(width: int, expected_columns: int) -> None:
    assert supervisor_layout(width).repository_columns == expected_columns
```

- [ ] **Step 2: Confirm controls/layout failures**

Run: `python -m pytest tests/test_controls.py tests/test_supervisor_renderers.py -q`

Expected: FAIL on missing supervisor display state and Rich renderer.

- [ ] **Step 3: Implement pure selection state and Rich composition**

Add `SupervisorDisplayState` with selected stable session ID rather than only an index;
`j`/down selects the next ordered ID and `k`/up selects the previous ID. Preserve the
selection across resorting and choose the nearest remaining session after removal. Build
aggregate status, repository table, selected existing-detail panels, degraded-discovery
notice, and help footer from `SupervisorSnapshot` only.

```python
@dataclass(frozen=True, slots=True)
class SupervisorDisplayState:
    view_mode: ViewMode = ViewMode.BALANCED
    selected_session_id: str | None = None
    show_help: bool = False
    cancel_requested: bool = False

    def reconcile(self, ordered_ids: tuple[str, ...]) -> SupervisorDisplayState:
        if self.selected_session_id in ordered_ids or not ordered_ids:
            return self
        return replace(self, selected_session_id=ordered_ids[0] if ordered_ids else None)
```

- [ ] **Step 4: Verify accessibility and snapshot rendering**

Run: `python -m pytest tests/test_controls.py tests/test_supervisor_renderers.py -q`

Expected: PASS for narrow, ASCII, reduced-motion, no-color, and selection cases.

- [ ] **Step 5: Commit dashboard behavior**

```powershell
git add src/hf_download_live_monitor/controls.py src/hf_download_live_monitor/supervisor_renderers.py tests/test_controls.py tests/test_supervisor_renderers.py
git commit -S -m "feat: render adaptive multi-download dashboard"
```

### Task 8: CLI integration and stable exit behavior

**Files:**
- Modify: `src/hf_download_live_monitor/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI contract tests**

```python
def test_continuous_attach_all_routes_to_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = RecordingSupervisor()
    monkeypatch.setattr(cli_module, "_make_supervisor", lambda **_: supervisor)
    result = runner.invoke(cli, ["attach", "--all", "--jsonl"])
    assert result.exit_code == 0
    assert supervisor.runs == 1


def test_attach_all_validates_supervisor_limits() -> None:
    result = runner.invoke(cli, ["attach", "--all", "--max-sessions", "0"])
    assert result.exit_code == 2
```

- [ ] **Step 2: Prove continuous mode is still rejected**

Run: `python -m pytest tests/test_cli.py -q`

Expected: FAIL because CLI raises `all_requires_once`.

- [ ] **Step 3: Add the supervisor construction path**

Add `--discovery-refresh 1.0`, `--retention 15.0`, and `--max-sessions 32` to `attach`.
Route only `all_downloads and not once` to `_make_supervisor(...).run()`. Retain the
existing loop for `--all --once`, and keep `--pid` mutually exclusive with `--all`.
Map supervisor failures through `_exit_for_error`; cancellation returns the stable
cancelled exit code and renderer shutdown failures remain monitor errors.

```python
if all_downloads and not once:
    code = _make_supervisor(
        refresh=refresh,
        discovery_refresh=discovery_refresh,
        retention=retention,
        max_sessions=max_sessions,
        plain=plain,
        json_output=json_output,
        jsonl=jsonl,
        no_color=no_color,
        ascii_only=ascii_only,
        view=view,
        reduced_motion=reduced_motion,
    ).run()
    if code:
        raise typer.Exit(code=code)
    return
```

- [ ] **Step 4: Run CLI and application regression tests**

Run: `python -m pytest tests/test_cli.py tests/test_app.py tests/test_runner.py -q`

Expected: PASS.

- [ ] **Step 5: Commit CLI integration**

```powershell
git add src/hf_download_live_monitor/cli.py tests/test_cli.py
git commit -S -m "feat: enable continuous attach all supervision"
```

### Task 9: Multi-process simulations and published acceptance

**Files:**
- Create: `tests/integration/test_multi_download_supervisor.py`
- Modify: `scripts/run_published_acceptance.py`
- Modify: `tests/test_published_acceptance.py`
- Modify: `.github/workflows/release-acceptance.yml`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Add failing deterministic integration scenarios**

Start two real child `hf download` simulations against separate localhost fixtures after
the supervisor starts. Finish them in reverse order, make one integrity-invalid, then
assert added/ready/progress/finalized/stopped event ordering and that both children remain
un-signalled when the monitor exits.

```python
assert event_types.count("session_added") == 2
assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
assert {event["lifecycle"] for event in finals} == {"completed", "failed"}
assert children_signalled == []
```

- [ ] **Step 2: Run the integration test and confirm red state**

Run: `python -m pytest tests/integration/test_multi_download_supervisor.py -q`

Expected: FAIL until discovery injection and lifecycle details satisfy the real scenario.

- [ ] **Step 3: Extend black-box acceptance safely**

Add a bounded `run_multi_acceptance()` helper that launches two fixture-backed downloader
children, invokes the published monitor with `attach --all --jsonl`, waits for both final
events, requests monitor-only shutdown, validates strict sequences and redacted output,
and records only command names, exit codes, session counts, and outcomes. Add one native
multi-download job on Linux x86_64 while retaining the six-platform smoke matrix.

```python
events = [json.loads(line) for line in completed.stdout.splitlines()]
sequences = [event["sequence"] for event in events]
if sequences != list(range(1, len(events) + 1)):
    raise AcceptanceError("supervisor event sequence was not contiguous")
finals = [event for event in events if event["event"] == "session_finalized"]
if len(finals) != 2 or events[-1]["event"] != "supervisor_stopped":
    raise AcceptanceError("supervisor did not emit two finals and an orderly stop")
```

- [ ] **Step 4: Run integration, harness, and workflow contract tests**

Run: `python -m pytest tests/integration/test_multi_download_supervisor.py tests/test_published_acceptance.py tests/test_release_assets.py -q`

Expected: PASS with no network dependency and no secrets in reports.

- [ ] **Step 5: Commit simulations and acceptance**

```powershell
git add tests/integration/test_multi_download_supervisor.py scripts/run_published_acceptance.py tests/test_published_acceptance.py .github/workflows/release-acceptance.yml tests/test_release_assets.py
git commit -S -m "test: accept published multi-download supervision"
```

### Task 10: Documentation, complete verification, and protected integration

**Files:**
- Modify: `README.md`
- Modify: `docs/user-manual.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Add failing documentation contract tests**

Assert the docs contain continuous `attach --all`, new options, lifecycle definitions,
monitor-only cancellation, JSONL event schema, final JSON behavior, privacy guarantees,
and the explicit non-goals.

```python
def test_continuous_attach_all_contract_is_documented() -> None:
    text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("README.md", "docs/user-manual.md", "docs/architecture.md")
    )
    for value in (
        "attach --all",
        "--discovery-refresh",
        "--retention",
        "--max-sessions",
        "supervisor_event",
        "does not terminate",
    ):
        assert value in text
```

- [ ] **Step 2: Prove documentation is stale**

Run: `python -m pytest tests/test_docs.py -q`

Expected: FAIL on the old deliberate-rejection text and missing supervisor contracts.

- [ ] **Step 3: Update operator and architecture documentation**

Replace the continuous-mode rejection, add copyable examples for Rich/plain/JSONL/final
JSON, document discovery/backoff/retention/session cap behavior, and add an `Unreleased`
changelog entry. Do not bump or publish a release in this feature commit.

```markdown
## Unreleased

### Added

- Continuously discover and monitor concurrent Hugging Face downloads with
  `attach --all`, adaptive aggregate output, and privacy-safe JSONL events.
```

- [ ] **Step 4: Run the complete local verification matrix**

```powershell
python -m pytest -q
python -m ruff format --check .
python -m ruff check .
python -m pyright
git diff --check
```

Expected: all tests pass; Ruff and Pyright report zero errors; diff check is clean.

- [ ] **Step 5: Run local consumer simulations repeatedly**

Run the multi-download integration four consecutive times and run the Docker simulation
four consecutive times. Expected: eight successful runs, deterministic final event
counts, no leaked child processes, and no structured-output contamination.

- [ ] **Step 6: Commit documentation**

```powershell
git add README.md docs/user-manual.md docs/architecture.md CHANGELOG.md tests/test_docs.py
git commit -S -m "docs: explain continuous multi-download monitoring"
```

- [ ] **Step 7: Review exact delta and open protected PR**

Inspect `git diff origin/main...HEAD`, run a confidential-pattern scan over the exact
delta, push `feature/multi-repo-supervisor`, and open a PR listing test evidence and
non-goals. Merge only after every required and optional GitHub check is green.

- [ ] **Step 8: Verify post-merge state**

Wait for post-merge CI, confirm `main` equals the merge commit, confirm branch protection
and required signatures remain enabled, and preserve the user's unrelated local
`.dockerignore` modification. Release versioning and protected publication require a
separate explicit promotion decision after this feature phase is green on `main`.
