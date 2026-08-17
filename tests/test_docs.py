import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hf_download_live_monitor.cli import cli
from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import (
    DownloadSpec,
    FileProgress,
    FileState,
    MonitorError,
    ProgressSnapshot,
)
from hf_download_live_monitor.renderers import snapshot_to_dict
from scripts.build_standalone import artifact_name


@pytest.mark.parametrize(
    "filename",
    [
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "docs/json-schema.md",
        "docs/architecture.md",
    ],
)
def test_distribution_document_exists_and_is_not_empty(filename: str) -> None:
    assert Path(filename).read_text(encoding="utf-8").strip()


def test_readme_documents_all_output_modes() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert readme.startswith("# HF Download Live Monitor\n")
    assert "hf-download-live-monitor" in readme
    for value in ("--plain", "--json", "--jsonl", "--once"):
        assert value in readme


def test_complete_user_manual_is_linked_and_covers_operations() -> None:
    readme = Path("README.md").read_text(encoding="utf-8").lower()
    manual_path = Path("docs/user-manual.md")
    assert "docs/user-manual.md" in readme
    manual = manual_path.read_text(encoding="utf-8").lower()
    required_topics = (
        "prerequisites",
        "authentication",
        "installation",
        "verify the installation",
        "quick start",
        "watch mode",
        "attach mode",
        "run mode",
        "output formats",
        "update",
        "downgrade",
        "uninstall",
        "troubleshooting",
        "privacy",
        "support",
    )
    for topic in required_topics:
        assert topic in manual


def test_manual_uses_canonical_product_and_command() -> None:
    manual = Path("docs/user-manual.md").read_text(encoding="utf-8")
    assert manual.startswith("# HF Download Live Monitor User Manual\n")
    assert "hf-download-live-monitor --help" in manual


@pytest.mark.parametrize(
    "required",
    [
        "Adaptive Focus",
        "ARM64",
        "complete_unverified",
        "verified",
        "--reduced-motion",
        "--view",
    ],
)
def test_manual_documents_new_public_contract(required: str) -> None:
    manual = Path("docs/user-manual.md").read_text(encoding="utf-8")
    assert required in manual


def test_json_schema_documents_version_two() -> None:
    schema = Path("docs/json-schema.md").read_text(encoding="utf-8")
    assert '"schema_version": 2' in schema
    assert '"resolved_revision"' in schema


def test_documented_cli_flags_exist_in_command_help() -> None:
    manual = Path("docs/user-manual.md").read_text(encoding="utf-8")
    runner = CliRunner()
    help_by_command = {
        command: runner.invoke(cli, [command, "--help"]).stdout
        for command in ("attach", "run", "watch")
    }
    for command in ("attach", "run", "watch"):
        help_text = help_by_command[command]
        assert help_text
        section_start = manual.index(f"## {command.title()} mode")
        section_end = manual.find("\n## ", section_start + 1)
        section = manual[section_start : None if section_end == -1 else section_end]
        documented = set(re.findall(r"(?<![a-z-])(--[a-z][a-z-]+)", section))
        for flag in documented:
            assert flag in help_text, f"{command}: {flag}"


def test_manual_names_every_supported_standalone_artifact() -> None:
    manual = Path("docs/user-manual.md").read_text(encoding="utf-8")
    platforms = (
        ("Windows", "AMD64"),
        ("Windows", "ARM64"),
        ("Linux", "x86_64"),
        ("Linux", "aarch64"),
        ("Darwin", "x64"),
        ("Darwin", "arm64"),
    )
    for system, machine in platforms:
        assert artifact_name(system, machine) in manual


def test_manual_exit_code_table_matches_runtime_mapping() -> None:
    manual = Path("docs/user-manual.md").read_text(encoding="utf-8")
    rows = dict(re.findall(r"\| `([a-z]+)` \| (\d+) \|", manual))
    assert rows == {category.value: str(exit_code_for(category)) for category in ErrorCategory}
    assert "| `downloader` | 6 | Official downloader launch or managed cleanup failure. |" in manual


def test_schema_example_matches_representative_runtime_snapshot() -> None:
    schema = Path("docs/json-schema.md").read_text(encoding="utf-8")
    example = json.loads(re.search(r"```json\n(.*?)\n```", schema, re.DOTALL).group(1))  # type: ignore[union-attr]
    fixture = snapshot_to_dict(
        ProgressSnapshot(
            spec=DownloadSpec(
                "owner/repository",
                Path("/downloads/repository"),
                revision="0123456789abcdef0123456789abcdef01234567",
            ),
            requested_revision="main",
            files=(FileProgress("model.bin", 1048576, 1048576, FileState.VERIFIED, 0.0, 0.0),),
            observed_at=1786965600.5,
            downloaded_bytes=1048576,
            expected_bytes=1048576,
            rate_bytes_per_second=343756.0,
            eta_seconds=0.0,
            verified_files=1,
            errors=(
                MonitorError(
                    "temporary_observation_error",
                    "a redacted, user-safe diagnostic",
                    True,
                    ErrorCategory.MONITOR,
                ),
            ),
        )
    )
    example["repository"]["local_dir"] = fixture["repository"]["local_dir"]
    assert example == fixture
    assert isinstance(example["observed_at"], float)
    assert isinstance(example["downloaded_bytes"], int)
    assert example["files"][0]["state"] in {state.value for state in FileState}
    assert example["integrity"] == {
        "verified_files": 1,
        "complete_unverified_files": 0,
        "failed_files": 0,
    }
    assert example["errors"][0] == {
        "category": "monitor",
        "code": "temporary_observation_error",
        "message": "a redacted, user-safe diagnostic",
        "recoverable": True,
    }


def test_schema_documents_exact_runtime_state_vocabulary() -> None:
    schema = Path("docs/json-schema.md").read_text(encoding="utf-8")
    documented = set(
        re.findall(r"`([a-z][a-z_]+)`", schema.split("File states are", 1)[1].split(".", 1)[0])
    )
    assert documented == {state.value for state in FileState}


def test_local_markdown_links_resolve() -> None:
    for source in (Path("README.md"), *Path("docs").glob("*.md")):
        text = source.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            assert (source.parent / path).exists(), f"{source}: {target}"


def test_interrupt_reconciliation_contract_is_documented_without_old_claims() -> None:
    manual = Path("docs/user-manual.md").read_text(encoding="utf-8")
    architecture = Path("docs/architecture.md").read_text(encoding="utf-8")
    schema = Path("docs/json-schema.md").read_text(encoding="utf-8")
    combined = "\n".join((manual, architecture, schema))

    for required in (
        "forced final observation",
        "final integrity failure",
        "exit code `9`",
        "exit code `8`",
        "exit code `6`",
        "stopped and reaped",
        "cleanup failure takes precedence",
        "second interrupt",
    ):
        assert required in combined
    assert (
        "Ctrl+C closes the display and resources but does not promise a final observation"
        not in combined
    )
    assert "handled Ctrl+C in `watch` or `attach` mode" not in manual
    assert "Ctrl+C returns `0`" not in combined
