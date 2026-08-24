# Draft-First Release Promotion Design

## Status

Approved in conversation on 2026-08-24. This document defines the release architecture;
it does not authorize a push, tag, public release, or PyPI publication.

## Goal

Publish HF Download Live Monitor `v0.1.0` to GitHub through a reviewable draft-first
pipeline, then retain an optional, separately approved path to promote the exact Python
archives from that release to PyPI without rebuilding them.

## Release principles

- A tag starts verification and staging, never public publication.
- Public GitHub publication requires a separate manual workflow dispatch.
- PyPI publication is independent, optional, and protected by its own manual approval.
- The wheel and source archive are built once. Any later PyPI promotion uses the exact
  bytes already attached to the GitHub release.
- Published release assets are immutable. A defect after publication is corrected in a
  new patch release rather than by replacing `v0.1.0` files.
- Private GPG key material never enters GitHub Actions or repository secrets.

## Version and tag contract

The first release uses package version `0.1.0` and an annotated, GPG-signed tag named
`v0.1.0`. The release workflow must reject a tag unless all of these values agree:

- the tag has the exact `v<PEP 440 version>` form accepted by the project;
- `pyproject.toml` contains the corresponding package version;
- the built wheel and source archive report that same version;
- the changelog contains a dated heading for that version and no longer labels it
  `Unreleased`.

Commits and tags are signed locally with the documented project key. GitHub Actions
verifies the checked-in public key and the tag signature before building. GitHub's
keyless artifact attestations authenticate CI-built files; they complement rather than
replace the GPG-signed source tag.

## Workflow boundaries

### 1. Stage release

The tag-triggered staging workflow performs the complete quality matrix, builds the
wheel, source archive, and six OS/architecture-labelled standalone executables, and
smoke-tests every executable on its native runner. Each build job creates a GitHub
artifact attestation for the files it produced.

After all builds pass, one validation job downloads the complete bundle and enforces:

- exactly one wheel and one source archive for `0.1.0`;
- exactly six expected standalone executables;
- an adjacent checksum for every standalone executable;
- a valid aggregate `SHA256SUMS` file using asset basenames that still verify after a
  normal `gh release download` flattens the release files into one directory;
- no unexpected executable or distribution files;
- package metadata and tag/version agreement.

The staging job creates or updates a **draft** GitHub release for the tag and uploads the
validated bundle. Reruns may replace assets while the release is a draft. The workflow
must fail if the release is already public, preventing silent mutation of published
assets. Staging completion leaves the release private to repository collaborators.

### 2. Publish GitHub release

A separate `workflow_dispatch` workflow accepts the exact tag as input. It downloads the
draft release assets, repeats the completeness and checksum checks, confirms the tag and
package versions, confirms the release is still a draft, and then makes it public.

The workflow performs no build. Its only state-changing final step is changing the
validated draft to a public, non-prerelease GitHub release. That step runs in a protected
`github-release` environment so repository settings can require human approval. A
failed validation leaves the draft unchanged.

### 3. Optional PyPI promotion

A separate manually dispatched workflow accepts the stable tag. It requires the GitHub
release to be public and non-prerelease, downloads only the wheel and source archive plus
`SHA256SUMS`, verifies their hashes and embedded versions, and publishes those unchanged
archives through PyPI Trusted Publishing.

This job uses the protected `pypi` environment and short-lived OIDC credentials. It does
not check out source code for a rebuild and does not accept a local file upload. PyPI
promotion remains outside the GitHub-only `v0.1.0` milestone and requires later explicit
user authorization.

## Artifact identity and provenance

Release filenames remain deterministic:

- `hf-download-live-monitor-windows-x86_64.exe`
- `hf-download-live-monitor-windows-arm64.exe`
- `hf-download-live-monitor-linux-x86_64`
- `hf-download-live-monitor-linux-arm64`
- `hf-download-live-monitor-macos-x86_64`
- `hf-download-live-monitor-macos-arm64`
- one `hf_download_live_monitor-0.1.0-*.whl`
- one `hf_download_live_monitor-0.1.0.tar.gz`
- one adjacent `.sha256` file per standalone executable
- `SHA256SUMS` covering every published payload except itself, with one basename-only
  entry per downloaded release asset

GitHub artifact attestations are issued with OIDC and `attestations: write` permissions.
The workflows receive only the least privileges needed by each job. In particular,
build jobs cannot publish releases, the GitHub publication job cannot publish to PyPI,
and the PyPI job cannot modify GitHub release assets.

## Failure and rerun behavior

- A quality, build, smoke-test, provenance, or checksum failure creates no public
  release and performs no PyPI action.
- Staging can be rerun safely while the release is a draft.
- GitHub publication refuses missing, extra, renamed, or hash-mismatched payloads.
- Publication refuses an absent tag, a version mismatch, a non-draft release, or an
  unexpected prerelease version.
- PyPI promotion refuses a draft or prerelease GitHub release and refuses artifacts
  whose bytes or embedded versions differ from the public release manifest.
- Once public, `v0.1.0` assets are never overwritten. Corrections use `v0.1.1` or a
  later semantic version.

## Documentation and operator experience

The manual documents the three distinct operations: signed tag and draft staging,
manual GitHub publication, and optional PyPI promotion. It gives checksum and GitHub
attestation verification commands without implying that GPG signs CI-built binaries.
The changelog records `0.1.0` with its publication date only in the release-preparation
commit.

Before any tag is created, the operator reports the release commit, exact changed files,
test and build results, expected asset list, signature status, and confidential-file
scan. Creating or pushing the tag requires explicit user approval.

## Testing and acceptance

Automated workflow-structure tests must prove:

- tag events can stage only a draft release;
- no tag-triggered job can publish publicly or invoke PyPI;
- public GitHub publication is manual, validation-gated, and environment-protected;
- PyPI promotion is manual, environment-protected, OIDC-only, and build-free;
- all three stages enforce tag, metadata, filename, and checksum consistency;
- artifact attestation permissions and steps cover Python and standalone outputs;
- published releases cannot be modified by a staging rerun.

Release readiness also requires Ruff formatting and lint, Pyright, the complete test
suite, Python distribution build and Twine validation, standalone build smoke testing,
Docker simulation, a clean exact-delta inspection, and verified GPG signatures. The
GitHub-only milestone is complete only after the draft is reviewed, publication is
explicitly authorized, the public workflow succeeds, and the downloaded public assets
pass checksum and smoke verification.

## Out of scope

- Automatically publishing to PyPI from a tag.
- Storing a private GPG key or passphrase in GitHub.
- Replacing public release assets in place.
- Code-signing certificates for Windows Authenticode or Apple notarization.
- Publishing containers or installer packages in `v0.1.0`.
