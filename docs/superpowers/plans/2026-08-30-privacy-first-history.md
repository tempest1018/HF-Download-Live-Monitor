# Privacy-First Local History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in, privacy-preserving local download history with complete user control, safe failure behavior, and a second-PC/VM acceptance guide.

**Architecture:** Immutable history contracts feed a standard-library SQLite store through a fail-open recorder protocol. Watch and supervisor orchestration emit aggregate lifecycle checkpoints without knowing SQL, while a dedicated Typer command group owns configuration, queries, exports, recovery, and destructive user-authorized operations.

**Tech Stack:** Python 3.10+, `sqlite3`, `hmac`, `hashlib`, `secrets`, Typer, Rich, pytest, Ruff, strict Pyright, Docker, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-28-privacy-first-history-design.md`

## Global Constraints

- History is disabled by default and ordinary monitoring must create no history files until explicitly enabled.
- All history remains local; there is no telemetry, account, synchronization, or history-related network request.
- Default retention is exactly 30 days and the default database-size cap is exactly 64 MiB.
- Default records and exports contain no readable repository name, local path, credential, command line, environment value, filename, file digest, downloaded content, process ID, or process start token.
- Identifier recording is per-invocation opt-in and never becomes a global default.
- Monitoring and downloader exit behavior must not change when history is locked, corrupt, unavailable, or full.
- Python 3.10 remains the minimum supported version and Phase 8 adds no runtime dependency.
- History files use platform state conventions, absolute overrides only, no symlinked database/key files, Unix directory mode `0700`, and Unix file mode `0600`.
- Structured progress schema version 2 and supervisor schema version 1 remain unchanged; history output has an independent schema version.
- Destructive history operations require interactive confirmation or `--yes`, accurately report their scope, and never silently recreate purged data.
- Existing Windows, Linux, macOS, x86-64, ARM64, Docker, packaging, signing, and release privacy gates remain mandatory.

---

### Task 1: Immutable history contracts and platform-safe paths

**Files:**
- Create: `src/hf_download_live_monitor/history_models.py`
- Create: `src/hf_download_live_monitor/history_paths.py`
- Create: `tests/test_history_models.py`
- Create: `tests/test_history_paths.py`

**Interfaces:**
- Produces: `HistoryConfig`, `HistoryOutcome`, `HistoryCheckpoint`, `HistoryRecord`, `HistoryQuery`, `HistoryHealth`, `HistoryPaths`, `resolve_history_paths()`, and `ensure_private_history_directory()`.
- Consumes: existing `RepoType`, `ErrorCategory`, and `MonitorError` vocabulary only.

- [ ] **Step 1: Write failing contract validation tests**

```python
def test_history_defaults_are_privacy_first() -> None:
    config = HistoryConfig.defaults()
    assert config.enabled is False
    assert config.retention_days == 30
    assert config.max_size_bytes == 64 * 1024 * 1024
    assert config.include_identifiers is False


def test_checkpoint_rejects_direct_identifiers_without_opt_in() -> None:
    with pytest.raises(ValueError, match="identifier opt-in"):
        HistoryCheckpoint.start(
            session_id="session-1",
            mode="watch",
            repo_type=RepoType.MODEL,
            repository_hmac="a" * 64,
            destination_hmac="b" * 64,
            repository="private/repo",
            local_dir="C:/private/model",
            include_identifiers=False,
            observed_at_utc=1_800_000_000.0,
        )
```

- [ ] **Step 2: Run the model tests and confirm the missing-module failure**

Run: `python -m pytest tests/test_history_models.py -q`

Expected: FAIL because `history_models` does not exist.

- [ ] **Step 3: Implement frozen, validated contracts**

```python
@dataclass(frozen=True, slots=True)
class HistoryConfig:
    enabled: bool = False
    retention_days: int | None = 30
    max_size_bytes: int = 64 * 1024 * 1024
    include_identifiers: bool = False

    def __post_init__(self) -> None:
        if self.retention_days is not None and self.retention_days < 1:
            raise ValueError("retention days must be positive or unlimited")
        if self.max_size_bytes < 1024 * 1024:
            raise ValueError("history size cap must be at least 1 MiB")

    @classmethod
    def defaults(cls) -> HistoryConfig:
        return cls()
