# Architecture

The main data path is `DownloadPlan -> preflight -> FileSystemObserver ->
ProgressEngine/verifier -> ProgressSnapshot -> renderer`. The CLI normalizes user input
into an immutable `DownloadSpec`. `HubRepository.prepare` uses the standard Hugging
Face client, resolves the requested branch or tag to a full immutable commit, extracts
size and supported LFS SHA-256 metadata, and returns a frozen `DownloadPlan`.
Destination preflight checks path containment, writability, and conservative capacity
before managed child creation.

`FileSystemObserver` correlates final and partial files with the manifest.
`ProgressEngine` owns deterministic state, rate, ETA, and integrity transitions from
monotonic observations. A bounded verifier hashes stable size-matched candidates
outside the refresh path and caches results by file identity. Renderers consume
immutable snapshots and provide Adaptive Focus, plain text, schema-v2 JSON, or JSON
Lines. Pure layout policy selects narrow, normal, or wide composition.

Continuous `attach --all` adds a `DownloadSupervisor` above those per-repository
components. A `psutil` provider supplies `(PID, process start token)` identities so PID
reuse cannot merge sessions. Discovery reconciliation, a four-worker preparation and
finalization pool, one progress engine per session, bounded session count, idle backoff,
and timed retention keep resource use predictable. Immutable `SupervisorSnapshot`
values feed the adaptive aggregate renderer; versioned `supervisor_event` values feed
JSONL, while final JSON emits one last aggregate document.

Sessions move through `discovered`, `preparing`, `active`, and `finalizing` before a
terminal `completed`, `failed`, or `lost` state. Process disappearance triggers a forced
final observation. Integrity evidence determines completed or failed; insufficient
evidence produces lost. Operator cancellation closes supervisor-owned workers,
controls, and renderers but does not terminate attached downloaders.

## Lifecycle and fallbacks

`WatchApplication` prepares a plan, observes and renders repeatedly, and polls optional
controls. It performs a forced final observation, verification, and render when the
managed downloader stop condition fires, dashboard `q` requests cancellation, or it
handles KeyboardInterrupt. Managed Ctrl+C first invokes runner cleanup, which attempts
to stop and reap the child before final reconciliation. The child is confirmed stopped
and reaped when cleanup succeeds; `watch` and `attach` have no
managed child but perform the same final pass. Dashboard `q` performs its final pass
before the managed runner receives the cancellation result and cleans up the child. A
final integrity failure returns exit code `8` instead of cancellation exit code `9`
when managed cleanup succeeds. If cleanup fails, including on a second interrupt during
cleanup, child reaping is not confirmed. The application retains that error, still
performs final reconciliation, then raises a downloader-category error: cleanup failure
takes precedence over snapshot integrity and maps to exit code `6`. The no-child
`watch` and `attach` paths have no such cleanup-error precedence. Renderer, engine, and
controls are then closed. Controls mutate only `DisplayState`. Terminal setup or
key-reading failures disable
interaction without changing monitoring correctness; plain and structured renderers
do not initialize controls.

`processes.py` emits normalized process records through `psutil` on Windows, Linux, and
macOS, with native providers retained as fallbacks. `hf_command.py` accepts documented download
arguments without retaining credentials, and `attach.py` selects deterministically.
`runner.py` launches the official CLI without a shell using the resolved commit SHA and
owns terminate, kill, wait, and reap behavior on every exit path. Child exit triggers
the final pass; downloader success cannot override a final integrity failure.

## Trust and security boundaries

Hub metadata, process command lines, and filesystem contents are untrusted inputs.
Repository paths are contained under the destination, sizes and digests are validated,
errors are redacted, and credentials remain in the standard Hugging Face client flow.
Private local-cache naming is isolated in `compat.py`; uncertainty falls back to
conservative non-reuse. Polling is the portable correctness baseline, and every refresh
indexes partial files once.

Release automation labels artifacts by normalized operating system and architecture
and runs native Windows, Linux, and macOS jobs on x86-64 and ARM64. It uses a
build once, promote-without-rebuilding boundary. A GPG-signed tag is verified with an
independently stored public key pinned to fingerprint
`BF317715C9E7B15A750F481A5C53F25769B6CA89`; the key contained in a release tag is not a
trust source. Release commits must be reachable from protected `main`, and protected
`v*` tag rules restrict tag creation, update, and deletion. Builds receive GitHub artifact
attestations, and the tag workflow can stage only a draft release. The manual `Publish
GitHub Release` workflow validates
the same flat bundle in the protected `github-release` environment before making it
public. The independent `Publish PyPI` workflow may later promote the exact public
wheel and source archive with OIDC; PyPI promotion is optional and manual. Only
completed workflow results establish availability. Python wheels remain the portable
fallback. A deterministic incremental-downloader fixture exercises the real
subprocess, observer, verifier, renderer boundary, final pass, and cleanup locally and
in Docker.
