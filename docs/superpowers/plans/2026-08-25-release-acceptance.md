# Published Release Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and run a read-only, repeatable acceptance system that proves a published stable GitHub release works on all six supported native targets and as an isolated wheel installation.

**Architecture:** A Python acceptance harness drives the published executable against a deterministic localhost Hugging Face-compatible endpoint, keeping JSON stdout separate from progress stderr and producing a redacted report. A manual GitHub Actions workflow first validates the immutable public bundle and attestations, then distributes that exact bundle to six native jobs and one isolated-wheel job. Workflow contracts, harness tests, local Windows execution, Docker execution, and the remote matrix provide layered evidence without modifying the release or publishing to PyPI.

**Tech Stack:** Python 3.10+, pytest, `http.server`, subprocess, GitHub Actions, GitHub CLI, GitHub artifact attestations, Docker.

---

## File structure

- Create `scripts/run_published_acceptance.py`: deterministic localhost Hub fixture, black-box command runner, stdout/stderr assertions, report writer, and CLI.
- Create `tests/test_published_acceptance.py`: fixture protocol, black-box success/failure, stream separation, path isolation, and report-redaction tests.
- Create `.github/workflows/release-acceptance.yml`: manual public-release validation plus six-platform and wheel matrices.
- Modify `tests/test_release_assets.py`: executable workflow contracts and mutation prohibitions.
- Modify `docs/user-manual.md`: operator instructions and interpretation of acceptance results.
- Modify `tests/test_docs.py`: documentation/CLI consistency checks.

### Task 1: Build the deterministic published-binary harness

**Files:**
- Create: `scripts/run_published_acceptance.py`
- Create: `tests/test_published_acceptance.py`

- [ ] **Step 1: Write failing tests for the localhost Hub fixture**

Add tests that start `HubFixture`, request `/api/models/acceptance/tiny/revision/<40-char-sha>`, and assert the response contains one `config.json` sibling with the exact SHA-256 and size. Send `HEAD` and `GET` to `/acceptance/tiny/resolve/<sha>/config.json`; require `x-repo-commit`, `etag`, and `content-length`, then require the GET body to arrive in multiple chunks.

```python
def test_hub_fixture_serves_pinned_metadata_and_payload() -> None:
    content = b'{"model_type":"acceptance"}\n' * 4096
    with HubFixture(content=content, chunk_size=1024, delay=0.001) as fixture:
        metadata = json.loads(urlopen(f"{fixture.endpoint}/api/models/acceptance/tiny/revision/{REVISION}").read())
        assert metadata["sha"] == REVISION
        assert metadata["siblings"] == [{
            "rfilename": "config.json",
            "size": len(content),
            "lfs": {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)},
        }]
```

- [ ] **Step 2: Run the fixture test and verify RED**

Run: `python -m pytest tests/test_published_acceptance.py::test_hub_fixture_serves_pinned_metadata_and_payload -q`

Expected: FAIL because `scripts.run_published_acceptance` does not exist.

- [ ] **Step 3: Implement the minimal fixture**

Implement a context-managed `ThreadingHTTPServer` bound to `127.0.0.1` on an ephemeral port. Accept only the two exact paths above, reject mutable revisions, emit the required Hugging Face headers, stream the GET body in configured chunks, suppress request logging, and always shut down/join its thread in `__exit__`.

- [ ] **Step 4: Write failing black-box acceptance tests**

Create a temporary fake monitor executable that records arguments and emits a representative JSON snapshot to stdout plus progress to stderr. Assert `run_acceptance()`:

- runs `--help`, `watch --help`, `attach --help`, and `run --help`;
- invokes `run acceptance/tiny --revision <sha> --filename config.json --local-dir <Unicode path> --json --hf-executable <absolute hf path>`;
- supplies `HF_ENDPOINT` only to the child environment;
- parses exactly one JSON value from stdout and rejects leading/trailing text;
- requires progress or downloader activity on stderr;
- requires exit code zero and final complete/verified state;
- writes a report containing no environment dump, token assignment, or authorization header.

Add failure cases for contaminated stdout, nonzero exit, missing final file, timeout, and a report destination inside the checkout.

- [ ] **Step 5: Run the black-box tests and verify RED**

Run: `python -m pytest tests/test_published_acceptance.py -q`

Expected: fixture tests pass; black-box tests fail because `run_acceptance` and the report CLI are absent.

