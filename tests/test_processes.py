import json
from pathlib import Path

import pytest

from hf_download_live_monitor.processes import (
    PosixProcessProvider,
    WindowsProcessProvider,
    system_process_provider,
)


def test_posix_provider_reads_numeric_process_entries(tmp_path: Path) -> None:
    process = tmp_path / "123"
    process.mkdir()
    (process / "cmdline").write_bytes(b"hf\0download\0owner/repo\0--local-dir\0out\0")
    (process / "cwd").mkdir()
    (tmp_path / "not-a-pid").mkdir()

    records = PosixProcessProvider(tmp_path).discover()

    assert len(records) == 1
    assert records[0].pid == 123
    assert records[0].args[0] == "hf"
    assert records[0].cwd == (process / "cwd").resolve()


def test_posix_provider_skips_inaccessible_or_disappearing_entries(tmp_path: Path) -> None:
    (tmp_path / "123").mkdir()
    assert PosixProcessProvider(tmp_path).discover() == ()


def test_windows_provider_parses_cim_json() -> None:
    payload = json.dumps(
        [
            {"ProcessId": 7, "CommandLine": "hf download owner/repo --local-dir C:/out"},
            {"ProcessId": None, "CommandLine": None},
        ]
    )
    provider = WindowsProcessProvider(runner=lambda _: payload, cwd_resolver=lambda _: Path("C:/"))

    records = provider.discover()

    assert len(records) == 1
    assert records[0].pid == 7
    assert records[0].args[:2] == ("hf", "download")


def test_windows_provider_tolerates_malformed_output() -> None:
    assert WindowsProcessProvider(runner=lambda _: "not json").discover() == ()


def test_system_provider_selects_requested_platform() -> None:
    assert isinstance(system_process_provider("nt"), WindowsProcessProvider)
    assert isinstance(system_process_provider("posix"), PosixProcessProvider)
    with pytest.raises(ValueError, match="unsupported"):
        system_process_provider("java")
