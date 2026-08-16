from importlib.metadata import entry_points
from pathlib import Path


def test_declared_readme_exists() -> None:
    assert Path("README.md").is_file()


def test_console_entry_point_is_installed() -> None:
    commands = {item.name: item.value for item in entry_points(group="console_scripts")}
    assert commands["hf-download-live-monitor"] == "hf_download_live_monitor.cli:run"


def test_package_declares_typing_support() -> None:
    assert Path("src/hf_download_live_monitor/py.typed").is_file()
