"""Run deterministic end-to-end download simulations without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = CHECKOUT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from hf_download_live_monitor.app import WatchApplication  # noqa: E402
from hf_download_live_monitor.engine import ProgressEngine  # noqa: E402
from hf_download_live_monitor.filesystem import FileSystemObserver  # noqa: E402
from hf_download_live_monitor.integrity import IntegrityVerifier  # noqa: E402
from hf_download_live_monitor.models import (  # noqa: E402
    DownloadPlan,
    DownloadSpec,
    FileObservation,
    FileState,
    ManifestFile,
    ProgressSnapshot,
)
from hf_download_live_monitor.runner import ManagedDownload  # noqa: E402


@dataclass(frozen=True, slots=True)
class SimulationResult:
    exit_code: int
    snapshots: tuple[ProgressSnapshot, ...]
    child_reaped: bool
    handshake_acknowledged: bool


class _PreparedRepository:
    def __init__(self, plan: DownloadPlan) -> None:
        self._plan = plan

    def prepare(self, spec: DownloadSpec) -> DownloadPlan:
        return self._plan

    def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]:
        return self._plan.manifest


class _Collector:
    def __init__(self, *, fail_render: bool = False) -> None:
        self.snapshots: list[ProgressSnapshot] = []
        self._fail_render = fail_render

    def render(self, snapshot: ProgressSnapshot) -> None:
        self.snapshots.append(snapshot)
        if self._fail_render:
            raise RuntimeError("simulated renderer failure")

    def close(self) -> None:
        pass


class _HandshakeObserver:
    def __init__(self, ready_marker: Path, continue_marker: Path) -> None:
        self._observer = FileSystemObserver()
        self._ready_marker = ready_marker
        self._continue_marker = continue_marker
        self.acknowledged = False

    def observe(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        now: float,
    ) -> tuple[FileObservation, ...]:
        observations = self._observer.observe(spec, manifest, now)
        if self._ready_marker.is_file() and any(
            0 < item.visible_bytes < item.expected_bytes for item in observations
        ):
            self.acknowledged = True
            self._continue_marker.touch()
        return observations


def run_simulation(
    plan: DownloadPlan,
    *,
    content: bytes,
    chunk_size: int = 1024,
    delay: float = 0.002,
    corrupt: bool = False,
    fail_render: bool = False,
) -> SimulationResult:
    source = plan.spec.local_dir / ".simulation-source"
    plan.spec.local_dir.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    ready_marker = plan.spec.local_dir / ".simulation-ready"
    continue_marker = plan.spec.local_dir / ".simulation-continue"
    fixture = CHECKOUT_ROOT / "tests" / "fixtures" / "incremental_downloader.py"
    process: subprocess.Popen[bytes] | None = None

    def start(_: tuple[str, ...]) -> subprocess.Popen[bytes]:
        nonlocal process
        command = [
            sys.executable,
            str(fixture),
            str(plan.spec.local_dir),
            plan.manifest[0].filename,
            str(source),
            "--chunk-size",
            str(chunk_size),
            "--delay",
            str(delay),
            "--ready-marker",
            str(ready_marker),
            "--continue-marker",
            str(continue_marker),
        ]
        if corrupt:
            command.append("--corrupt")
        process = subprocess.Popen(command)
        return process

    collector = _Collector(fail_render=fail_render)
    observer = _HandshakeObserver(ready_marker, continue_marker)
    application = WatchApplication(
        repository=_PreparedRepository(plan),
        observer=observer,
        engine=ProgressEngine(verifier=IntegrityVerifier()),
        renderer=collector,
        refresh=0.001,
    )
    try:
        try:
            exit_code = ManagedDownload(application, process_factory=start).run(
                plan.spec, plan=plan
            )
        except BaseException as exc:
            if process is not None:
                exc.__dict__["simulation_process"] = process
            raise
    finally:
        continue_marker.touch()
    if process is None:
        raise RuntimeError("simulation child was not started")
    return SimulationResult(
        exit_code,
        tuple(collector.snapshots),
        process.poll() is not None,
        observer.acknowledged,
    )


def _content(run: int, size: int = 64 * 1024) -> bytes:
    block = hashlib.sha256(f"simulation-{run}".encode()).digest()
    return (block * ((size + len(block) - 1) // len(block)))[:size]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=4)
    args = parser.parse_args()
    if args.repeat <= 0:
        parser.error("repeat must be positive")
    succeeded = True
    for run in range(1, args.repeat + 1):
        with tempfile.TemporaryDirectory(prefix="hf-monitor-simulation-") as directory:
            destination = Path(directory)
            content = _content(run)
            spec = DownloadSpec("local/simulation", destination, filenames=("model.bin",))
            plan = DownloadPlan(
                spec,
                "main",
                (ManifestFile("model.bin", len(content), hashlib.sha256(content).hexdigest()),),
            )
            result = run_simulation(plan, content=content)
            intermediate = result.handshake_acknowledged and any(
                0 < snapshot.downloaded_bytes < len(content) for snapshot in result.snapshots
            )
            final_state = result.snapshots[-1].files[0].state
            verified = final_state is FileState.VERIFIED
            summary = {
                "run": run,
                "snapshots": len(result.snapshots),
                "intermediate_seen": intermediate,
                "final_state": final_state.value,
                "verified": verified,
                "exit_code": result.exit_code,
                "child_reaped": result.child_reaped,
            }
            print(json.dumps(summary, sort_keys=True), flush=True)
            succeeded &= result.exit_code == 0 and intermediate and verified and result.child_reaped
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
