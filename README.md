# HF Download Live Monitor

Download history is optional, local-only, and disabled by default. See the
[user manual](docs/user-manual.md#local-private-history) for controls and the
[second-PC/VM acceptance guide](docs/second-pc-vm-acceptance.md) for independent testing.

HF Download Live Monitor is a privacy-conscious, cross-platform terminal monitor for Hugging Face downloads. It reports accurate per-file and aggregate progress without replacing the official download client.

New here? Follow the [complete user manual](docs/user-manual.md) for prerequisites,
installation, authentication, first use, maintenance, and troubleshooting. Automation
consumers should also read the [structured-output schema](docs/json-schema.md).

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
hf-download-live-monitor attach --all
hf-download-live-monitor attach --all --once --jsonl
```

When several downloads are visible, an interactive terminal offers a numbered selection.
Non-interactive callers can use `--pid`, or continuously supervise every visible download
with `attach --all`. The aggregate dashboard follows newly started downloads and retains
final results briefly. Pressing `q` or Ctrl+C stops only the monitor; it does not terminate
attached downloader processes.

For automation, `--jsonl` emits versioned `supervisor_event` objects and `--json` emits
one final aggregate snapshot. Tune discovery with `--discovery-refresh`, completed-result
visibility with `--retention`, and resource bounds with `--max-sessions`.

## Launch and monitor

Managed mode starts the official Hugging Face CLI and propagates its exit status:

```console
hf-download-live-monitor run owner/repository --local-dir ./download
hf-download-live-monitor run owner/dataset --repo-type dataset --local-dir ./data --include "*.parquet"
```

The first Ctrl+C requests graceful child termination. If shutdown times out or is interrupted again, the child is killed. Only documented download arguments are forwarded; no command is evaluated through a shell.

## Standalone releases

The release workflow is configured to build checksummed native Windows, Linux, and
macOS executables for x86-64 and ARM64, plus wheels and source distributions. Treat a
platform build as available only when it is present in a completed GitHub release;
workflow configuration is not evidence that an artifact has passed on GitHub.

Releases use a draft-first, build-once process. A GPG-signed tag is checked against an
independently stored, fingerprint-pinned public key and must identify a protected-main
commit before it stages a private draft release; tag creation does not publish the release
publicly. The separately approved
`Publish GitHub Release` workflow verifies the complete bundle before publication.
GitHub artifact attestations identify files built by CI. PyPI promotion is optional and
manual through the isolated `Publish PyPI` workflow.

## Platforms and privacy

Interactive terminals use the responsive **Adaptive Focus** dashboard. It selects a
narrow, normal, or wide arrangement whenever the terminal is resized; choose the
starting information density with `--view compact`, `--view balanced`, or
`--view detailed`. Use `--reduced-motion`, `--ascii`, or `--no-color` for accessibility
and compatibility. Redirected output automatically remains readable in logs.

Before starting a managed download, the application resolves the requested revision
to an immutable commit, checks repository access and destination capacity, and later
verifies SHA-256 metadata when Hugging Face supplies it. A size-complete file without
a supported digest is reported as `complete_unverified`, never as verified.

Python wheels are the portable fallback on supported Python installations. The
configured standalone matrix is:

| Platform | x86-64 | ARM64 |
| --- | --- | --- |
| Windows | workflow configured | workflow configured |
| Linux | workflow configured | workflow configured |
| macOS | workflow configured | workflow configured |

Availability is determined by the assets attached to a completed release. Verify the
adjacent `.sha256` file before running any standalone executable.

Explicit watch mode works on native Windows, WSL, Linux, and macOS. It queries repository metadata through `huggingface_hub` and observes only the selected local directory. HF Download Live Monitor has no telemetry and does not store credentials. Tokens are never included in structured output.

See [architecture](docs/architecture.md), [JSON schema](docs/json-schema.md), and [security policy](SECURITY.md).

## Troubleshooting

- `repository_not_found`: confirm the repository ID, type, revision, and authentication.
- `authentication_required`: authenticate with `hf auth login` or the normal Hugging Face environment configuration.
- `metadata_unavailable`: update `huggingface_hub` within the supported range and retry.
- Incorrect totals: pass the same filenames, include patterns, and exclude patterns used by the download command.

Licensed under the MIT License.
