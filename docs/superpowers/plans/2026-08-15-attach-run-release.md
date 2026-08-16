# Attach, Managed Run, and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cross-platform attachment to existing Hugging Face downloads, managed download launching, and reproducible standalone/release workflows to the tested portable watch core.

**Architecture:** Platform process providers emit privacy-safe normalized process records. A pure Hugging Face command parser converts records into `DownloadSpec` candidates, while attach orchestration selects and monitors them through the existing engine. Managed run launches the official CLI with an explicit local directory and uses the same monitoring pipeline while preserving subprocess exit and interruption semantics.

**Tech Stack:** Python 3.10+, standard-library subprocess and POSIX `/proc`, PowerShell CIM on Windows, Typer, Rich, pytest, Ruff, Pyright, PyInstaller, GitHub Actions.

---

### Task 1: Normalize and parse Hugging Face download processes

**Files:**
- Create: `src/hf_download_live_monitor/processes.py`
- Create: `src/hf_download_live_monitor/hf_command.py`
- Create: `tests/test_hf_command.py`

- [ ] Write failing tests covering `hf download`, executable paths, `python -m huggingface_hub.commands.huggingface_cli download`, separated and equals-style options, model/dataset/Space types, revision, filenames, include/exclude filters, relative local directories, unrelated commands, and token redaction.
- [ ] Run `python -m pytest tests/test_hf_command.py -v` and confirm missing-module failure.
- [ ] Implement frozen `ProcessRecord(pid, args, cwd)` and pure `parse_download_process(record) -> DownloadSpec | None`; resolve relative destinations against `cwd`, reject missing local directories, and never retain token arguments.
- [ ] Run focused tests, Ruff, and Pyright.

### Task 2: Discover processes on Windows and POSIX

**Files:**
- Modify: `src/hf_download_live_monitor/processes.py`
- Create: `tests/test_processes.py`

- [ ] Write failing fixture-driven tests for Linux `/proc/<pid>/cmdline` and `/proc/<pid>/cwd`, disappearing processes, permission failures, Windows CIM JSON records, malformed records, and provider selection.
- [ ] Run focused tests and confirm behavioral failures.
- [ ] Implement `PosixProcessProvider`, `WindowsProcessProvider`, and `system_process_provider`. POSIX reads only numeric process directories. Windows invokes a fixed PowerShell CIM query returning PID, command line, and executable path as JSON; parsing uses `CommandLineToArgvW` on Windows and a documented fallback only in tests.
- [ ] Ensure discovery returns classified warnings rather than failing the entire scan because one process is inaccessible.
- [ ] Run focused tests, Ruff, Pyright, and the full suite.

### Task 3: Add attach selection and monitoring

**Files:**
- Create: `src/hf_download_live_monitor/attach.py`
- Modify: `src/hf_download_live_monitor/cli.py`
- Create: `tests/test_attach.py`
- Modify: `tests/test_cli.py`

- [ ] Write failing tests for no matches, one automatic match, PID selection, ambiguous non-interactive selection, interactive numeric selection, and `--all` validation.
- [ ] Run tests and confirm missing attach behavior.
- [ ] Implement discovery-to-candidate orchestration with stable PID ordering and safe summaries. Reuse `WatchApplication` for a selected process; support `--pid`; reject simultaneous `--pid` and `--all`. Initial `--all` emits a classified limitation unless more than one renderer can be safely coordinated.
- [ ] Add `hf-download-live-monitor attach` with the common renderer and timing options.
- [ ] Run focused and full validation.

### Task 4: Implement managed download launching

**Files:**
- Create: `src/hf_download_live_monitor/runner.py`
- Modify: `src/hf_download_live_monitor/cli.py`
- Create: `tests/test_runner.py`
- Modify: `tests/test_cli.py`

- [ ] Write failing tests around an injected `Popen` factory for exact argument forwarding, mandatory `--local-dir`, repository type, revision and filters, token-safe errors, successful and failing exit propagation, graceful first interruption, and forced repeated interruption.
- [ ] Run focused tests and confirm missing runner behavior.
- [ ] Implement `ManagedDownload` with injected process factory. Launch `hf download`, track the exact child, run monitoring concurrently through a small watcher thread, terminate on first interrupt, kill on repeated interrupt, join cleanly, and return the child exit code.
- [ ] Add `hf-download-live-monitor run REPO --local-dir PATH [OPTIONS]` using explicit supported download options rather than unsafe arbitrary shell text.
- [ ] Run focused tests, full tests, Ruff, and Pyright.

### Task 5: Add standalone and trusted-release workflows

**Files:**
- Create: `hf_download_live_monitor.spec`
- Create: `.github/workflows/release.yml`
- Create: `scripts/build_standalone.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Create: `tests/test_release_assets.py`

- [ ] Write failing tests confirming the PyInstaller entrypoint, platform-safe artifact naming, release workflow permissions, tag/version gate, checksums, and PyPI Trusted Publishing environment.
- [ ] Implement a deterministic PyInstaller build script and spec without embedding credentials or local paths.
- [ ] Implement tag-triggered release jobs that rerun quality gates, build Python distributions, publish through `pypa/gh-action-pypi-publish` with OIDC, build standalone executables on Windows/Linux/macOS, smoke-test each executable, generate SHA-256 checksums, and attach artifacts to GitHub Releases.
- [ ] Document `attach`, `run`, standalone artifacts, interruption behavior, and remaining platform limitations.
- [ ] Run release-asset tests and all local quality gates.

### Task 6: Final compatibility and installed-artifact validation

**Files:**
- Modify tests as required only for discovered regressions.

- [ ] Run `python -m ruff format --check .`, `python -m ruff check .`, `python -m pyright`, and `python -m pytest -q`.
- [ ] Build wheel and sdist, then run `python -m twine check dist\*`.
- [ ] Install the wheel into a fresh temporary environment and smoke-test `--help`, `attach --help`, and `run --help`.
- [ ] Build the native Windows executable locally when PyInstaller is available, then run its three help commands. If executable construction is blocked by an external platform/tool limitation, retain verified CI workflows and report the exact limitation rather than claiming a local binary test.
- [ ] Review all command/error output for credentials and local development paths.

## Completion criteria

Phases 2 and 3 are complete when attach discovery and command parsing have deterministic Windows/POSIX tests, managed run correctly forwards supported options and propagates child outcomes, CLI and security regression tests pass, release workflows are structurally tested, Python artifacts build and validate, and installed commands pass clean-environment smoke tests. Git operations and public publishing remain deferred until the user explicitly configures repository identity and release ownership.
