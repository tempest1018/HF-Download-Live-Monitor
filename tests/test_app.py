from pathlib import Path

from hf_download_live_monitor.app import WatchApplication
from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.models import (
    DownloadSpec,
    FileObservation,
    ManifestFile,
    MonitorError,
    ProgressSnapshot,
)


class FakeRepository:
    def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]:
        return (ManifestFile("model.bin", 10),)


class FakeObserver:
    def observe(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        now: float,
    ) -> tuple[FileObservation, ...]:
        return (FileObservation("model.bin", 10, 5, None, 5, now),)


class RecordingRenderer:
    def __init__(self) -> None:
        self.snapshots: list[ProgressSnapshot] = []
        self.closed = False

    def render(self, snapshot: ProgressSnapshot) -> None:
        self.snapshots.append(snapshot)

    def close(self) -> None:
        self.closed = True


def test_once_renders_one_snapshot_and_closes() -> None:
    renderer = RecordingRenderer()
    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=renderer,
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )

    assert app.run(DownloadSpec("owner/repo", Path("out")), once=True) == 0
    assert len(renderer.snapshots) == 1
    assert renderer.closed


def test_keyboard_interrupt_exits_cleanly_and_closes() -> None:
    renderer = RecordingRenderer()

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=renderer,
        clock=lambda: 5.0,
        sleeper=interrupt,
    )

    assert app.run(DownloadSpec("owner/repo", Path("out")), once=False) == 0
    assert renderer.closed


def test_recoverable_metadata_failure_uses_bounded_exponential_retry() -> None:
    class FlakyRepository:
        attempts = 0

        def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]:
            self.attempts += 1
            if self.attempts < 3:
                raise MonitorError("hub_error", "temporary", recoverable=True)
            return (ManifestFile("model.bin", 10),)

    sleeps: list[float] = []
    renderer = RecordingRenderer()
    app = WatchApplication(
        repository=FlakyRepository(),
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=renderer,
        clock=lambda: 5.0,
        sleeper=sleeps.append,
    )

    assert app.run(DownloadSpec("owner/repo", Path("out")), once=True) == 0
    assert sleeps == [1.0, 2.0]


def test_stop_condition_ends_after_final_snapshot() -> None:
    renderer = RecordingRenderer()
    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=renderer,
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )
    assert app.run(DownloadSpec("owner/repo", Path("out")), stop_when=lambda: True) == 0
    assert len(renderer.snapshots) == 1
