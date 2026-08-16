# User Manual Design

Date: 2026-08-15
Status: Approved

## Goal

Provide a five-minute README path for new users and a complete, task-oriented manual that enables Windows, WSL, Linux, and macOS users to install, authenticate, run, verify, update, troubleshoot, and remove HF Download Live Monitor safely.

## Documentation structure

The README remains the project landing page and quick start. It links prominently to `docs/user-manual.md`, which is the authoritative end-user reference. Architecture, structured-output schema, security policy, and contributor material remain separate specialist documents.

## Manual contents

The manual covers supported platforms, Python and Hugging Face prerequisites, authentication, four installation methods, installation verification, a first successful download, all `watch`, `attach`, and `run` options and workflows, repository types, revisions and filters, output formats, private repositories, signals, updates, downgrades, uninstalling, troubleshooting by symptom and error code, privacy behavior, support diagnostics, and a readiness checklist.

Commands must be copyable, platform distinctions must be explicit, secrets must never appear in examples, and capabilities not present in the application must not be documented.

## Validation and editorial review

Automated tests verify that the manual exists, is linked from the README, names every command, and contains installation, authentication, verification, update, uninstall, troubleshooting, privacy, and platform guidance.

Three editorial passes follow implementation:

1. Completeness and technical accuracy against CLI help and source behavior.
2. Usability, task order, navigation, and copy-paste quality.
3. Privacy, terminology consistency, concision, and removal of ambiguity.
