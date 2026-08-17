from importlib.metadata import entry_points
from pathlib import Path

import pytest

from scripts import build_standalone


def test_declared_readme_exists() -> None:
    assert Path("README.md").is_file()


def test_console_entry_point_is_installed() -> None:
    commands = {item.name: item.value for item in entry_points(group="console_scripts")}
    assert commands["hf-download-live-monitor"] == "hf_download_live_monitor.cli:run"


def test_package_declares_typing_support() -> None:
    assert Path("src/hf_download_live_monitor/py.typed").is_file()


def test_workflow_yaml_parser_is_a_development_dependency_only() -> None:
    configuration = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"pyyaml>=6,<7"' in configuration


@pytest.mark.parametrize(
    ("system", "machine"),
    [
        ("Windows", "AMD64"),
        ("Windows", "ARM64"),
        ("Linux", "x86_64"),
        ("Linux", "aarch64"),
        ("Darwin", "x64"),
        ("Darwin", "arm64"),
    ],
)
def test_standalone_distribution_names_are_collision_free(system: str, machine: str) -> None:
    candidates = [
        ("Windows", "AMD64"),
        ("Windows", "ARM64"),
        ("Linux", "x86_64"),
        ("Linux", "aarch64"),
        ("Darwin", "x64"),
        ("Darwin", "arm64"),
    ]
    combinations = {
        build_standalone.artifact_name(candidate_system, candidate_machine)
        for candidate_system, candidate_machine in candidates
    }
    assert len(combinations) == 6
    assert build_standalone.artifact_name(system, machine) in combinations
