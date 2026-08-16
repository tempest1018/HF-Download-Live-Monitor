# HF Download Live Monitor Rename Design

## Objective

Rename the entire project from HF Live Monitor and its former repository variants to **HF Download Live Monitor**. This is an intentional breaking rename: the finished repository will not retain legacy package, import, command, executable, or artifact aliases.

## Canonical identities

| Surface | Canonical value |
| --- | --- |
| Product name | `HF Download Live Monitor` |
| GitHub repository | `tempest1018/HF-Download-Live-Monitor` |
| Python distribution | `hf-download-live-monitor` |
| Console command | `hf-download-live-monitor` |
| Standalone executable | `hf-download-live-monitor` or `hf-download-live-monitor.exe` |
| Python import package | `hf_download_live_monitor` |
| Source directory | `src/hf_download_live_monitor` |
| PyInstaller specification | `hf_download_live_monitor.spec` |
| Local project directory | `hf-download-live-monitor` |

## Scope

The rename covers package metadata, entry points, imports, source paths, tests, build scripts, standalone artifacts, CI and release workflows, README, user manual, policies, architecture documentation, changelog, contribution guidance, design specifications, and implementation plans. User-facing prose will use the complete product name.

Historical prototype files under `docs/prototypes` remain byte-for-byte preserved because an integrity test protects them. Their historical filenames and internal text are exempt from the rename. References that describe the current product around those files will use the new name.

No compatibility command, import shim, duplicate distribution entry, or legacy executable will be shipped. Existing users must uninstall the old distribution and install the new one.

## Migration behavior

The Python source tree and every internal import move atomically to `hf_download_live_monitor`. Project tests will assert the new distribution, console entry point, source package, executable names, and workflow artifact names. Documentation will include a concise breaking-rename migration note using the old distribution name only where required to explain removal.

The GitHub repository already has the correct canonical slug. The local Git remote will be changed to its canonical URL. The local workspace directory will be renamed only after all file operations and tests are complete, so commands do not run from an invalid working directory.

## Validation and delivery

Before upload, the renamed project must pass:

1. Repository-wide legacy-identifier audit, with exemptions limited to preserved prototype content, its checksum, the migration note, and historical Git data.
2. YAML parsing and workflow-version checks.
3. Ruff formatting and linting.
4. Strict Pyright analysis.
5. The complete pytest suite.
6. Source distribution and wheel builds plus Twine metadata checks.
7. Installation of the new wheel into a clean environment and execution of the new command help surfaces.
8. Standalone build-name assertions and privacy scan of the exact commit delta.

After the commit is pushed to `main`, the complete GitHub Actions run must finish successfully with zero failure annotations and zero warning annotations. Any newly exposed failure will be diagnosed, corrected, revalidated, committed, and pushed until the repository returns to that state.

## Success criteria

The work is complete only when local and remote `main` match, the canonical remote URL is configured, the local directory uses the new slug, all supported installation and execution instructions use the new identifiers, all validation passes, and the final GitHub Actions run reports every job successful with no annotations.
