# HF Live Monitor User Manual

This manual takes you from a clean computer to a verified installation and a successfully monitored Hugging Face download. It also covers daily use, automation, updates, removal, privacy, and failure recovery.

## Contents

- [Five-minute setup](#five-minute-setup)
- [Choose the right mode](#choose-the-right-mode)
- [Prerequisites](#prerequisites)
- [Hugging Face authentication](#hugging-face-authentication)
- [Installation](#installation)
- [Verify the installation](#verify-the-installation)
- [Quick start](#quick-start)
- [Watch mode](#watch-mode)
- [Attach mode](#attach-mode)
- [Run mode](#run-mode)
- [Repository types and file selection](#repository-types-and-file-selection)
- [Output formats](#output-formats)
- [Exit status](#exit-status)
- [Platform guidance](#platform-guidance)
- [Update, downgrade, and uninstall](#update-downgrade-and-uninstall)
- [Troubleshooting](#troubleshooting)
- [Privacy and security](#privacy-and-security)
- [Support and diagnostics](#support-and-diagnostics)
- [Readiness checklist](#readiness-checklist)

## Five-minute setup

Use this path after `hf-live-monitor` is published to PyPI:

```console
python --version
pipx install hf-live-monitor
hf-live-monitor --help
hf --help
hf-live-monitor run hf-internal-testing/tiny-random-bert --local-dir ./tiny-bert
```

If the repository is private or gated, run `hf auth login` before the final command. Success means the monitor exits without a traceback and `./tiny-bert` contains the downloaded repository files.

Before the first public release, install from this trusted checkout instead:

```console
python -m pip install -e ".[dev]"
python -m pytest -q
hf-live-monitor --help
```

Continue through the full manual when using a standalone executable, WSL, private repositories, automation output, or an existing download.

## Choose the right mode

HF Live Monitor has three commands:

| Mode | Use it when | Key requirement |
| --- | --- | --- |
| `run` | You want the simplest, most reliable experience. | HF Live Monitor starts the official download. |
| `watch` | You know the repository and destination directory. | Pass the same repository, revision, type, and filters as the download. |
| `attach` | An `hf download` command is already running. | Process discovery must be supported and the command must use `--local-dir`. |

For a first use, choose `run`. It launches the official Hugging Face downloader without a shell and monitors the exact child process. Standalone-executable users must also have the official `hf` command available.

## Prerequisites

### Package installation

For `pipx`, `uv`, or `pip` installation, you need:

- Windows 10 or newer, WSL, a current Linux distribution, or macOS.
- Python 3.10 or newer.
- Internet access to install packages and query Hugging Face metadata.
- Enough disk space for the repository you intend to download.

Check Python:

```console
python --version
```

On Windows, `py --version` may work when `python --version` does not.

### Standalone executable

The standalone executable does not require a separate Python installation for `watch` or `attach`. `run` still launches the official external `hf` command, so install the Hugging Face CLI separately or pass its path with `--hf-executable`. Download standalone HF Live Monitor files only from this project's GitHub Releases page and verify their checksums before running them.

### Hugging Face access

Public repositories need no account. Private or gated repositories require a Hugging Face account, access to the repository, and local authentication.

## Hugging Face authentication

The recommended authentication method is the official CLI:

```console
hf auth login
```

Paste your Hugging Face access token only into the secure prompt. Do not place it in command history, screenshots, issue reports, or JSON output.

Verify the active account:

```console
hf auth whoami
```

To remove locally stored Hugging Face authentication:

```console
hf auth logout
```

HF Live Monitor delegates authentication to `huggingface_hub`. It does not ask for, save, or print tokens. The `HF_TOKEN` environment variable is supported by Hugging Face, but persistent shell environment variables can be exposed by diagnostics or child processes; prefer `hf auth login` on a personal workstation.

## Installation

Choose one method. Do not install the application through several methods simultaneously unless you understand command-path precedence.

> **Availability:** `pipx install`, `uv tool install`, and ordinary PyPI installation work only after a release has been published. Until then, use the development installation from a trusted checkout. Standalone files are available only after a GitHub release workflow has produced them.

### Option A: pipx (recommended Python installation)

Install pipx using its platform instructions, then run:

```console
pipx install hf-live-monitor
```

If the command is not immediately available, open a new terminal after running:

```console
pipx ensurepath
```

### Option B: uv tool

```console
uv tool install hf-live-monitor
```

Confirm that the uv tool binary directory is on your `PATH` if the command is not found.

### Option C: pip in a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install hf-live-monitor
```

WSL, Linux, and macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install hf-live-monitor
```

The command remains available only while that environment is active unless you invoke its executable directly.

### Option D: standalone executable

1. Open the project's GitHub Releases page.
2. Download the executable for your operating system and `SHA256SUMS` or the adjacent `.sha256` file.
3. Verify the download.

Windows PowerShell:

```powershell
Get-FileHash .\hf-live-monitor.exe -Algorithm SHA256
Get-Content .\hf-live-monitor.exe.sha256
```

The two hexadecimal digests must match exactly. Then run:

```powershell
.\hf-live-monitor.exe --help
```

Linux or macOS:

```bash
sha256sum -c hf-live-monitor.sha256
chmod +x hf-live-monitor
./hf-live-monitor --help
```

On macOS, use `shasum -a 256 hf-live-monitor` if `sha256sum` is unavailable. Do not bypass operating-system security warnings for an artifact whose checksum or source you cannot verify.

### Development installation

From a trusted source checkout:

```console
python -m pip install -e ".[dev]"
python -m pytest -q
```

Development installation is for contributors, not ordinary users.

## Verify the installation

Run:

```console
hf-live-monitor --help
hf-live-monitor watch --help
hf-live-monitor attach --help
hf-live-monitor run --help
```

For a standalone file, replace `hf-live-monitor` with `./hf-live-monitor` or `.\hf-live-monitor.exe`.

Expected result: all four commands print help and exit without a traceback. Also verify the official Hugging Face CLI when you intend to use `run`:

```console
hf --help
```

If your shell finds a different installation than expected:

```powershell
Get-Command hf-live-monitor -All
```

```bash
command -v hf-live-monitor
```

## Quick start

Choose a small public model repository and a new destination directory. The following example uses a commonly available test-sized repository; if it becomes unavailable, substitute another small public model:

```console
hf-live-monitor run hf-internal-testing/tiny-random-bert --local-dir ./tiny-bert
```

Expected behavior:

1. The official downloader starts.
2. A live table shows each requested file, downloaded bytes, percentage, speed, and state.
3. Completed files appear in `./tiny-bert`.
4. The command exits with the downloader's exit code.

To produce one machine-readable observation instead:

```console
hf-live-monitor watch hf-internal-testing/tiny-random-bert --local-dir ./tiny-bert --json --once
```

## Watch mode

Use `watch` when a download is already writing to a known local directory or when you want a one-time status snapshot.

Start a download in terminal one:

```console
hf download owner/repository --local-dir ./download
```

Monitor it in terminal two:

```console
hf-live-monitor watch owner/repository --local-dir ./download
```

The repository, repository type, revision, selected filenames, include patterns, and exclude patterns must match the download. Otherwise totals can include files the downloader did not request.

Useful options:

```console
hf-live-monitor watch owner/repository --local-dir ./download --once
hf-live-monitor watch owner/repository --local-dir ./download --refresh 0.5
hf-live-monitor watch owner/repository --local-dir ./download --rate-window 3
hf-live-monitor watch owner/repository --local-dir ./download --plain
hf-live-monitor watch owner/repository --local-dir ./download --ascii --no-color
```

- `--refresh` controls seconds between observations; minimum `0.01`, default `0.25`.
- `--rate-window` controls rate smoothing in seconds; minimum `0.1`, default `2.0`.
- `--once` renders one observation and exits.
- `--ascii` affects plain output; `--no-color` affects the interactive renderer.

Press Ctrl+C to stop monitoring. In `watch` mode this stops only the monitor, not a separately started download.

## Attach mode

`attach` discovers supported `hf download` processes that include `--local-dir`.

```console
hf-live-monitor attach
```

If one download is found, monitoring starts. If several are found in an interactive terminal, select one by number. For automation or explicit selection:

```console
hf-live-monitor attach --pid 1234
```

To take one snapshot of every discovered download:

```console
hf-live-monitor attach --all --once --jsonl
```

`--pid` and `--all` cannot be used together. Continuous multi-download `--all` output is deliberately rejected; use `--all --once`.

Attachment support:

- Native Windows: process command lines are discovered through Windows management APIs; working directories use `psutil`.
- WSL and Linux: processes are discovered through `/proc`.
- macOS: automatic attachment is not supported because `/proc` is unavailable; use `watch` or `run`.
- Restricted containers or accounts may not permit inspection of other processes; use `watch` or `run`.

Relative `--local-dir` values are resolved against the downloader process's working directory. Token options are discarded by the parser and are not retained in the normalized download specification.

## Run mode

`run` is the recommended general-purpose mode:

```console
hf-live-monitor run owner/repository --local-dir ./download
```

It constructs an argument list and launches the official `hf download` executable directly. It does not evaluate a shell command.

Examples:

```console
hf-live-monitor run owner/repository --local-dir ./download --revision v2
hf-live-monitor run owner/dataset --repo-type dataset --local-dir ./dataset
hf-live-monitor run owner/space --repo-type space --local-dir ./space
hf-live-monitor run owner/repository --local-dir ./download --filename config.json
```

If `hf` is installed under a different executable name or path:

```console
hf-live-monitor run owner/repository --local-dir ./download --hf-executable /path/to/hf
```

On the first Ctrl+C, HF Live Monitor asks the child downloader to terminate and waits up to five seconds. If shutdown times out or the wait is interrupted again, it kills the child. The final process exit code is propagated to the calling shell.

## Repository types and file selection

The default repository type is `model`:

```console
--repo-type model
--repo-type dataset
--repo-type space
```

Select a revision:

```console
--revision main
--revision v1.2.0
--revision 0123456789abcdef
```

Select explicit files by repeating `--filename`:

```console
--filename config.json --filename tokenizer.json
```

Use repeatable include and exclude glob patterns:

```console
--include "*.json" --include "weights/*" --exclude "*.bin"
```

Selection order is:

1. Explicit filenames form the starting set when supplied.
2. Include patterns constrain that set.
3. Exclude patterns always win.

Quote wildcard patterns so the shell does not expand them before HF Live Monitor receives them.

## Output formats

Only one of `--plain`, `--json`, or `--jsonl` may be selected.

### Interactive display

The default on a terminal is a live Rich table. It shows file progress, state, speed, and aggregate totals.

### Plain text

```console
hf-live-monitor watch owner/repository --local-dir ./download --plain
```

Plain text contains no cursor-control sequences and is selected automatically when standard output is redirected. Add `--ascii` for ASCII-only separators.

### JSON

```console
hf-live-monitor watch owner/repository --local-dir ./download --json --once > status.json
```

`--json` writes one JSON document per render. It is best paired with `--once`.

### JSON Lines

```console
hf-live-monitor watch owner/repository --local-dir ./download --jsonl > progress.jsonl
```

`--jsonl` writes one independent JSON object per line and is suitable for streaming automation. The current structured schema version is `1`; see [Structured output schema](json-schema.md).

The managed downloader started by `run` inherits the terminal's standard streams. Depending on the installed Hugging Face CLI version, its own messages can appear alongside monitor output. For a guaranteed monitor-only JSON file, start the download separately and use `watch --json --once`, `watch --jsonl`, or an attached process whose output remains in its original terminal.

Common file states:

- `queued`: requested but no bytes are visible.
- `measuring`: a partial file exists but the rate window is not mature.
- `downloading`: bytes are increasing.
- `waiting`: partial bytes are not currently increasing.
- `finalizing`: expected bytes exist in a partial file pending finalization.
- `complete`: final size exactly matches repository metadata.
- `inconsistent`: final size does not match repository metadata.

## Exit status

- `0` means the monitor completed normally, produced a requested one-time observation, or was stopped cleanly in `watch` or `attach` mode.
- `2` is used for invalid CLI usage and classified monitor errors such as a missing repository or unsupported selection.
- `run` propagates the official downloader's nonzero exit status after monitoring it.
- Operating-system launch failures and forced termination can produce platform-specific nonzero values.

Automation should treat any nonzero status as failure and preserve sanitized standard error for diagnosis.

## Platform guidance

### Native Windows

- PowerShell examples use `.\` for executables and virtual-environment activation.
- Windows Terminal is recommended for the interactive table.
- If script activation is restricted, use `.\.venv\Scripts\python.exe -m hf_live_monitor --help` rather than weakening system-wide execution policy.
- Very long repository paths are handled by supported Hugging Face versions, but a short destination such as `C:\Models\name` reduces compatibility risk.

### WSL

- Install the application inside WSL if the downloader runs inside WSL.
- Use Linux paths such as `/mnt/c/Models/name` consistently.
- A native Windows monitor cannot attach to a WSL process, and a WSL monitor cannot attach to a native Windows process.

### Linux

- Attach visibility depends on `/proc` permissions and container isolation.
- Prefer `pipx` or `uv tool` instead of modifying a distribution-managed Python installation.

### macOS

- `watch` and `run` are supported.
- `attach` is not supported because this implementation relies on Linux `/proc` for POSIX attachment.
- Verify downloaded standalone artifacts before handling any Gatekeeper prompt.

## Update, downgrade, and uninstall

### Update

pipx:

```console
pipx upgrade hf-live-monitor
```

uv:

```console
uv tool upgrade hf-live-monitor
```

pip:

```console
python -m pip install --upgrade hf-live-monitor
```

Standalone: download the newer release, verify its checksum, smoke-test `--help`, and then replace the older executable. Retain the prior verified binary until the new one works.

After every update:

```console
hf-live-monitor --help
hf-live-monitor run --help
```

### Downgrade

Replace `X.Y.Z` with an available published version:

```console
pipx install --force hf-live-monitor==X.Y.Z
uv tool install --force hf-live-monitor==X.Y.Z
python -m pip install --force-reinstall hf-live-monitor==X.Y.Z
```

For a standalone installation, restore a previously verified release artifact. Review the changelog before downgrading because structured output or supported Hugging Face versions may differ.

### Uninstall

pipx:

```console
pipx uninstall hf-live-monitor
```

uv:

```console
uv tool uninstall hf-live-monitor
```

pip:

```console
python -m pip uninstall hf-live-monitor
```

Standalone: delete only the executable and its checksum file from the directory where you placed them.

Uninstalling HF Live Monitor does not delete downloaded repositories, Hugging Face credentials, or the Hugging Face cache. Remove those separately only when you understand what data will be lost.

## Troubleshooting

### `hf-live-monitor` is not recognized

- Open a new terminal after installing with pipx or uv.
- Run `pipx ensurepath` when using pipx.
- Confirm the correct virtual environment is active.
- Locate competing installations with `Get-Command hf-live-monitor -All` or `command -v hf-live-monitor`.

### `hf` is not recognized in run mode

Confirm `hf --help` works. Reinstall or update `huggingface_hub`, or pass the trusted executable path with `--hf-executable`.

### `no_download_found`

- Confirm the active command is `hf download` and includes `--local-dir`.
- Run the monitor in the same environment boundary as the downloader: Windows with Windows, WSL with WSL.
- Process inspection may be restricted; use `watch` with explicit repository information.

### `multiple_downloads`

Run interactively and choose a process, or use:

```console
hf-live-monitor attach --pid PROCESS_ID
```

### `pid_not_found`

The process may have exited, the PID may be wrong, or the command may not be a supported local-directory download. Discover again with `hf-live-monitor attach`.

### `repository_not_found`

Check spelling, `--repo-type`, `--revision`, network access, and authentication. Hugging Face may intentionally present a private repository as not found to unauthenticated users.

### `authentication_required`

Run `hf auth login`, verify with `hf auth whoami`, and confirm that your account has accepted any gated repository conditions.

### `rate_limited` or `hub_error`

Wait and retry. The application uses bounded exponential retry for recoverable metadata errors. Persistent failures can indicate network filtering, proxy configuration, service availability, or an incompatible dependency version.

### `metadata_unavailable`

The Hub did not provide usable file-size metadata. Confirm the repository type and revision, then update within the supported dependency range:

```console
python -m pip install --upgrade "huggingface-hub<2"
```

### Progress stays at `waiting`

The downloader may be retrying, rate-limited, paused, or finalizing another file. Check the downloader terminal. A stable partial file is not treated as failed without definitive evidence.

### A final file is `inconsistent`

Its local size differs from repository metadata. Stop relying on the file until the official downloader has finished. If the state remains, verify the revision and filters, check disk errors, and retry into a clean destination.

### Totals are wrong

Pass the same repository type, revision, explicit filenames, include patterns, and exclude patterns used by the download. `watch` cannot infer filters from an unrelated process; use `attach` or `run` when possible.

### JSON is mixed with other output

Use only one structured mode, redirect standard output, and keep downloader output separate. For a single atomic record use `--json --once`; for a stream use `--jsonl`.

### Standalone executable is unexpectedly large

Official release binaries are built in clean environments. Do not distribute a binary built from a development environment containing unrelated optional ML frameworks. Rebuild in a clean virtual environment using the documented release script.

## Privacy and security

- No telemetry is collected.
- HF Live Monitor does not store credentials.
- Authentication is handled by the official Hugging Face library.
- Token-bearing command options are discarded during attach parsing.
- Structured output contains repository IDs and local destination paths, which may still be sensitive.
- Windows attachment reads local process command lines and resolves process working directories.
- Linux and WSL attachment read permitted `/proc` entries.
- `run` starts the official CLI directly without shell evaluation.
- Repository paths are normalized and checked to remain inside the selected destination.

Before sharing output, remove private repository names, usernames in local paths, PIDs, filesystem layout, and any other environment details you consider sensitive. Never post tokens, complete authentication errors containing secrets, or private downloaded data.

See the [Security policy](../SECURITY.md) for vulnerability reporting guidance.

## Support and diagnostics

When requesting help, provide the smallest safe set of facts:

```console
hf-live-monitor --help
python --version
hf version
```

Also state:

- Operating system and whether the process runs in Windows, WSL, a container, or macOS.
- Installation method and HF Live Monitor version.
- Command mode (`watch`, `attach`, or `run`) with tokens and private names removed.
- Repository type and whether it is public, private, or gated.
- Exact error code and sanitized message.

Do not attach environment dumps, complete process lists, authentication files, `.env` files, Hugging Face tokens, VPN configurations, private repository contents, or unrestricted directory listings.

## Readiness checklist

- [ ] `python --version` reports 3.10 or newer, or a verified standalone executable is available.
- [ ] `hf-live-monitor --help` works.
- [ ] `hf --help` works when using `run`.
- [ ] `hf auth whoami` succeeds when private or gated access is required.
- [ ] The destination has enough disk space.
- [ ] Repository type, revision, and filters are known.
- [ ] The selected mode matches the platform and workflow.
- [ ] Structured output is redirected when used for automation.
- [ ] No secret appears in a command, screenshot, or support report.

You are ready when the help commands pass and the quick-start download completes with the expected files in the selected local directory.
