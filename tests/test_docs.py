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
    for command in ("attach", "run", "watch"):
        help_text = runner.invoke(cli, [command, "--help"]).stdout
        assert help_text
        documented = set(
            re.findall(r"`(--[a-z][a-z-]+)`", manual[manual.index(f"## {command.title()} mode") :])
        )
        for flag in documented:
            if flag in {"--help"}:
                continue
            assert flag in help_text or any(
                flag in runner.invoke(cli, [other, "--help"]).stdout
                for other in ("attach", "run", "watch")
            ), flag


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


def test_schema_example_matches_runtime_snapshot_shape() -> None:
    schema = Path("docs/json-schema.md").read_text(encoding="utf-8")
    example = json.loads(re.search(r"```json\n(.*?)\n```", schema, re.DOTALL).group(1))  # type: ignore[union-attr]
    fixture = snapshot_to_dict(
        ProgressSnapshot(
            spec=DownloadSpec("owner/repository", Path("/downloads/repository"), revision="a" * 40),
            requested_revision="main",
            files=(FileProgress("model.bin", 10, 10, FileState.VERIFIED, 0.0, 0.0),),
            observed_at=1.0,
            downloaded_bytes=10,
            expected_bytes=10,
            rate_bytes_per_second=0.0,
            eta_seconds=0.0,
            verified_files=1,
            errors=(MonitorError("example", "safe", True, ErrorCategory.MONITOR),),
        )
    )
    assert example["schema_version"] == 2
    assert example.keys() == fixture.keys()
    assert example["repository"].keys() == fixture["repository"].keys()
    assert example["integrity"].keys() == fixture["integrity"].keys()
    assert example["files"][0].keys() == fixture["files"][0].keys()
    assert example["errors"][0].keys() == fixture["errors"][0].keys()


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
