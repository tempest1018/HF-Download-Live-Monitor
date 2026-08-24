# Draft-First Release Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `v0.1.0` once, stage it as a private GitHub draft, publish it only through a separately approved manual workflow, and retain an independent build-free PyPI promotion path.

**Architecture:** A shared Python validator owns version, filename, package-metadata, and checksum rules. The tag workflow verifies the signed tag, builds and attests all artifacts, validates a flat release bundle, and stages only a draft. Two manual workflows reuse the same validator: one publishes the draft on GitHub, and the other optionally promotes the exact public wheel and source archive to PyPI.

**Tech Stack:** Python 3.10+, pytest, PyYAML, GitHub Actions, GitHub CLI, GPG, GitHub artifact attestations, PyPI Trusted Publishing.

---

## File map

- Create `scripts/validate_release_bundle.py`: pure release-tag parsing, package metadata inspection, exact asset inventory, checksum generation, and checksum validation.
- Create `tests/test_release_bundle.py`: focused unit tests for the shared validator and hostile/malformed bundles.
- Modify `.github/workflows/release.yml`: signed-tag verification, build attestations, flat bundle validation, and draft-only staging.
- Create `.github/workflows/publish-github-release.yml`: manual validation and public GitHub promotion without building.
- Create `.github/workflows/publish-pypi.yml`: manual validation and exact-archive PyPI promotion without building.
- Modify `tests/test_release_assets.py`: executable workflow contracts and separation-of-authority assertions.
- Modify `pyproject.toml`: set stable package version `0.1.0`.
- Modify `CHANGELOG.md`: date the `0.1.0` release entry.
- Modify `README.md`, `docs/user-manual.md`, and `docs/architecture.md`: document the draft-first operator and verification flow.
- Modify `tests/test_docs.py`: lock version and release documentation to runtime/project metadata.

### Task 1: Centralize release-bundle validation

**Files:**
- Create: `scripts/validate_release_bundle.py`
- Create: `tests/test_release_bundle.py`

- [ ] **Step 1: Write failing tag and metadata tests**

Create tests that import `ReleaseValidationError`, `parse_stable_tag`, `project_version`, `wheel_version`, and `sdist_version` and assert:

```python
def test_stable_tag_must_match_project_version(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nname = "hf-download-live-monitor"\nversion = "0.1.0"\n')
    assert parse_stable_tag("v0.1.0") == "0.1.0"
    assert project_version(project) == "0.1.0"
    with pytest.raises(ReleaseValidationError, match="stable tag"):
        parse_stable_tag("v0.1.0-rc.1")
```

Build tiny wheel ZIP and sdist tar fixtures containing canonical `METADATA`/`PKG-INFO`, then require both readers to return `0.1.0` and reject missing, duplicate, or conflicting metadata.

- [ ] **Step 2: Run the metadata tests and verify RED**

Run: `python -m pytest tests/test_release_bundle.py -q`

Expected: FAIL because `scripts.validate_release_bundle` does not exist.

- [ ] **Step 3: Implement strict tag and package metadata readers**

Implement dependency-free helpers with these public contracts:

```python
EXPECTED_STANDALONES = (
    "hf-download-live-monitor-windows-x86_64.exe",
    "hf-download-live-monitor-windows-arm64.exe",
    "hf-download-live-monitor-linux-x86_64",
    "hf-download-live-monitor-linux-arm64",
    "hf-download-live-monitor-macos-x86_64",
    "hf-download-live-monitor-macos-arm64",
)

class ReleaseValidationError(ValueError):
    pass

def parse_stable_tag(tag: str) -> str:
    match = re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag)
    if match is None:
        raise ReleaseValidationError(f"expected stable tag vMAJOR.MINOR.PATCH, got {tag!r}")
    return tag[1:]

```

Add three further public functions with exact signatures
`project_version(path: Path) -> str`, `wheel_version(path: Path) -> str`, and
`sdist_version(path: Path) -> str`.

Use anchored parsing for `[project]` and `version`, `zipfile` for the single wheel `.dist-info/METADATA`, and `tarfile` for the single top-level sdist `PKG-INFO`. Reject unsafe archive members, duplicate metadata, missing `Name`, a noncanonical project name, and ambiguous `Version` fields.

