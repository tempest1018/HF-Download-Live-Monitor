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

## Lifecycle and fallbacks

`WatchApplication` prepares a plan, observes and renders repeatedly, and polls optional
controls. It performs a forced final observation, verification, and render when the
managed downloader stop condition fires, dashboard `q` requests cancellation, or it
handles KeyboardInterrupt. Managed Ctrl+C first invokes runner cleanup so the child is
stopped and reaped before final reconciliation; `watch` and `attach` have no managed
child but perform the same final pass. Dashboard `q` performs its final pass before the
managed runner receives the cancellation result and cleans up the child. A final
integrity failure returns exit code `8` instead of cancellation exit code `9`.
Renderer, engine, and controls are then closed.
Controls mutate only `DisplayState`. Terminal setup or key-reading failures disable
interaction without changing monitoring correctness; plain and structured renderers
do not initialize controls.

`processes.py` emits normalized process records from POSIX `/proc` or Windows CIM plus
`psutil` working-directory resolution. `hf_command.py` accepts documented download
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

Release automation is configured to label artifacts by normalized operating system and
architecture and to run native Windows, Linux, and macOS jobs on x86-64 and ARM64.
Only completed workflow results establish availability. Python wheels remain the
portable fallback. A deterministic incremental-downloader fixture exercises the real
subprocess, observer, verifier, renderer boundary, final pass, and cleanup locally and
in Docker.