```

Define terminal outcomes `completed`, `failed`, `lost`, `cancelled`, and `interrupted`; waiting classifications `progressing`, `waiting_for_data`, `finalizing`, and `interrupted`; and frozen checkpoint/record/query values with non-negative byte, rate, duration, and count validation. Limit persisted diagnostic messages to 512 characters.

- [ ] **Step 4: Write failing platform path and symlink tests**

```python
@pytest.mark.parametrize(
    ("platform", "environment", "suffix"),
    [
        ("win32", {"LOCALAPPDATA": "C:/Local"}, "HF Download Live Monitor/history"),
        ("darwin", {"HOME": "/Users/tester"}, "Library/Application Support/HF Download Live Monitor/history"),
        ("linux", {"HOME": "/home/tester"}, ".local/state/hf-download-live-monitor/history"),
    ],
)
def test_default_history_root_is_platform_native(platform, environment, suffix) -> None:
    assert resolve_history_paths(platform=platform, environment=environment).directory.as_posix().endswith(suffix)


def test_relative_override_is_rejected() -> None:
    with pytest.raises(MonitorError, match="absolute"):
        resolve_history_paths(override=Path("relative/history.db"))
```

- [ ] **Step 5: Implement deterministic path resolution and private directory creation**

```python
@dataclass(frozen=True, slots=True)
class HistoryPaths:
    directory: Path
    database: Path
    pseudonym_key: Path


def resolve_history_paths(
    *, override: Path | None = None, platform: str = sys.platform,
    environment: Mapping[str, str] = os.environ,
) -> HistoryPaths:
    if override is not None and not override.is_absolute():
        raise MonitorError("history_path_invalid", "history path must be absolute")
    # Resolve the approved platform root without creating it.
```

Reject existing symlinked database/key paths before opening them. Create the directory only from an explicit history mutation or enabled recorder, apply Unix modes, and verify resolved children remain beneath the selected directory.

- [ ] **Step 6: Run and commit Task 1**

Run: `python -m pytest tests/test_history_models.py tests/test_history_paths.py -q`

Expected: PASS.

```powershell
git add src/hf_download_live_monitor/history_models.py src/hf_download_live_monitor/history_paths.py tests/test_history_models.py tests/test_history_paths.py
git commit -S -m "feat: define privacy-first history contracts"
```

### Task 2: SQLite schema, settings, migrations, and pseudonyms

**Files:**
- Create: `src/hf_download_live_monitor/history_store.py`
- Create: `tests/test_history_store.py`

**Interfaces:**
- Consumes: `HistoryConfig`, `HistoryCheckpoint`, `HistoryPaths`.
- Produces: `HistoryStore.open(paths, *, create)`, `load_config()`, `save_config()`, `pseudonymize()`, `checkpoint()`, `finalize()`, and `close()`.

- [ ] **Step 1: Write failing disabled/create and schema tests**

```python
def test_open_without_create_does_not_touch_disk(tmp_path: Path) -> None:
    paths = paths_under(tmp_path)
    assert HistoryStore.open(paths, create=False) is None
    assert not tmp_path.exists()