- [ ] **Step 4: Add failing inventory and checksum tests**

Create a complete flat fixture with the six standalone names, six adjacent `.sha256` files, one wheel, one sdist, and `SHA256SUMS`. Assert `validate_bundle` rejects every one of these mutations independently:

- missing or extra standalone;
- missing or malformed adjacent checksum;
- duplicate wheel or sdist;
- nested files;
- package/tag/project version mismatch;
- absolute or parent-traversing checksum names;
- duplicate, missing, extra, or mismatched `SHA256SUMS` entries.

Assert `write_aggregate_checksums` emits sorted basename-only entries and never hashes `SHA256SUMS` itself or adjacent `.sha256` files.

- [ ] **Step 5: Run inventory tests and verify RED**

Run: `python -m pytest tests/test_release_bundle.py -q`

Expected: metadata tests pass and inventory/checksum tests fail because bundle validation is absent.

- [ ] **Step 6: Implement bundle generation and validation**

Add:

```python
@dataclass(frozen=True, slots=True)
class ValidatedBundle:
    wheel: Path
    sdist: Path
    standalones: tuple[Path, ...]

```

Add `write_aggregate_checksums(directory: Path) -> Path` and
`validate_bundle(directory: Path, *, tag: str, project_file: Path) -> ValidatedBundle`
with the exact behaviors below.

Stream every hash in 1 MiB chunks. Require a flat directory and the exact payload set. Parse each adjacent standalone checksum as `<64 lowercase hex><two spaces><basename>`. Parse `SHA256SUMS` with the same grammar and require its names to equal all six standalone payloads plus the wheel and sdist. Compare tag, `pyproject.toml`, wheel, sdist, and distribution filename versions.

Provide a CLI:

```text
python scripts/validate_release_bundle.py release-assets --tag v0.1.0 \
  --project-file pyproject.toml [--write-aggregate]
```

Print only a concise success summary; send validation failures to stderr and exit nonzero.

- [ ] **Step 7: Verify validator GREEN**

Run: `python -m pytest tests/test_release_bundle.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the validator**

```powershell
git add scripts/validate_release_bundle.py tests/test_release_bundle.py
git commit -S -m "feat: validate immutable release bundles"
```

### Task 2: Make tag builds stage drafts only

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Replace the old publication test with failing draft-only contracts**

Assert the release workflow:

```python
triggers = _workflow_triggers(_workflow("release"))
assert triggers == {"push": {"tags": ["v*"]}}
jobs = _workflow("release")["jobs"]
assert "publish-pypi" not in jobs
assert "finalize-github-release" not in jobs
assert jobs["stage-github-release"]["needs"] == ["validate-release-assets"]
```

Also assert `fetch-depth: 0`, `git verify-tag`, import of `SIGNING_KEY.asc`, the shared validator command, flat `python-distributions` download, `actions/attest-build-provenance@v3` in both build jobs, least-privilege attestation permissions, draft creation, and an explicit failure when `isDraft` is `false`. Prohibit `--draft=false`, the PyPI action, and a successful early exit for a public release.

- [ ] **Step 2: Run workflow tests and verify RED**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: FAIL against the existing tag-to-PyPI workflow.

- [ ] **Step 3: Add signed-tag and version gates before build**

In `verify-and-build-python`, check out full history, import `SIGNING_KEY.asc` into an ephemeral `GNUPGHOME`, run `git verify-tag "$GITHUB_REF_NAME"`, and run the validator's tag/project-version check before installing or building. Ensure the temporary keyring is job-local and removed by runner teardown.

- [ ] **Step 4: Attest Python and standalone outputs**

Give only build jobs:

```yaml
permissions:
  contents: read
  id-token: write
  attestations: write
```

After each successful build/smoke test, invoke `actions/attest-build-provenance@v3` with the exact wheel/sdist glob or exact matrix executable and checksum paths. Keep release-write permission out of all build jobs.

- [ ] **Step 5: Flatten and validate the staged bundle**

Download both Python distributions and merged standalone artifacts directly into `release-assets/`. Run:

```yaml
- run: >-
    python scripts/validate_release_bundle.py release-assets
    --tag "$GITHUB_REF_NAME" --project-file pyproject.toml --write-aggregate
