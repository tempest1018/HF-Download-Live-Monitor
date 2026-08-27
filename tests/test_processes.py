import json
from pathlib import Path

import psutil
import pytest

from hf_download_live_monitor.processes import (
    PosixProcessProvider,
    ProcessIdentity,
    ProcessRecord,
    PsutilProcessProvider,
    WindowsProcessProvider,
    system_process_provider,
)


class FakePsutilProcess:
    def __init__(
        self,
        pid: int,
        created: float = 1234.5,
        args: tuple[str, ...] = ("hf", "download", "owner/repo"),
        cwd: str = "out",
        error: BaseException | None = None,
    ) -> None:
        self.pid = pid
        self._created = created
        self._args = args
        self._cwd = cwd
        self._error = error

    def cmdline(self) -> list[str]:
        if self._error is not None:
            raise self._error
        return list(self._args)

    def cwd(self) -> str:
        return self._cwd

    def create_time(self) -> float:
        return self._created


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
    assert isinstance(system_process_provider("nt"), PsutilProcessProvider)
    assert isinstance(system_process_provider("posix"), PsutilProcessProvider)
    with pytest.raises(ValueError, match="unsupported"):
        system_process_provider("java")


def test_process_identity_distinguishes_reused_pid() -> None:
    first = ProcessRecord(41, ("hf",), Path("out"), "100")
    second = ProcessRecord(41, ("hf",), Path("out"), "200")

    assert first.identity == ProcessIdentity(41, "100")
    assert first.identity != second.identity


def test_psutil_provider_records_stable_start_token_and_skips_denied_process() -> None:
    allowed = FakePsutilProcess(41)
    denied = FakePsutilProcess(42, error=PermissionError("denied"))

    records = PsutilProcessProvider(iterator=lambda: (denied, allowed)).discover()

    assert records == (
        ProcessRecord(
            41,
            ("hf", "download", "owner/repo"),
            Path("out"),
            "1234.500000000",
        ),
    )


def test_psutil_provider_preserves_records_before_iterator_failure() -> None:
    def processes():
        yield FakePsutilProcess(41)
        raise psutil.Error("process table changed")

    records = PsutilProcessProvider(iterator=processes).discover()
    assert [record.pid for record in records] == [41]
