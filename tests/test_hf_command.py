from pathlib import Path

import pytest

from hf_live_monitor.hf_command import parse_download_process
from hf_live_monitor.models import RepoType
from hf_live_monitor.processes import ProcessRecord


def record(*args: str, cwd: Path = Path("/work")) -> ProcessRecord:
    return ProcessRecord(42, args, cwd)


def test_parse_standard_download_and_resolve_relative_directory() -> None:
    spec = parse_download_process(
        record("/usr/bin/hf", "download", "owner/repo", "a.bin", "--local-dir", "models")
    )
    assert spec is not None
    assert spec.repo == "owner/repo"
    assert spec.local_dir == Path("/work/models").resolve()
    assert spec.filenames == ("a.bin",)


def test_parse_equals_options_and_repository_type() -> None:
    spec = parse_download_process(
        record(
            "hf.exe",
            "download",
            "owner/data",
            "--local-dir=C:/models",
            "--repo-type=dataset",
            "--revision=v2",
            "--include=*.json",
            "--exclude=*private*",
        )
    )
    assert spec is not None
    assert spec.repo_type is RepoType.DATASET
    assert spec.revision == "v2"
    assert spec.includes == ("*.json",)
    assert spec.excludes == ("*private*",)


def test_parse_python_module_cli() -> None:
    spec = parse_download_process(
        record(
            "python",
            "-m",
            "huggingface_hub.commands.huggingface_cli",
            "download",
            "owner/space",
            "--repo-type",
            "space",
            "--local-dir",
            "/tmp/space",
        )
    )
    assert spec is not None
    assert spec.repo_type is RepoType.SPACE


@pytest.mark.parametrize(
    "args",
    [
        ("python", "download", "file"),
        ("hf", "upload", "owner/repo"),
        ("hf", "download", "owner/repo"),
    ],
)
def test_unrelated_or_unmonitorable_command_returns_none(args: tuple[str, ...]) -> None:
    assert parse_download_process(record(*args)) is None


def test_token_option_is_not_retained() -> None:
    spec = parse_download_process(
        record(
            "hf",
            "download",
            "owner/repo",
            "--local-dir",
            "out",
            "--token",
            "hf_secret",
        )
    )
    assert spec is not None
    assert "hf_secret" not in repr(spec)