```

Upload only the validated flat directory. Do not construct checksums with duplicated shell logic.

- [ ] **Step 6: Make staging idempotent only for drafts**

Use `gh release view` to distinguish absent, draft, and public releases. Create an absent release with `--draft --verify-tag --generate-notes`; upload with `--clobber` only when absent or draft; exit nonzero when already public. Never make the draft public in this workflow.

- [ ] **Step 7: Verify the stage workflow GREEN**

Run: `python -m pytest tests/test_release_assets.py tests/test_release_bundle.py -q`

Expected: PASS.

- [ ] **Step 8: Commit draft-only staging**

```powershell
git add .github/workflows/release.yml tests/test_release_assets.py
git commit -S -m "ci: stage verified draft releases"
```

### Task 3: Add manual GitHub publication

**Files:**
- Create: `.github/workflows/publish-github-release.yml`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write failing manual-publication workflow tests**

Require `workflow_dispatch` with a required string `tag` input, no tag/push trigger, a `github-release` environment, `contents: write` only in the publication job, full-history checkout at the requested tag, GPG verification, release asset download, shared bundle validation, draft-state validation, and exactly one final `gh release edit "$TAG" --draft=false --prerelease=false --latest` mutation. Assert the workflow contains no build, PyInstaller, package-build, upload, `--clobber`, or PyPI step.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: FAIL because the workflow is missing.

- [ ] **Step 3: Implement validation-first manual publication**

Create a workflow named `Publish GitHub Release` with:

```yaml
on:
  workflow_dispatch:
    inputs:
      tag:
        description: Stable signed tag to publish
        required: true
        type: string
concurrency:
  group: publish-github-${{ inputs.tag }}
  cancel-in-progress: false
```

Check out `inputs.tag` with `fetch-depth: 0`, verify the tag against `SIGNING_KEY.asc`, query `isDraft`, `isPrerelease`, and `tagName`, download all assets into one directory, run the shared validator, and only then publish. Set `environment: github-release`. Fail rather than treating an already-public release as a successful publication.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_release_assets.py tests/test_release_bundle.py -q`

Expected: PASS.

```powershell
git add .github/workflows/publish-github-release.yml tests/test_release_assets.py
git commit -S -m "ci: gate public GitHub releases"
```

### Task 4: Add build-free optional PyPI promotion

**Files:**
- Create: `.github/workflows/publish-pypi.yml`
- Modify: `tests/test_release_assets.py`

- [ ] **Step 1: Write failing PyPI isolation tests**

Require a manual-only required `tag` input, `environment: pypi`, `id-token: write`, `contents: read`, public non-prerelease release checks, signed-tag verification, download and full validation of the public GitHub bundle, and `pypa/gh-action-pypi-publish@release/v1` pointed only at a directory containing the validated wheel and sdist. Prohibit `python -m build`, PyInstaller, release edits/uploads, `contents: write`, `skip-existing`, and tag/push triggers.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest tests/test_release_assets.py -q`

Expected: FAIL because the PyPI workflow is missing.

- [ ] **Step 3: Implement exact-archive promotion**

Create `Publish PyPI` as a `workflow_dispatch` workflow. Check out the signed tag only for the version validator and public key, download every public release asset, require `isDraft == false` and `isPrerelease == false`, validate the entire bundle, and copy only the returned wheel and sdist to `python-distributions/`. Invoke Trusted Publishing without `skip-existing`, so a duplicate-version error remains visible rather than hiding an unintended rerun.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_release_assets.py tests/test_release_bundle.py -q`

Expected: PASS.

```powershell
git add .github/workflows/publish-pypi.yml tests/test_release_assets.py
git commit -S -m "ci: isolate optional PyPI promotion"
```

### Task 5: Prepare `0.1.0` metadata and operator documentation

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `docs/user-manual.md`
- Modify: `docs/architecture.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write failing metadata and documentation contracts**

Add tests asserting the project version is exactly `0.1.0`, the changelog contains `## 0.1.0 - 2026-08-24` and not `## 0.1.0 - Unreleased`, and the manual/architecture contain the exact concepts `draft release`, `Publish GitHub Release`, `Publish PyPI`, `github-release`, `build once`, `GPG-signed tag`, and `artifact attestation`. Assert the docs explicitly say tag creation does not publish publicly and PyPI is optional/manual.