def test_create_initializes_versioned_wal_database(tmp_path: Path) -> None:
    store = required_store(tmp_path)
    assert store.schema_version == 1
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
```

- [ ] **Step 2: Run the store tests and confirm failure**

Run: `python -m pytest tests/test_history_store.py -q`

Expected: FAIL because `HistoryStore` is undefined.

- [ ] **Step 3: Implement schema creation and typed settings**

```python
SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE metadata(schema_version INTEGER NOT NULL, created_at_utc REAL NOT NULL);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sessions(
  session_id TEXT PRIMARY KEY, mode TEXT NOT NULL,
  repository_hmac TEXT NOT NULL, destination_hmac TEXT NOT NULL,
  repository_label TEXT NOT NULL, destination_label TEXT NOT NULL,
  repository_identifier TEXT, destination_identifier TEXT,
  repo_type TEXT NOT NULL, revision_kind TEXT NOT NULL,
  started_at_utc REAL NOT NULL, updated_at_utc REAL NOT NULL, ended_at_utc REAL,
  outcome TEXT, expected_bytes INTEGER NOT NULL, downloaded_bytes INTEGER NOT NULL,
  average_rate REAL NOT NULL, peak_rate REAL NOT NULL,
  waiting_seconds REAL NOT NULL, longest_wait_seconds REAL NOT NULL,
  verified_files INTEGER NOT NULL, unverified_files INTEGER NOT NULL,
  failed_files INTEGER NOT NULL, interrupted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE diagnostics(
  id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
  observed_at_utc REAL NOT NULL, category TEXT NOT NULL, code TEXT NOT NULL,
  message TEXT NOT NULL, recoverable INTEGER NOT NULL
);
"""
```

Use URI read-only connections for queries when creation is not authorized. Set SQLite's
connection and `PRAGMA busy_timeout` limits to 100 milliseconds so history cannot stall a
monitor refresh indefinitely. Encode settings with strict parsers; unknown keys are
preserved, invalid required values yield `history_config_invalid`, and
`include_identifiers` is never persisted as enabled.

- [ ] **Step 4: Add migration atomicity and newer-schema tests**

```python
def test_failed_migration_rolls_back_schema_version(store_v0, monkeypatch) -> None:
    monkeypatch.setattr(history_store, "MIGRATIONS", {1: "INVALID SQL"})
    with pytest.raises(MonitorError, match="migration"):
        HistoryStore.open(store_v0.paths, create=True)
    assert raw_schema_version(store_v0.paths.database) == 0


def test_newer_schema_is_refused_without_writes(tmp_path: Path) -> None:
    make_database(tmp_path, schema_version=SCHEMA_VERSION + 1)
    with pytest.raises(MonitorError, match="newer"):
        HistoryStore.open(paths_under(tmp_path), create=False)
```

- [ ] **Step 5: Implement transactional migrations and local HMAC key handling**

```python
def pseudonymize(self, value: str, *, label: str) -> tuple[str, str]:
    digest = hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest, f"{label}-{digest[:8]}"
```

Generate exactly 32 random bytes with `secrets.token_bytes(32)`, write them with exclusive creation and restrictive permissions, reject wrong key length, never return the key through CLI output, and prove the same input groups consistently while different keys do not.

- [ ] **Step 6: Run and commit Task 2**

Run: `python -m pytest tests/test_history_store.py tests/test_history_paths.py -q`

Expected: PASS.

```powershell
git add src/hf_download_live_monitor/history_store.py tests/test_history_store.py
git commit -S -m "feat: add versioned local history store"
```

### Task 3: Queries, retention, capacity, deletion, and recovery

**Files:**
- Modify: `src/hf_download_live_monitor/history_store.py`
- Modify: `tests/test_history_store.py`
- Create: `tests/test_history_recovery.py`

**Interfaces:**
- Produces: `list_records(query)`, `get_record(session_id)`, `delete(session_id)`, `clear_before(cutoff)`, `purge()`, `vacuum()`, `recover(output)`, `reset_preserving_corrupt()`, and `health()`.

- [ ] **Step 1: Write failing deterministic CRUD and retention tests**

```python
def test_list_filters_and_orders_newest_first(store) -> None:
    seed_records(store, outcomes=("failed", "completed", "failed"))
    records = store.list_records(HistoryQuery(outcomes=(HistoryOutcome.FAILED,), limit=10))
    assert [record.session_id for record in records] == ["session-3", "session-1"]


def test_retention_deletes_old_terminal_rows_but_not_active(store) -> None:
    seed_old_terminal_and_active(store)
    store.enforce_limits(now_utc=1_800_000_000.0)
    assert store.get_record("old-terminal") is None
    assert store.get_record("old-active") is not None
```

- [ ] **Step 2: Run targeted tests and confirm missing operations**

Run: `python -m pytest tests/test_history_store.py -k "list or retention or capacity or delete" -q`

Expected: FAIL with missing store methods.

- [ ] **Step 3: Implement bounded queries and transactional lifecycle operations**

```python
def delete(self, session_id: str) -> bool:
    with self._transaction():
        cursor = self._connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    return cursor.rowcount == 1
```

Parameterize every query, cap list/export batches at 1,000 records per database fetch,
remove oldest terminal rows before size failure, checkpoint WAL before measuring size, and
return structured counts for destructive operations. Parse `history clear --before` as an
ISO 8601 calendar date with a UTC midnight boundary. `purge()` must close connections
before deleting only the exact database, `-wal`, `-shm`, and pseudonym-key paths. It may
remove the dedicated default history directory when empty, but never removes the parent of
a custom database path.

- [ ] **Step 4: Write corruption and non-overwriting recovery tests**

```python
def test_corruption_is_reported_without_modifying_source(tmp_path: Path) -> None:
    database = write_corrupt_database(tmp_path)
    before = database.read_bytes()
    assert inspect_health(database).status is HistoryHealth.CORRUPT
    assert database.read_bytes() == before


def test_recovery_refuses_existing_output(store, tmp_path: Path) -> None:
    output = tmp_path / "existing.db"
    output.write_bytes(b"keep")
    with pytest.raises(MonitorError, match="already exists"):
        store.recover(output)
    assert output.read_bytes() == b"keep"
```

- [ ] **Step 5: Implement health, backup recovery, and preserve-first reset**

Use `PRAGMA quick_check` for health. Recovery writes only to a new absolute output through `sqlite3.Connection.backup`, validates schema and `quick_check`, and removes an invalid candidate without touching the source. Reset moves the corrupt database and sidecars to timestamped names within the same verified directory before creating a new enabled store; cross-directory or overwrite moves are prohibited.

- [ ] **Step 6: Run and commit Task 3**

Run: `python -m pytest tests/test_history_store.py tests/test_history_recovery.py -q`

Expected: PASS.

```powershell
git add src/hf_download_live_monitor/history_store.py tests/test_history_store.py tests/test_history_recovery.py
git commit -S -m "feat: give users complete history lifecycle control"
```

### Task 4: Fail-open recorder and privacy-safe aggregation

**Files:**
- Create: `src/hf_download_live_monitor/history_recorder.py`
- Create: `tests/test_history_recorder.py`
- Modify: `src/hf_download_live_monitor/security.py`
- Modify: `tests/test_security.py`

**Interfaces:**
- Produces: `HistoryRecorder` protocol; `NullHistoryRecorder`; `SQLiteHistoryRecorder`; methods `start(spec, mode, observed_at_utc) -> str`, `checkpoint(session_id, snapshot, observed_at_utc, *, final)`, `diagnostic(session_id, error, observed_at_utc)`, `interrupt(session_id, observed_at_utc)`, and `close()`.

- [ ] **Step 1: Write failing aggregation, cadence, and canary tests**

```python
def test_recorder_checkpoints_at_most_every_five_seconds(store, snapshot) -> None:
    recorder = SQLiteHistoryRecorder(store, utc_clock=sequence(100.0, 101.0, 105.0))
    session_id = recorder.start(snapshot.spec, "watch", 100.0)
    recorder.checkpoint(session_id, snapshot, 101.0, final=False)
    recorder.checkpoint(session_id, snapshot, 105.0, final=False)
    assert store.checkpoint_times(session_id) == [100.0, 105.0]


def test_default_database_never_contains_sensitive_canaries(tmp_path: Path) -> None:
    record_private_download(tmp_path, repo="secret-owner/private-model", token="hf_" + "x" * 40)
    blob = database_bytes_after_checkpoint(tmp_path)
    assert b"secret-owner" not in blob
    assert b"private-model" not in blob
    assert b"hf_" + b"x" * 40 not in blob
```

- [ ] **Step 2: Run recorder tests and confirm failure**

Run: `python -m pytest tests/test_history_recorder.py -q`

Expected: FAIL because recorder implementations do not exist.

- [ ] **Step 3: Strengthen persistence redaction and implement aggregation**

```python
class HistoryRecorder(Protocol):
    def start(self, spec: DownloadSpec, mode: str, observed_at_utc: float) -> str: ...
    def checkpoint(self, session_id: str, snapshot: ProgressSnapshot, observed_at_utc: float, *, final: bool) -> None: ...
    def interrupt(self, session_id: str, observed_at_utc: float) -> None: ...
    def close(self) -> None: ...


class NullHistoryRecorder:
    def start(self, spec, mode, observed_at_utc): return ""
    def checkpoint(self, session_id, snapshot, observed_at_utc, *, final): return None
```

Add `sanitize_persisted_diagnostic()` to redact credentials, remove control characters, replace absolute paths, reject token-shaped residues, and limit output to 512 characters. Derive average/peak rate, waiting duration, longest no-progress interval, aggregate counts, and final outcome without persisting file-level data.

- [ ] **Step 4: Write fail-open tests for locked, corrupt, and full stores**

```python
@pytest.mark.parametrize("failure", [sqlite3.OperationalError("locked"), OSError("disk full")])
def test_recorder_failure_disables_only_history(store, snapshot, failure) -> None:
    store.fail_next_write(failure)
    recorder = SQLiteHistoryRecorder(store)
    recorder.checkpoint("session", snapshot, 100.0, final=False)
    assert recorder.available is False
    assert recorder.warning.code in {"history_busy", "history_write_failed"}
```

- [ ] **Step 5: Implement bounded exception mapping and stale-session interruption**

Catch SQLite/I/O failures at the recorder boundary, retain one sanitized warning per run, stop further writes for that recorder, and never raise into monitor orchestration. On successful store open, mark stale active sessions interrupted before accepting new sessions.

- [ ] **Step 6: Run and commit Task 4**

Run: `python -m pytest tests/test_history_recorder.py tests/test_security.py -q`

Expected: PASS.

```powershell
git add src/hf_download_live_monitor/history_recorder.py src/hf_download_live_monitor/security.py tests/test_history_recorder.py tests/test_security.py
git commit -S -m "feat: record privacy-safe download summaries"
```

### Task 5: Integrate recording with watch and managed run

**Files:**
- Modify: `src/hf_download_live_monitor/app.py`
- Modify: `src/hf_download_live_monitor/runner.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_runner.py`

**Interfaces:**
- Consumes: `HistoryRecorder` with default `NullHistoryRecorder`.
- Produces: identical `WatchApplication.run()` and `ManagedDownload.run()` exit semantics plus history lifecycle calls.

- [ ] **Step 1: Write failing watch lifecycle tests**

```python
def test_watch_records_start_checkpoints_and_final_outcome() -> None:
    history = RecordingHistoryRecorder()
    app = make_application(history=history, snapshots=(active_snapshot(), complete_snapshot()))
    assert app.run(spec(), stop_when=lambda: True) == 0
    assert history.calls == ["start:watch", "checkpoint", "final:completed", "close"]


def test_history_failure_never_changes_integrity_exit_code() -> None:
    app = make_application(history=FailingHistoryRecorder(), snapshots=(failed_snapshot(),))
    assert app.run(spec(), once=True) == exit_code_for(ErrorCategory.INTEGRITY)
```

- [ ] **Step 2: Run watch/runner tests and confirm missing integration**

Run: `python -m pytest tests/test_app.py tests/test_runner.py -k history -q`

Expected: FAIL because constructors do not accept a recorder.

- [ ] **Step 3: Add recorder injection and lifecycle calls**

```python
class WatchApplication:
    def __init__(..., history: HistoryRecorder | None = None, mode: str = "watch") -> None:
        self._history = history or NullHistoryRecorder()
        self._mode = mode
```

Start only after plan preparation succeeds, checkpoint after engine updates, finalize on every final observation, mark interruption when no final snapshot is possible, and close history after the final recorder call without changing existing renderer/engine/control cleanup precedence. `ManagedDownload` identifies the application mode as `run` without storing its child command or PID.

- [ ] **Step 4: Add cancellation, exception, cleanup, and no-op regression tests**

Cover `once`, stop condition, dashboard `q`, Ctrl+C, repository failure, renderer failure, integrity failure, child failure, and cleanup failure. Assert recorder failures never mask the original exception or exit code and default construction writes nothing.

- [ ] **Step 5: Run and commit Task 5**

Run: `python -m pytest tests/test_app.py tests/test_runner.py -q`

Expected: PASS.

```powershell
git add src/hf_download_live_monitor/app.py src/hf_download_live_monitor/runner.py tests/test_app.py tests/test_runner.py
git commit -S -m "feat: connect local history to watch and run"
```

### Task 6: Integrate recording with single and continuous attachment

**Files:**
- Modify: `src/hf_download_live_monitor/supervisor.py`
- Modify: `tests/test_supervisor.py`
- Modify: `tests/test_attach.py`
- Modify: `tests/integration/test_multi_download_supervisor.py`

**Interfaces:**
- Consumes: one `HistoryRecorder` shared by the supervisor run.
- Produces: one unrelated persistent UUID per admitted supervisor session and terminal history for completed, failed, lost, cancelled, and interrupted sessions.

- [ ] **Step 1: Write failing multi-session recorder tests**

```python
def test_supervisor_records_each_session_without_persisting_process_identity() -> None:
    history = RecordingHistoryRecorder()
    supervisor = make_supervisor(history=history, candidates=(candidate(pid=123), candidate(pid=456)))
    supervisor.tick()
    assert len(history.started) == 2
    assert all(item.persistent_id not in {"123", "456"} for item in history.started)


def test_lost_and_failed_sessions_receive_distinct_outcomes() -> None:
    history = exercise_disappearing_sessions(lost=True, failed=True)
    assert history.outcomes == {"repo-a": "lost", "repo-b": "failed"}
```

- [ ] **Step 2: Run supervisor history tests and confirm failure**

Run: `python -m pytest tests/test_supervisor.py tests/test_attach.py -k history -q`

Expected: FAIL because `DownloadSupervisor` has no recorder boundary.

- [ ] **Step 3: Add recorder state to `_SessionRuntime` and lifecycle transitions**

```python
@dataclass(slots=True)
class _SessionRuntime:
    candidate: DownloadCandidate
    session_id: str
    history_id: str = ""
```

Generate the persistent ID through the recorder, checkpoint only prepared sessions with aggregate progress, finalize from `_finish`, and interrupt unfinished sessions during shutdown. Never pass `SessionKey`, PID, process start token, command arguments, or filenames to the store.

- [ ] **Step 4: Extend the real incremental multi-download integration test**

Run the existing two-download fixture with a temporary enabled store. Assert two terminal records, valid aggregate byte/rate/count summaries, no readable repository/path canaries in default storage, and complete cleanup of supervisor resources.

- [ ] **Step 5: Run and commit Task 6**

Run: `python -m pytest tests/test_supervisor.py tests/test_attach.py tests/integration/test_multi_download_supervisor.py -q`

Expected: PASS.

```powershell
git add src/hf_download_live_monitor/supervisor.py tests/test_supervisor.py tests/test_attach.py tests/integration/test_multi_download_supervisor.py
git commit -S -m "feat: record continuous attachment outcomes"
```

### Task 7: History policy resolution and CLI command group

**Files:**
- Create: `src/hf_download_live_monitor/history_cli.py`
- Modify: `src/hf_download_live_monitor/cli.py`
- Create: `tests/test_history_cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `history_cli` Typer group, `resolve_history_policy()`, `make_history_recorder()`, and monitoring options `--record-history`, `--no-record-history`, `--include-identifiers`, `--history-path`.
- Consumes: store CRUD/configuration/recovery interfaces.

- [ ] **Step 1: Write failing command discovery and opt-in tests**

```python
def test_help_exposes_history_without_enabling_it() -> None:
    result = runner.invoke(cli, ["history", "--help"])
    assert result.exit_code == 0
    assert all(name in result.stdout for name in ("status", "enable", "disable", "list", "show", "export", "delete", "clear", "purge", "vacuum", "recover", "reset"))


def test_watch_default_does_not_create_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_DOWNLOAD_LIVE_MONITOR_HISTORY_DIR", str(tmp_path / "state"))
    invoke_one_shot_watch()
    assert not (tmp_path / "state").exists()
```

- [ ] **Step 2: Run CLI tests and confirm missing group/options**

Run: `python -m pytest tests/test_history_cli.py tests/test_cli.py -q`

Expected: FAIL because the history group is not registered.

- [ ] **Step 3: Implement effective policy and monitoring options**

```python
history_cli = typer.Typer(no_args_is_help=True, help="Control private local history.")
cli.add_typer(history_cli, name="history")


def resolve_history_policy(*, record: bool | None, include_identifiers: bool, path: Path | None) -> EffectiveHistoryPolicy:
    if include_identifiers and record is False:
        raise typer.BadParameter("--include-identifiers requires history recording")
    # Read existing settings without creating state, then apply invocation-only overrides.
```

Add dual boolean options to `watch`, `run`, and `attach`; pass the recorder into both CLI factories. `--record-history` overrides a disabled global setting for one invocation. `--no-record-history` overrides enabled settings without modifying them. Never persist identifier opt-in.

- [ ] **Step 4: Implement configuration, status, and confirmation rules**

`enable` creates the store and saves enabled/default policy. `disable` saves disabled without deleting records. `configure` accepts positive days or literal `unlimited`, and a minimum 1 MiB cap. `status` works for never-enabled, healthy, locked, unsupported, and corrupt states. Destructive commands call `typer.confirm(..., abort=True)` unless `--yes` is supplied; non-interactive destructive invocation without `--yes` exits with a usage error.

- [ ] **Step 5: Add complete CLI behavior tests**

Test absolute path enforcement, env override, status without creation, enable/disable persistence, per-run overrides, identifier constraints, filter parsing, missing session IDs, confirmed/aborted deletion, range clearing, complete purge including sidecars/key, vacuum, recover, and preserve-first reset.

- [ ] **Step 6: Run and commit Task 7**

Run: `python -m pytest tests/test_history_cli.py tests/test_cli.py -q`

Expected: PASS.

```powershell
git add src/hf_download_live_monitor/history_cli.py src/hf_download_live_monitor/cli.py tests/test_history_cli.py tests/test_cli.py
git commit -S -m "feat: add user-controlled history commands"
```

### Task 8: Human and structured history output

**Files:**
- Create: `src/hf_download_live_monitor/history_renderers.py`
- Create: `tests/test_history_renderers.py`
- Modify: `src/hf_download_live_monitor/history_cli.py`

**Interfaces:**
- Produces: `history_record_to_dict(record, *, include_identifiers=False)`, `HistoryTableRenderer`, `HistoryJsonRenderer`, and `HistoryJsonLinesRenderer`.

- [ ] **Step 1: Write failing output schema and redaction tests**

```python
def test_sanitized_export_omits_identifier_fields(record_with_identifiers) -> None:
    payload = history_record_to_dict(record_with_identifiers)
    assert payload["schema_version"] == 1
    assert payload["kind"] == "history_record"
    assert "repository_identifier" not in payload
    assert "destination_identifier" not in payload


def test_identifier_export_requires_explicit_confirmation(cli_store) -> None:
    result = runner.invoke(cli, ["history", "export", "--include-identifiers"], input="n\n")
    assert result.exit_code != 0
    assert "private/repo" not in result.stdout
```

- [ ] **Step 2: Run renderer tests and confirm failure**

Run: `python -m pytest tests/test_history_renderers.py -q`

Expected: FAIL because history renderers do not exist.

- [ ] **Step 3: Implement compact tables and versioned JSON/JSONL**

```python
def history_record_to_dict(record: HistoryRecord, *, include_identifiers: bool = False) -> dict[str, object]:
    payload = {
        "schema_version": 1,
        "kind": "history_record",
        "session_id": record.session_id,
        "repository": {"label": record.repository_label},
        "destination": {"label": record.destination_label},
        "outcome": record.outcome.value,
        "timing": {"started_at_utc": record.started_at_utc, "ended_at_utc": record.ended_at_utc},
    }
    if include_identifiers:
        payload["repository"]["identifier"] = record.repository_identifier
        payload["destination"]["identifier"] = record.destination_identifier
    return payload
```

Add bounded diagnostics, aggregate byte/rate/count fields, waiting explanations that distinguish evidence from possible causes, deterministic ordering, UTC timestamps, and one-record JSON versus streaming JSONL rules.

- [ ] **Step 4: Add token/path canaries to every renderer and export mode**

Assert default tables, JSON, JSONL, status, missing-record errors, and diagnostics never reveal raw identifiers or token-shaped strings. Identifier-bearing output appears only after the exact opt-in plus confirmation path.

- [ ] **Step 5: Run and commit Task 8**

Run: `python -m pytest tests/test_history_renderers.py tests/test_history_cli.py tests/test_security.py -q`

Expected: PASS.

```powershell
git add src/hf_download_live_monitor/history_renderers.py src/hf_download_live_monitor/history_cli.py tests/test_history_renderers.py tests/test_history_cli.py
git commit -S -m "feat: render sanitized history and diagnostics"
```

### Task 9: Documentation and second-PC/VM acceptance guide

**Files:**
- Modify: `README.md`
- Modify: `docs/user-manual.md`
- Modify: `docs/architecture.md`
- Modify: `SECURITY.md`
- Modify: `CHANGELOG.md`
- Create: `docs/second-pc-vm-acceptance.md`
- Modify: `tests/test_docs.py`

**Interfaces:**
- Documents: opt-in semantics, local storage locations, every history command, privacy implications, recovery, deletion, purge, and sanitized external testing.

- [ ] **Step 1: Write failing documentation contract tests**

```python
def test_manual_documents_history_control_and_defaults() -> None:
    manual = Path("docs/user-manual.md").read_text(encoding="utf-8")
    for phrase in ("disabled by default", "30 days", "64 MiB", "history purge", "--include-identifiers"):
        assert phrase in manual


def test_second_pc_guide_never_requests_sensitive_evidence() -> None:
    guide = Path("docs/second-pc-vm-acceptance.md").read_text(encoding="utf-8")
    assert "fresh machine or VM snapshot" in guide
    assert "sanitize" in guide.lower()
    assert "environment dump" not in guide.lower()
```

- [ ] **Step 2: Run docs tests and confirm failure**

Run: `python -m pytest tests/test_docs.py -q`

Expected: FAIL because Phase 8 documentation is absent.

- [ ] **Step 3: Document installation-independent history behavior**

Explain that disabled history creates nothing, `disable` retains user-owned records, `purge` removes all history state, identifiers are HMAC pseudonyms by default, readable identifiers require one-run opt-in, and history failures cannot fail a download. Include Windows/macOS/Linux locations and absolute portable overrides without embedding a maintainer's path.

- [ ] **Step 4: Write the second-PC/VM checklist**

Provide commands to verify `SHA256SUMS`, the signed tag fingerprint, GitHub attestations, executable architecture, disabled-state absence, enable/record/list/show/export/delete/purge behavior, and clean uninstall. Use only a public tiny test fixture and placeholder paths. Tell testers to share only pass/fail steps, application version, OS/architecture, sanitized error code, and checksums—never usernames, home paths, private repository names, tokens, process lists, environment dumps, or downloaded content.

- [ ] **Step 5: Update architecture, security, changelog, and links**

Describe the recorder protocol and fail-open boundary. State that the local pseudonym key is private state, not a credential for network access, and is deleted by purge. Add the guide to README/manual navigation and an Unreleased Phase 8 entry without assigning a release version.

- [ ] **Step 6: Run and commit Task 9**

Run: `python -m pytest tests/test_docs.py -q`

Expected: PASS.

```powershell
git add README.md docs/user-manual.md docs/architecture.md docs/second-pc-vm-acceptance.md SECURITY.md CHANGELOG.md tests/test_docs.py
git commit -S -m "docs: add private history and external acceptance guide"
```

### Task 10: End-to-end verification, Docker, and distribution privacy audit

**Files:**
- Modify: `Dockerfile.test`
- Create: `tests/integration/test_history_lifecycle.py`
- Modify: `tests/test_package.py`
- Modify: `tests/test_release_assets.py`

**Interfaces:**
- Verifies: installed wheel and standalone behavior with history disabled and enabled; complete purge; no bundled machine-local state.

- [ ] **Step 1: Add a failing installed-application lifecycle test**

```python
def test_installed_cli_records_and_purges_simulated_download(tmp_path: Path) -> None:
    result = run_incremental_fixture(history_dir=tmp_path / "history", record=True)
    assert result.returncode == 0
    assert history_list(result.command).record_count == 1
    purge = run_cli("history", "purge", "--yes", env=result.environment)
    assert purge.returncode == 0
    assert not any((tmp_path / "history").glob("*") if (tmp_path / "history").exists() else ())
```

- [ ] **Step 2: Run the integration test and confirm the missing installed flow**

Run: `python -m pytest tests/integration/test_history_lifecycle.py -q`

Expected: FAIL until the fixture and installed CLI history flow are wired.

- [ ] **Step 3: Extend Docker and release structural checks**

Run the deterministic incremental fixture once with history disabled and once with an isolated enabled history directory; validate sanitized export and purge inside the container. Assert wheel/sdist manifests contain no `.db`, `.sqlite`, `-wal`, `-shm`, pseudonym key, local history directory, `.env`, credentials, or absolute build path. Preserve native ARM64 jobs and published-artifact acceptance contracts.

- [ ] **Step 4: Run all local quality gates**

```powershell
python -m ruff format --check .
python -m ruff check .
python -m pyright
python -m pytest -q
python -m build
python -m twine check (Get-ChildItem -File dist\* | ForEach-Object FullName)
```

Expected: zero formatting changes, zero lint/type errors, all tests pass, and wheel/sdist metadata passes.

- [ ] **Step 5: Run four independent Docker lifecycle simulations**

```powershell
docker build --no-cache -f Dockerfile.test -t hf-download-live-monitor:phase8 .
1..4 | ForEach-Object { docker run --rm hf-download-live-monitor:phase8 }
```

Expected: all four containers exercise disabled history, enabled recording, sanitized export, deletion, purge, and report success.

- [ ] **Step 6: Audit the exact Git delta and built payloads**

Scan `origin/main...HEAD`, tracked filenames, wheel members, source-archive members, and payload bytes for Windows/user paths, Unix home paths, emails, private-key markers, GitHub tokens, Hugging Face tokens, credential assignments, history databases, sidecars, pseudonym keys, and machine-local configuration. Investigate every match without printing secrets. The required result is zero personal/sensitive findings; build artifacts remain ignored.

- [ ] **Step 7: Commit final verification wiring**

```powershell
git add Dockerfile.test tests/integration/test_history_lifecycle.py tests/test_package.py tests/test_release_assets.py
git commit -S -m "test: verify private history distributions"
git status --short
git log --show-signature --oneline origin/main..HEAD
```

Expected: clean tracked worktree, only intentionally ignored build outputs, and every Phase 8 commit has a valid project-key signature.

- [ ] **Step 8: Review and GitHub handoff**

Run the repository's code-review workflow, correct all findings with focused signed commits, rerun the complete gates, and present the exact commit range, changed-file list, privacy-scan result, and excluded local files before pushing. Push/PR/release actions follow the user's current authorization and protected workflows. Do not publish a release or PyPI package without a separate explicit approval. After protected-main CI is fully green, prepare—but do not publish—the signed draft candidate and give the user `docs/second-pc-vm-acceptance.md` for their independent machine test.
