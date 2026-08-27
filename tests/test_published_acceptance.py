import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from scripts.run_published_acceptance import REVISION, AcceptanceError, HubFixture, run_acceptance


def test_hub_fixture_serves_pinned_metadata_and_payload() -> None:
    content = b'{"model_type":"acceptance"}\n' * 4096
    with HubFixture(content=content, chunk_size=1024, delay=0.001) as fixture:
        metadata = json.loads(
            urlopen(
                f"{fixture.endpoint}/api/models/acceptance/tiny/revision/{REVISION}", timeout=5
            ).read()
        )
        assert metadata["sha"] == REVISION
        assert metadata["siblings"] == [
            {
                "rfilename": "config.json",
                "size": len(content),
                "lfs": {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                },
            }
        ]

        url = f"{fixture.endpoint}/acceptance/tiny/resolve/{REVISION}/config.json"
        with urlopen(Request(url, method="HEAD"), timeout=5) as response:
            assert response.headers["x-repo-commit"] == REVISION
            assert response.headers["etag"] == f'"{hashlib.sha256(content).hexdigest()}"'
            assert int(response.headers["content-length"]) == len(content)
            assert response.read() == b""
        with urlopen(url, timeout=5) as response:
            assert response.read() == content


def test_hub_fixture_rejects_mutable_revision() -> None:
    with HubFixture() as fixture:
        url = f"{fixture.endpoint}/api/models/acceptance/tiny/revision/main"
        try:
            urlopen(url, timeout=5)
        except Exception as exc:
            assert getattr(exc, "code", None) == 404
        else:
            raise AssertionError("mutable revision was accepted")


def _fake_monitor(tmp_path: Path, *, stdout_suffix: str = "", exit_code: int = 0) -> Path:
    script = tmp_path / "fake_monitor.py"
    snapshot = {
        "schema_version": 2,
        "repository": {
            "id": "acceptance/tiny",
            "requested_revision": REVISION,
            "resolved_revision": REVISION,
        },
        "integrity": {"verified_files": 1, "failed_files": 0},
        "files": [{"filename": "config.json", "state": "verified"}],
        "errors": [],
    }
    script.write_text(
        "import json, sys\n"
        "if '--help' in sys.argv:\n"
        "    print('help')\n"
        "    raise SystemExit(0)\n"
        "print('downloader progress', file=sys.stderr)\n"
        f"print(json.dumps({snapshot!r}) + {stdout_suffix!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return script


def test_run_acceptance_exercises_surface_and_writes_redacted_report(tmp_path: Path) -> None:
    monitor = _fake_monitor(tmp_path)
    hf = tmp_path / "hf-dummy"
    hf.write_text("dummy", encoding="utf-8")
    report = tmp_path / "reports" / "result.json"
    checkout = Path(__file__).resolve().parents[1]

    result = run_acceptance(
        monitor=monitor.resolve(),
        hf_executable=hf.resolve(),
        tag="v0.1.0",
        asset="hf-download-live-monitor-windows-x86_64.exe"
        if os.name == "nt"
        else "hf-download-live-monitor-linux-x86_64",
        report=report.resolve(),
        checkout_root=checkout,
    )

    assert result.outcome == "passed"
    assert [command.arguments for command in result.commands[:4]] == [
        ("--help",),
        ("watch", "--help"),
        ("attach", "--help"),
        ("run", "--help"),
    ]
    run_arguments = result.commands[-1].arguments
    assert run_arguments[:4] == ("run", "acceptance/tiny", "--revision", REVISION)
    assert "--json" in run_arguments
    assert str(hf.resolve()) in run_arguments
    payload = report.read_text(encoding="utf-8")
    assert json.loads(payload)["outcome"] == "passed"
    assert "HF_TOKEN=" not in payload
    assert "Authorization:" not in payload
    assert "Bearer " not in payload


@pytest.mark.parametrize(
    ("suffix", "exit_code", "message"),
    [("trailing", 0, "JSON"), ("", 7, "exit code")],
)
def test_run_acceptance_rejects_bad_process_result(
    tmp_path: Path, suffix: str, exit_code: int, message: str
) -> None:
    monitor = _fake_monitor(tmp_path, stdout_suffix=suffix, exit_code=exit_code)
    hf = tmp_path / "hf"
    hf.write_text("dummy", encoding="utf-8")
    with pytest.raises(AcceptanceError, match=message):
        run_acceptance(
            monitor=monitor.resolve(),
            hf_executable=hf.resolve(),
            tag="v0.1.0",
            asset="hf-download-live-monitor-windows-x86_64.exe"
            if os.name == "nt"
            else "hf-download-live-monitor-linux-x86_64",
            report=(tmp_path / "result.json").resolve(),
            checkout_root=Path(__file__).resolve().parents[1],
        )


def test_run_acceptance_rejects_report_inside_checkout(tmp_path: Path) -> None:
    checkout = Path(__file__).resolve().parents[1]
    with pytest.raises(AcceptanceError, match="outside the checkout"):
        run_acceptance(
            monitor=Path(sys.executable).resolve(),
            hf_executable=Path(sys.executable).resolve(),
            tag="v0.1.0",
            asset="hf-download-live-monitor-windows-x86_64.exe"
            if os.name == "nt"
            else "hf-download-live-monitor-linux-x86_64",
            report=(checkout / "acceptance.json").resolve(),
            checkout_root=checkout,
        )
