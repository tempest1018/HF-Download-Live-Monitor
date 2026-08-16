# HF Download Live Monitor Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Perform a clean, breaking rename of every current project identity to HF Download Live Monitor and restore fully green local and GitHub validation.

**Architecture:** Move the import package atomically, then update packaging, executable, workflow, documentation, and repository identities around it. Tests define the new public identity before implementation; historical prototype bytes remain untouched.

**Tech Stack:** Python 3.10-3.13, Hatchling, Typer, PyInstaller, pytest, Ruff, Pyright, GitHub Actions.

---

### Task 1: Lock the new public identity in tests

**Files:**
- Modify: `tests/test_package.py`
- Modify: `tests/test_release_assets.py`
- Modify: `tests/test_docs.py`

- [ ] Replace old distribution, command, import package, executable, and documentation expectations with `hf-download-live-monitor`, `hf_download_live_monitor`, and `HF Download Live Monitor`.
- [ ] Run `python -m pytest tests/test_package.py tests/test_release_assets.py tests/test_docs.py -q` and confirm it fails because implementation still uses the old identity.

### Task 2: Rename source and packaging surfaces

**Files:**
- Move: `src/hf_download_live_monitor` to `src/hf_download_live_monitor`
- Move: `hf_download_live_monitor.spec` to `hf_download_live_monitor.spec`
- Modify: `pyproject.toml`
- Modify: `scripts/build_standalone.py`
- Modify: all Python imports under `src` and `tests`

- [ ] Move the package and spec using Git-aware moves.
- [ ] Replace internal imports and metadata with the canonical identifiers.
- [ ] Change the console script and standalone filename to `hf-download-live-monitor`.
- [ ] Run the three identity tests and confirm they pass.

### Task 3: Rename workflows and documentation

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `README.md`
- Modify: `docs/user-manual.md`
- Modify: `docs/architecture.md`
- Modify: `docs/json-schema.md`
- Modify: `SECURITY.md`
- Modify: `CONTRIBUTING.md`
- Modify: `CHANGELOG.md`
- Modify: `LICENSE`
- Modify: current design and plan documents under `docs/superpowers`

- [ ] Update commands, artifact names, module invocations, prose, and installation instructions.
- [ ] Add a migration note that explicitly tells existing users to uninstall `hf-download-live-monitor` and install `hf-download-live-monitor`.
- [ ] Preserve `docs/prototypes` bytes and recorded checksum.
- [ ] Run the documentation and release-asset tests.

### Task 4: Audit and verify locally

**Files:**
- Test: entire repository

- [ ] Search for legacy identifiers and classify only the migration note and preserved prototype references as allowed.
- [ ] Parse both workflow YAML files.
- [ ] Run `python -m ruff format --check .`, `python -m ruff check .`, `python -m pyright`, and `python -m pytest -q`.
- [ ] Run `python -m build` and Twine checks for the renamed wheel and source distribution.
- [ ] Install the wheel in a clean temporary environment and execute `hf-download-live-monitor --help`, `watch --help`, `attach --help`, and `run --help`.
- [ ] Scan the exact Git delta for credentials, personal paths, and personal email addresses.

### Task 5: Commit, rename local workspace, and deliver

**Files:**
- Modify: Git metadata and workspace path only

- [ ] Commit the atomic rename on `main`.
- [ ] Set `origin` to `https://github.com/tempest1018/HF-Download-Live-Monitor.git` and verify fetch/push routing.
- [ ] Rename the local directory to `hf-download-live-monitor` after all commands using the old working path have completed.
- [ ] Push `main` under the user's standing authorization.
- [ ] Monitor the resulting GitHub Actions run through completion and query every job annotation.
- [ ] If any job or annotation fails, reproduce it, add a regression test, correct it, revalidate, commit, push, and monitor again until all jobs pass with zero annotations.
