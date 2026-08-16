# HF Download Live Monitor

HF Download Live Monitor is a privacy-conscious, cross-platform terminal monitor for Hugging Face downloads. It reports accurate per-file and aggregate progress without replacing the official download client.

> **Breaking rename:** Existing preview users must uninstall `hf-live-monitor` and install
> `hf-download-live-monitor`. The former command and Python import package are not retained.

New here? Follow the [complete user manual](docs/user-manual.md) for prerequisites, installation, authentication, first use, maintenance, and troubleshooting.

Release commits, tags, and artifacts are authenticated with the public key documented in the
[security policy](SECURITY.md#signature-verification).

## Install

Python 3.10 or newer is required.

```console
pipx install hf-download-live-monitor
# or
uv tool install hf-download-live-monitor
```

For development, run `python -m pip install -e ".[dev]"`.

## Watch a download

Start the official download in one terminal:

```console
hf download owner/repository --local-dir ./download
```

Watch the same destination in another:

```console
hf-download-live-monitor watch owner/repository --local-dir ./download
```

Datasets and Spaces are supported with `--repo-type dataset` and `--repo-type space`. Use `--revision`, repeated `--filename`, `--include`, and `--exclude` options to match filtered downloads.

Interactive terminals receive a live Rich table. Redirected output automatically becomes plain text. Explicit modes are available for automation:

```console
hf-download-live-monitor watch owner/repository --local-dir ./download --plain --once
hf-download-live-monitor watch owner/repository --local-dir ./download --json --once
hf-download-live-monitor watch owner/repository --local-dir ./download --jsonl
```

Use `--ascii` for ASCII-only plain output and `--no-color` to disable color. Run `hf-download-live-monitor watch --help` for the full option reference.

## Attach to a running download

On Windows, WSL, and Linux, discover a running CLI download automatically:

```console
hf-download-live-monitor attach
hf-download-live-monitor attach --pid 1234
hf-download-live-monitor attach --all --once --jsonl
```

When several downloads are visible, an interactive terminal offers a numbered selection. Non-interactive callers must use `--pid`. Continuous multi-download rendering is deliberately rejected; use `--all --once` for an atomic snapshot.

## Launch and monitor

Managed mode starts the official Hugging Face CLI and propagates its exit status:

```console
hf-download-live-monitor run owner/repository --local-dir ./download
hf-download-live-monitor run owner/dataset --repo-type dataset --local-dir ./data --include "*.parquet"
```

The first Ctrl+C requests graceful child termination. If shutdown times out or is interrupted again, the child is killed. Only documented download arguments are forwarded; no command is evaluated through a shell.

## Standalone releases

Tagged releases are configured to publish wheels and source distributions through PyPI Trusted Publishing and attach checksummed native Windows, Linux, and macOS executables. These workflows become active after repository ownership and release environments are configured.

## Platforms and privacy

Explicit watch mode works on native Windows, WSL, Linux, and macOS. It queries repository metadata through `huggingface_hub` and observes only the selected local directory. HF Download Live Monitor has no telemetry and does not store credentials. Tokens are never included in structured output.

See [architecture](docs/architecture.md), [JSON schema](docs/json-schema.md), and [security policy](SECURITY.md).

## Troubleshooting

- `repository_not_found`: confirm the repository ID, type, revision, and authentication.
- `authentication_required`: authenticate with `hf auth login` or the normal Hugging Face environment configuration.
- `metadata_unavailable`: update `huggingface_hub` within the supported range and retry.
- Incorrect totals: pass the same filenames, include patterns, and exclude patterns used by the download command.

Licensed under the MIT License.