- [ ] **Step 2: Run docs tests and verify RED**

Run: `python -m pytest tests/test_docs.py -q`

Expected: FAIL on development version and missing operator flow.

- [ ] **Step 3: Finalize release metadata**

Set `version = "0.1.0"` in `pyproject.toml`. Change the changelog heading to `## 0.1.0 - 2026-08-24`. Retain the Alpha classifier because `0.1.0` is the first stabilization milestone, not a claim of production maturity.

- [ ] **Step 4: Document the three-stage operator flow**

Document these commands and their authorization boundaries:

```powershell
git tag -s v0.1.0 -m "HF Download Live Monitor v0.1.0"
git push origin v0.1.0
gh workflow run "Publish GitHub Release" -f tag=v0.1.0
gh workflow run "Publish PyPI" -f tag=v0.1.0
gh attestation verify <downloaded-file> --repo tempest1018/HF-Download-Live-Monitor
```

State that the tag push stages a private draft, the GitHub workflow needs separate approval, and the PyPI command is future/optional and must not be run during this GitHub-only milestone. Keep end-user checksum instructions consistent with a flat, basename-only `SHA256SUMS` file.

- [ ] **Step 5: Verify docs and commit**

Run: `python -m pytest tests/test_docs.py tests/test_release_assets.py tests/test_release_bundle.py -q`

Expected: PASS.

```powershell
git add pyproject.toml CHANGELOG.md README.md docs/user-manual.md docs/architecture.md tests/test_docs.py
git commit -S -m "docs: prepare version 0.1.0 release"
```

### Task 6: Release-candidate verification and approval handoff

**Files:**
- Modify only files required to repair a discovered failure.

- [ ] **Step 1: Run static quality gates**

```powershell
python -m ruff format --check .
python -m ruff check .
python -m pyright
git diff --check main..HEAD
```

Expected: all commands exit 0 with no warnings or whitespace errors.

- [ ] **Step 2: Run the complete suite four consecutive times**

```powershell
1..4 | ForEach-Object {
  python -m pytest -q
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: four identical passing test counts.

- [ ] **Step 3: Build and validate Python distributions**

Remove only the worktree's generated `dist/` and `build/` directories after resolving and confirming they are inside this worktree, then run:

```powershell
python -m build
python -m twine check dist/*
```

Expected: one `0.1.0` wheel and one `0.1.0` source archive pass Twine.

- [ ] **Step 4: Build and smoke the local standalone**

Run:

```powershell
python scripts/build_standalone.py
dist\hf-download-live-monitor-windows-x86_64.exe --help
dist\hf-download-live-monitor-windows-x86_64.exe watch --help
dist\hf-download-live-monitor-windows-x86_64.exe attach --help
dist\hf-download-live-monitor-windows-x86_64.exe run --help
```

Expected: the executable and adjacent checksum exist and all commands exit 0.

- [ ] **Step 5: Run Docker simulation**

Run:

```powershell
docker build -f Dockerfile.test -t hf-download-live-monitor:release-candidate .
docker run --rm hf-download-live-monitor:release-candidate
```

Expected: all four simulated downloads verify and the container exits 0.

- [ ] **Step 6: Inspect signatures and confidential content**

Run:

```powershell
git log --show-signature --format=fuller main..HEAD
git status --short --branch
git diff --name-status main..HEAD
git diff main..HEAD | rg -n -i "(hf_[a-z0-9]{20,}|bearer\s+[a-z0-9._-]+|password|passphrase|private key|C:\\Users\\Tempest)"
```

Expected: every commit has a good signature, the tree is clean, only planned files changed, and the scan contains no credential or personal-path disclosure. The checked-in public key is expected and is not a private-key finding.

- [ ] **Step 7: Commit verification repairs only if needed**

If verification required a source repair, stage only its exact files and create a signed `fix:` commit. Do not create an empty verification commit.

- [ ] **Step 8: Stop before external mutation**

Report the exact branch, commit range, files, test counts, artifacts, checksum results, signature state, privacy scan, and workflow/environment prerequisites. Do not push the branch, create a pull request, create or push `v0.1.0`, dispatch a workflow, publish a release, or configure repository environments without explicit user approval.
