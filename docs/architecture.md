# Architecture

The CLI normalizes explicit user input into an immutable `DownloadSpec`. `HubRepository` retrieves file-size metadata using public Hugging Face model, dataset, or Space APIs and applies the same requested-file filters as the download. `FileSystemObserver` safely correlates final and partial local files with that manifest.

`ProgressEngine` owns deterministic state, rate, and ETA calculations using monotonic observations. It has no network, terminal, or direct filesystem dependency. Renderers consume immutable snapshots and provide interactive Rich, plain, JSON, or JSON Lines output. `WatchApplication` coordinates these components through small protocols so tests can use deterministic fakes.

Private Hugging Face local-cache naming is confined to `compat.py`. Credential redaction and repository-path containment are centralized in `security.py`. Polling is the portable correctness baseline; every refresh indexes partial files once.

`processes.py` emits normalized process records from POSIX `/proc` or Windows CIM plus `psutil` working-directory resolution. `hf_command.py` parses those records without retaining credentials, and `attach.py` selects a download deterministically. `runner.py` launches the official CLI without a shell and supplies the exact child exit condition to the same watch application.