- [ ] **Step 6: Implement the harness and CLI**

Add frozen `AcceptanceResult` and `CommandResult` dataclasses. Stream-capture stdout and stderr separately with a bounded timeout, parse stdout using `json.JSONDecoder().raw_decode`, reject any non-whitespace remainder, validate the final snapshot fields, and write a schema-stable JSON report using only tag, asset, OS, architecture, checksum status, commands, numeric exit codes, and outcome. Redact messages with the existing security helper when importable and a local assignment/Bearer fallback when testing a standalone.

CLI contract:

```text
python scripts/run_published_acceptance.py \
  --monitor <absolute executable> \
  --hf-executable <absolute hf executable> \
  --tag v0.1.0 --asset <release filename> \
  --report <absolute JSON path> --checkout-root <absolute repo path>
```

Reject relative executable/report paths, reports within the checkout, mutable/nonstable tags, and unsupported platform/asset combinations.

- [ ] **Step 7: Verify and commit Task 1**

Run:

```powershell
python -m pytest tests/test_published_acceptance.py -q
python -m ruff format --check scripts/run_published_acceptance.py tests/test_published_acceptance.py
python -m ruff check scripts/run_published_acceptance.py tests/test_published_acceptance.py
```

Expected: all focused tests and checks pass.

Commit:

```powershell
git add scripts/run_published_acceptance.py tests/test_published_acceptance.py
git commit -S -m "test: add published release acceptance harness"
```

### Task 2: Add the manual cross-platform workflow

**Files:**
- Create: `.github/workflows/release-acceptance.yml`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write the failing workflow contract test**

Assert the workflow has only `workflow_dispatch`, a required string `tag`, top-level `contents: read` and `attestations: read`, and no write/id-token/package permissions. Require jobs `validate`, `native`, and `wheel`; both execution jobs must need `validate`.

Require these exact native entries:

```python
EXPECTED_ACCEPTANCE_MATRIX = [
    ("windows-latest", "windows", "x86_64", "hf-download-live-monitor-windows-x86_64.exe"),
    ("windows-11-arm", "windows", "arm64", "hf-download-live-monitor-windows-arm64.exe"),
    ("ubuntu-latest", "linux", "x86_64", "hf-download-live-monitor-linux-x86_64"),
    ("ubuntu-24.04-arm", "linux", "arm64", "hf-download-live-monitor-linux-arm64"),
    ("macos-15-intel", "macos", "x86_64", "hf-download-live-monitor-macos-x86_64"),
    ("macos-15", "macos", "arm64", "hf-download-live-monitor-macos-arm64"),
]
```

Require signature verification, public/non-prerelease release checks, an initially empty download directory, shared bundle validator, eight `gh attestation verify` operations, exact architecture assertions, absolute harness invocation, `PYTHONPATH` removal for wheel execution, site-packages assertion, and `if: always()` report uploads. Prohibit `gh release create/edit/upload`, PyPI actions, `python -m build`, PyInstaller, and editable installs.

- [ ] **Step 2: Run the workflow test and verify RED**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: FAIL because `release-acceptance.yml` is absent.

- [ ] **Step 3: Implement validation and native jobs**

The `validate` job checks out `inputs.tag` with full history, imports `SIGNING_KEY.asc`, runs `git verify-tag`, confirms stable syntax and public release state, downloads all assets to an empty `release-assets`, runs the shared validator, verifies attestations for the six executables/wheel/sdist, and uploads the complete bundle.

The `native` matrix downloads that workflow artifact, installs only `huggingface-hub>=0.27,<2` to obtain `hf`, restores Unix execute permission, verifies both checksum sources, asserts native architecture, and runs the harness from `${{ runner.temp }}`. Always upload `acceptance-${os}-${arch}.json`.

- [ ] **Step 4: Implement isolated wheel acceptance**

The wheel job creates its virtual environment under `${{ runner.temp }}`, installs the public wheel by absolute path, copies only the fixture harness to a separate temporary execution directory, clears `PYTHONPATH`, and invokes the environment's Python and console script by absolute path. Assert:

```python
module_path = Path(hf_download_live_monitor.__file__).resolve()
site_packages = Path(site.getsitepackages()[0]).resolve()
assert module_path.is_relative_to(site_packages)
assert importlib.metadata.version("hf-download-live-monitor") == TAG.removeprefix("v")
```

Run the same harness and always upload `acceptance-wheel.json`.

