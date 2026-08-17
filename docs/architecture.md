# Architecture

The CLI normalizes explicit user input into an immutable `DownloadSpec`. `HubRepository.prepare` authenticates through the standard Hugging Face client, resolves the requested branch or tag to a full immutable commit, extracts size and supported LFS SHA-256 metadata, and returns a frozen `DownloadPlan`. Destination preflight verifies writability and conservative disk capacity before child creation. `FileSystemObserver` safely correlates final and partial local files with that manifest.

`ProgressEngine` owns deterministic state, rate, ETA, and integrity transitions using monotonic observations. A bounded verifier hashes stable size-matched candidates outside the refresh path and caches results by file identity. Renderers consume immutable snapshots and provide Adaptive Focus, plain text, schema-v2 JSON, or JSON Lines output. Pure layout policy selects narrow, normal, or wide composition from current terminal dimensions and view mode. `WatchApplication` coordinates these components through small protocols so tests can use deterministic fakes.

Private Hugging Face local-cache naming is confined to `compat.py`. Credential redaction and repository-path containment are centralized in `security.py`. Polling is the portable correctness baseline; every refresh indexes partial files once.

`processes.py` emits normalized process records from POSIX `/proc` or Windows CIM plus `psutil` working-directory resolution. `hf_command.py` parses those records without retaining credentials, and `attach.py` selects a download deterministically. `runner.py` launches the official CLI without a shell using the resolved commit SHA and owns terminate, kill, wait, and reap behavior on every exit path. A forced final observation after child exit determines the final integrity result.

Release automation labels artifacts by normalized operating system and architecture and validates Windows, Linux, and macOS on x86-64 and ARM64. Architecture-neutral wheels remain the fallback. A deterministic incremental-downloader fixture exercises the real subprocess, observer, verifier, renderer boundary, and cleanup behavior locally and in Docker.
