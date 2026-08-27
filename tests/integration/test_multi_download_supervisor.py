from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

from hf_download_live_monitor.attach import discover_downloads
from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.filesystem import FileSystemObserver
from hf_download_live_monitor.models import DownloadPlan, DownloadSpec, ManifestFile
from hf_download_live_monitor.processes import PsutilProcessProvider
from hf_download_live_monitor.supervisor import DownloadSupervisor
from hf_download_live_monitor.supervisor_models import EventType, SessionLifecycle


class FixtureRepository:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self._contents = contents

    def prepare(self, spec: DownloadSpec) -> DownloadPlan:
        content = self._contents[spec.repo]
        manifest = ManifestFile("model.bin", len(content), hashlib.sha256(content).hexdigest())
        resolved = DownloadSpec(spec.repo, spec.local_dir, revision="a" * 40)
        return DownloadPlan(resolved, spec.revision, (manifest,))

    def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]:
        raise AssertionError("prepare supplies the manifest")


def _child(
    destination: Path,
    content: bytes,
    delay: float,
    ready: Path,
    proceed: Path,
) -> subprocess.Popen[bytes]:
    source = destination.parent / f"{destination.name}-source.bin"
    source.write_bytes(content)
    script = "\n".join(
        (
            "import pathlib, sys, time",
            "p = pathlib.Path(sys.argv[sys.argv.index('--local-dir') + 1])",
            "p.mkdir(parents=True, exist_ok=True)",
            "data = pathlib.Path(sys.argv[1]).read_bytes()",
            "delay = float(sys.argv[2])",
            "ready = pathlib.Path(sys.argv[3])",
            "proceed = pathlib.Path(sys.argv[4])",
            "ready.touch()",
            "deadline = time.monotonic() + 10",
            "while not proceed.exists() and time.monotonic() < deadline:",
            "    time.sleep(0.01)",
            "assert proceed.exists(), 'monitor handshake timed out'",
            "with (p / 'model.bin').open('wb') as handle:",
            "    for offset in range(0, len(data), 512):",
            "        handle.write(data[offset:offset + 512])",
            "        handle.flush()",
            "        time.sleep(delay)",
        )
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(source),
            str(delay),
            str(ready),
            str(proceed),
            "hf",
            "download",
            destination.name,
            "--local-dir",
            str(destination),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_real_concurrent_processes_are_reconciled_and_finalized(tmp_path: Path) -> None:
    expected = {
        "good": b"good model" * 8192,
        "bad": b"expected model" * 8192,
    }
    tracked_pids: set[int] = set()

    def discover():
        return tuple(
            item for item in discover_downloads(PsutilProcessProvider()) if item.pid in tracked_pids
        )

    supervisor = DownloadSupervisor(
        discover,
        discovery_refresh=0.01,
        retention=30,
        repository=FixtureRepository(expected),
        observer=FileSystemObserver(),
        engine_factory=ProgressEngine,
        refresh=0.005,
    )
    supervisor.tick()  # The monitor is live before either child appears.
    ready = (tmp_path / "good-ready", tmp_path / "bad-ready")
    proceed = (tmp_path / "good-proceed", tmp_path / "bad-proceed")
    children = (
        _child(tmp_path / "good", expected["good"], 0.002, ready[0], proceed[0]),
        _child(
            tmp_path / "bad",
            b"corrupt model" * 8192,
            0.0005,
            ready[1],
            proceed[1],
        ),
    )
    tracked_pids.update(child.pid for child in children)
    try:
        ready_deadline = time.monotonic() + 5
        while time.monotonic() < ready_deadline and not all(path.exists() for path in ready):
            time.sleep(0.01)
        assert all(path.exists() for path in ready)
        discovery_deadline = time.monotonic() + 5
        while time.monotonic() < discovery_deadline:
            supervisor.tick()
            if len(supervisor.snapshot.sessions) == 2:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("supervisor did not discover both waiting child processes")
        for path in proceed:
            path.touch()

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            supervisor.tick()
            finals = [
                item
                for item in supervisor.snapshot.sessions
                if item.lifecycle
                in {SessionLifecycle.COMPLETED, SessionLifecycle.FAILED, SessionLifecycle.LOST}
            ]
            if len(finals) == 2:
                break
            time.sleep(0.005)
        else:
            raise AssertionError("supervisor did not finalize both child processes")
        assert {item.lifecycle for item in finals} == {
            SessionLifecycle.COMPLETED,
            SessionLifecycle.FAILED,
        }
        event_types = [item.event for item in supervisor.events]
        assert event_types.count(EventType.SESSION_ADDED) == 2
        assert event_types.count(EventType.SESSION_FINALIZED) == 2
        sequences = [item.sequence for item in supervisor.events]
        assert sequences == list(range(1, len(sequences) + 1))
    finally:
        for path in proceed:
            path.touch(exist_ok=True)
        for child in children:
            child.wait(timeout=5)
        supervisor.shutdown()
    assert all(child.returncode == 0 for child in children)
    assert supervisor.events[-1].event is EventType.SUPERVISOR_STOPPED