- [ ] **Step 5: Verify and commit Task 2**

Run:

```powershell
python -m pytest tests/test_release_assets.py tests/test_published_acceptance.py -q
python -m ruff format --check .
python -m ruff check .
python -m pyright
git diff --check
```

Commit:

```powershell
git add .github/workflows/release-acceptance.yml tests/test_release_assets.py
git commit -S -m "ci: verify published releases across native targets"
```

### Task 3: Document and lock the operator contract

**Files:**
- Modify: `docs/user-manual.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing documentation test**

Require the manual to name `Release Acceptance`, the `tag` input, all six architectures, public-asset-only execution, JSON stdout/stderr separation, wheel execution outside the checkout with `PYTHONPATH` removed, `site-packages` verification, report artifacts, immutable-release behavior, and the explicit exclusion of PyPI.

- [ ] **Step 2: Run the documentation test and verify RED**

Run: `python -m pytest tests/test_docs.py -q`

Expected: FAIL because the acceptance operator section is absent.

- [ ] **Step 3: Add concise operator documentation**

Document dispatch with:

```powershell
gh workflow run release-acceptance.yml -f tag=v0.1.0
gh run watch --exit-status
```

Explain success criteria, report retrieval, deterministic localhost endpoint, distinction between workflow failure and unavailable runner, and the rule that defects produce `v0.1.1` rather than replacement assets.

- [ ] **Step 4: Verify and commit Task 3**

Run: `python -m pytest tests/test_docs.py tests/test_release_assets.py -q`

Commit:

```powershell
git add docs/user-manual.md tests/test_docs.py
git commit -S -m "docs: explain published release acceptance"
```

### Task 4: Run local published-asset acceptance

**Files:**
- No repository changes expected; reports go under a unique temporary directory.

- [ ] **Step 1: Download and validate the public bundle**

Use a unique directory under `$env:TEMP`, run `gh release download v0.1.0`, execute `scripts/validate_release_bundle.py`, verify the signed tag, and run `gh attestation verify` for every executable, wheel, and sdist.

- [ ] **Step 2: Run Windows standalone and clean-wheel acceptance**

Run the harness against the published Windows x86_64 executable and against a new virtual environment containing only the public wheel and resolved dependencies. Execute outside the repository with `PYTHONPATH` removed. Inspect the interactive dashboard in a PTY after machine-readable checks pass.

- [ ] **Step 3: Run Docker acceptance**

Build a temporary consumer image that installs `huggingface-hub`, mounts the published Linux x86_64 executable and harness read-only, restores execute permission in a writable copy, and runs the harness four consecutive times. Do not copy or install the local application source.

- [ ] **Step 4: Preserve results and clean temporary payloads**

Record report hashes and outcomes in the handoff. Remove only the exact temporary directories created by this task after confirming paths resolve beneath the system temporary directory.

### Task 5: Verify, review, merge, and run GitHub acceptance

**Files:**
- No additional files unless review finds a defect.

- [ ] **Step 1: Run the complete local gate**

```powershell
python -m pytest -q
python -m ruff format --check .
python -m ruff check .
python -m pyright
git diff --check main...HEAD
git log --show-signature --format=fuller main..HEAD
```

- [ ] **Step 2: Inspect and push the exact delta**

Confirm only the design, plan, harness/tests, acceptance workflow, and operator documentation changed. Scan `main...HEAD` for tokens, authorization headers, absolute personal paths, environment dumps, and generated payloads. Push the branch and open a PR only after the scan is clean.

- [ ] **Step 3: Merge after all PR checks pass**

Merge the signed branch through GitHub, fast-forward canonical local `main`, preserve the user's pre-existing `.dockerignore` modification, and wait for post-merge CI to pass.

- [ ] **Step 4: Dispatch and monitor acceptance**

Dispatch `release-acceptance.yml` with `tag=v0.1.0`. Monitor until validation, all six native matrix entries, and wheel acceptance complete. On failure, collect exact job logs and reports, repair through a new signed branch, and rerun without modifying release assets.

- [ ] **Step 5: Final evidence report**

Report the workflow URL, commit SHA, public tag/signature status, 15-asset/checksum result, eight attestations, six native outcomes, wheel isolation evidence, Windows interactive result, Docker four-run result, and any unavailable external condition. State clearly that PyPI was not touched and `v0.1.0` remained immutable.

