# User Manual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a tested, task-oriented installation and operations manual for every supported HF Live Monitor workflow.

**Architecture:** Keep the README concise and link it prominently to one authoritative manual. Protect essential documentation coverage with lightweight tests, then perform three explicit editorial audits against the real CLI and security behavior.

**Tech Stack:** Markdown, pytest, Typer CLI help, Git.

---

### Task 1: Define documentation coverage contracts

**Files:**
- Modify: `tests/test_docs.py`

- [ ] Add a test that reads `docs/user-manual.md`, verifies the README links to it, and asserts coverage headings for prerequisites, authentication, install, verify, quick start, watch, attach, run, output, update, downgrade, uninstall, troubleshooting, privacy, and support.
- [ ] Run `python -m pytest tests/test_docs.py -v` and confirm failure because the manual does not exist.

### Task 2: Write and link the complete manual

**Files:**
- Create: `docs/user-manual.md`
- Modify: `README.md`

- [ ] Add a prominent manual link near the README introduction.
- [ ] Write copyable installation and authentication paths for standalone executable, pipx, uv, pip, and development installs.
- [ ] Document verification, first use, every CLI mode and repository-selection option, output formats, platform differences, signals, updates, downgrades, uninstalling, error-code recovery, privacy, diagnostic collection, and support boundaries.
- [ ] Run `python -m pytest tests/test_docs.py -v` and confirm all documentation contracts pass.

### Task 3: Perform three editorial passes

**Files:**
- Modify: `docs/user-manual.md`
- Modify: `README.md`

- [ ] Pass 1: compare every command and option against `hf-live-monitor --help` plus each command help; correct technical mismatches and missing prerequisites.
- [ ] Pass 2: follow the manual as a first-time Windows user and as a WSL/Linux user; improve navigation, ordering, copy-paste safety, expected results, and recovery instructions.
- [ ] Pass 3: scan for credentials, personal paths, ambiguous claims, inconsistent terminology, repetition, and unsupported behavior; tighten language without removing necessary detail.
- [ ] Run `python -m ruff format --check .`, `python -m ruff check .`, `python -m pyright`, and `python -m pytest -q`.
- [ ] Run the exact commit privacy and forbidden-path scans, amend the local commit, and stop before pushing.

## Completion criteria

The README offers a fast path and unmistakable manual link; the manual independently enables installation through every supported channel, first successful operation, ongoing maintenance, and safe failure recovery; all three editorial passes and all project checks pass; and no upload occurs without renewed approval.
