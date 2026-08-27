from pathlib import Path

import pytest

from hf_download_live_monitor.attach import SessionKey, discover_downloads, select_download
from hf_download_live_monitor.models import MonitorError
from hf_download_live_monitor.processes import ProcessRecord


class FakeProvider:
    def __init__(self, records: tuple[ProcessRecord, ...]) -> None:
        self.records = records

    def discover(self) -> tuple[ProcessRecord, ...]:
        return self.records


def download(pid: int, repo: str, start_token: str = "unknown") -> ProcessRecord:
    return ProcessRecord(
        pid,
        ("hf", "download", repo, "--local-dir", f"out-{pid}"),
        Path.cwd(),
        start_token,
    )


def test_discover_downloads_filters_and_sorts() -> None:
    provider = FakeProvider(
        (
            download(9, "b/repo"),
            ProcessRecord(2, ("python", "app.py"), Path.cwd()),
            download(3, "a/repo"),
        )
    )
    assert [item.pid for item in discover_downloads(provider)] == [3, 9]


def test_discover_downloads_orders_by_stable_session_key() -> None:
    candidates = discover_downloads(
        FakeProvider((download(3, "z/repo", "2"), download(9, "a/repo", "1")))
    )

    assert [item.spec.repo for item in candidates] == ["a/repo", "z/repo"]
    assert candidates[0].key == SessionKey(
        "model",
        "a/repo",
        str(candidates[0].spec.local_dir),
        "main",
        candidates[0].process,
    )


def test_discovery_discards_raw_arguments_and_tokens() -> None:
    record = ProcessRecord(
        7,
        (
            "hf",
            "download",
            "owner/repo",
            "--local-dir",
            "out",
            "--token",
            "hf_secret",
        ),
        Path.cwd(),
        "100",
    )

    candidate = discover_downloads(FakeProvider((record,)))[0]

    assert not hasattr(candidate, "args")
    assert "hf_secret" not in repr(candidate)
    assert candidate.process.pid == 7


def test_select_download_automatically_uses_single_match() -> None:
    assert select_download(discover_downloads(FakeProvider((download(7, "a/repo"),)))).pid == 7


def test_select_download_by_pid() -> None:
    candidates = discover_downloads(FakeProvider((download(7, "a/repo"), download(8, "b/repo"))))
    assert select_download(candidates, pid=8).pid == 8


def test_select_download_reports_no_match() -> None:
    with pytest.raises(MonitorError) as caught:
        select_download(())
    assert caught.value.code == "no_download_found"


def test_select_download_requires_choice_when_noninteractive() -> None:
    candidates = discover_downloads(FakeProvider((download(7, "a/repo"), download(8, "b/repo"))))
    with pytest.raises(MonitorError) as caught:
        select_download(candidates, interactive=False)
    assert caught.value.code == "multiple_downloads"


def test_select_download_accepts_interactive_number() -> None:
    candidates = discover_downloads(FakeProvider((download(7, "a/repo"), download(8, "b/repo"))))
    assert select_download(candidates, interactive=True, input_fn=lambda _: "2").pid == 8
