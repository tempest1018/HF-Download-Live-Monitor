from pathlib import Path

from hf_download_live_monitor.app import WatchApplication
from hf_download_live_monitor.controls import DisplayState
from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.layout import ViewMode
from hf_download_live_monitor.models import (
    DownloadPlan,
    DownloadSpec,
    FileObservation,
    FileState,
    ManifestFile,
    MonitorError,
    ProgressSnapshot,
)


class FakeRepository:
    calls = 0

    def prepare(self, spec: DownloadSpec) -> DownloadPlan:
        self.calls += 1
        return DownloadPlan(spec, spec.revision, (ManifestFile("model.bin", 10),))

    def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]:
        return (ManifestFile("model.bin", 10),)


class FakeObserver:
    def observe(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        now: float,
    ) -> tuple[FileObservation, ...]:
        return (FileObservation("model.bin", 10, 10, 10, None, now),)


class RecordingRenderer:
    def __init__(self) -> None:
        self.snapshots: list[ProgressSnapshot] = []
        self.closed = False

    def render(self, snapshot: ProgressSnapshot) -> None:
        self.snapshots.append(snapshot)

    def close(self) -> None:
        self.closed = True

    def update_display_state(self, state: DisplayState) -> None:
        self.state = state


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


def test_keyboard_interrupt_reconciles_final_snapshot_returns_cancelled_and_closes() -> None:
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

    assert app.run(DownloadSpec("owner/repo", Path("out")), once=False) == exit_code_for(
        ErrorCategory.CANCELLED
    )
    assert len(renderer.snapshots) == 2
    assert renderer.closed


def test_keyboard_interrupt_cleanup_runs_before_final_observation() -> None:
    events: list[str] = []

    class Observer(FakeObserver):
        def observe(self, *args: object, **kwargs: object) -> tuple[FileObservation, ...]:
            events.append("observe")
            return super().observe(*args, **kwargs)  # type: ignore[arg-type]

    app = WatchApplication(
        repository=FakeRepository(),
        observer=Observer(),
        engine=ProgressEngine(),
        renderer=RecordingRenderer(),
        clock=lambda: 5.0,
        sleeper=lambda _: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert app.run(
        DownloadSpec("owner/repo", Path("out")), interrupt_cleanup=lambda: events.append("cleanup")
    ) == exit_code_for(ErrorCategory.CANCELLED)
    assert events == ["observe", "cleanup", "observe"]


def test_keyboard_interrupt_final_integrity_failure_takes_precedence() -> None:
    class PartialObserver(FakeObserver):
        def observe(self, *args: object, **kwargs: object) -> tuple[FileObservation, ...]:
            return (FileObservation("model.bin", 10, 5, None, 5, 5.0),)

    app = WatchApplication(
        repository=FakeRepository(),
        observer=PartialObserver(),
        engine=ProgressEngine(),
        renderer=RecordingRenderer(),
        clock=lambda: 5.0,
        sleeper=lambda _: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert app.run(DownloadSpec("owner/repo", Path("out"))) == exit_code_for(
        ErrorCategory.INTEGRITY
    )


def test_interrupt_final_render_failure_survives_close_failure() -> None:
    class Renderer(RecordingRenderer):
        def render(self, snapshot: ProgressSnapshot) -> None:
            super().render(snapshot)
            if len(self.snapshots) == 2:
                raise RuntimeError("final render failed")

        def close(self) -> None:
            raise OSError("sensitive renderer close detail")

    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=Renderer(),
        clock=lambda: 5.0,
        sleeper=lambda _: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    import pytest

    with pytest.raises(RuntimeError, match="final render failed") as caught:
        app.run(DownloadSpec("owner/repo", Path("out")))
    diagnostics = getattr(caught.value, "__notes__", None) or [str(caught.value.__context__)]
    assert diagnostics == ["Resource cleanup also failed (OSError)."]
    assert "sensitive renderer close detail" not in str(diagnostics)


def test_interrupt_cleanup_failure_survives_all_close_failures() -> None:
    class Renderer(RecordingRenderer):
        def close(self) -> None:
            raise OSError("sensitive renderer close detail")

    class Engine(ProgressEngine):
        def close(self) -> None:
            raise RuntimeError("sensitive engine close detail")

    class Controls:
        def poll(self, state: DisplayState) -> DisplayState:
            return state

        def close(self) -> None:
            raise ValueError("sensitive controls close detail")

    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=Engine(),
        renderer=Renderer(),
        controls=Controls(),
        clock=lambda: 5.0,
        sleeper=lambda _: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    import pytest

    with pytest.raises(MonitorError) as caught:
        app.run(
            DownloadSpec("owner/repo", Path("out")),
            interrupt_cleanup=lambda: (_ for _ in ()).throw(OSError("secret child detail")),
        )
    assert caught.value.code == "cleanup_failed"
    close_diagnostics = getattr(caught.value, "__notes__", None) or [str(caught.value.__context__)]
    assert close_diagnostics == ["Resource cleanup also failed (OSError)."]
    rendered = str(caught.value) + str(close_diagnostics)
    assert "sensitive" not in rendered
    assert "secret child detail" not in rendered


def test_cleanup_note_failure_never_masks_primary_error() -> None:
    class NoteRejectingError(Exception):
        def add_note(self, note: str) -> None:
            raise TypeError("notes are unavailable")

    primary = NoteRejectingError("primary")

    WatchApplication._attach_close_note(primary, OSError("secret cleanup detail"))

    assert str(primary) == "primary"


def test_recoverable_metadata_failure_uses_bounded_exponential_retry() -> None:
    class FlakyRepository:
        attempts = 0

        def prepare(self, spec: DownloadSpec) -> DownloadPlan:
            self.attempts += 1
            if self.attempts < 3:
                raise MonitorError("hub_error", "temporary", recoverable=True)
            return DownloadPlan(spec, spec.revision, (ManifestFile("model.bin", 10),))

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
    assert len(renderer.snapshots) == 2
    assert renderer.snapshots[-1].observed_at == 5.0


def test_once_uses_one_final_observation() -> None:
    class CountingObserver(FakeObserver):
        calls = 0

        def observe(self, *args: object, **kwargs: object) -> tuple[FileObservation, ...]:
            self.calls += 1
            return super().observe(*args, **kwargs)  # type: ignore[arg-type]

    observer = CountingObserver()
    renderer = RecordingRenderer()
    app = WatchApplication(
        repository=FakeRepository(),
        observer=observer,
        engine=ProgressEngine(),
        renderer=renderer,
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )

    assert app.run(DownloadSpec("owner/repo", Path("out")), once=True) == 0
    assert observer.calls == 1
    assert len(renderer.snapshots) == 1


def test_prepared_plan_avoids_second_repository_call() -> None:
    repository = FakeRepository()
    spec = DownloadSpec("owner/repo", Path("out"), revision="requested")
    plan = DownloadPlan(spec, "requested", (ManifestFile("model.bin", 10),))
    app = WatchApplication(
        repository=repository,
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=RecordingRenderer(),
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )

    assert app.run(spec, plan=plan, once=True) == 0
    assert repository.calls == 0


def test_integrity_failure_returns_integrity_exit_code(tmp_path: Path) -> None:
    from hf_download_live_monitor.errors import ErrorCategory, exit_code_for

    content = b"wrong"
    (tmp_path / "model.bin").write_bytes(content)
    spec = DownloadSpec("owner/repo", tmp_path)
    plan = DownloadPlan(spec, "main", (ManifestFile("model.bin", len(content), "0" * 64),))

    class FinalObserver:
        def observe(self, spec: DownloadSpec, manifest: tuple[ManifestFile, ...], now: float):
            return (
                FileObservation("model.bin", len(content), len(content), len(content), None, now),
            )

    renderer = RecordingRenderer()
    app = WatchApplication(
        repository=FakeRepository(),
        observer=FinalObserver(),
        engine=ProgressEngine(),
        renderer=renderer,
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )

    assert app.run(spec, plan=plan, once=True) == exit_code_for(ErrorCategory.INTEGRITY)
    assert renderer.snapshots[-1].failed_files == 1


def test_once_renders_incomplete_final_snapshot_and_returns_integrity_exit() -> None:
    from hf_download_live_monitor.errors import ErrorCategory, exit_code_for

    class PartialObserver:
        def observe(
            self,
            spec: DownloadSpec,
            manifest: tuple[ManifestFile, ...],
            now: float,
        ) -> tuple[FileObservation, ...]:
            return (FileObservation("model.bin", 10, 5, None, 5, now),)

    renderer = RecordingRenderer()
    app = WatchApplication(
        repository=FakeRepository(),
        observer=PartialObserver(),
        engine=ProgressEngine(),
        renderer=renderer,
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )

    assert app.run(DownloadSpec("owner/repo", Path("out")), once=True) == exit_code_for(
        ErrorCategory.INTEGRITY
    )
    assert len(renderer.snapshots) == 1
    assert renderer.snapshots[-1].failed_files == 1
    assert renderer.snapshots[-1].files[0].state is FileState.FAILED


def test_resources_close_when_render_raises() -> None:
    class FailingRenderer(RecordingRenderer):
        def render(self, snapshot: ProgressSnapshot) -> None:
            raise RuntimeError("render failed")

    class ClosingEngine(ProgressEngine):
        closed = False

        def close(self) -> None:
            self.closed = True
            super().close()

    renderer = FailingRenderer()
    engine = ClosingEngine()
    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=engine,
        renderer=renderer,
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )

    import pytest

    with pytest.raises(RuntimeError, match="render failed"):
        app.run(DownloadSpec("owner/repo", Path("out")), once=True)
    assert renderer.closed
    assert engine.closed


def test_q_reconciles_final_snapshot_then_returns_cancelled_and_closes_controls() -> None:
    class Controls:
        closed = False

        def poll(self, state: DisplayState) -> DisplayState:
            return state.apply_key("q")

        def close(self) -> None:
            self.closed = True

    renderer = RecordingRenderer()
    controls = Controls()
    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=renderer,
        controls=controls,
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )
    assert app.run(DownloadSpec("owner/repo", Path("out"))) == exit_code_for(
        ErrorCategory.CANCELLED
    )
    assert len(renderer.snapshots) == 2
    assert controls.closed
    assert app.cancellation_requested


def test_q_integrity_failure_takes_precedence_over_cancelled(tmp_path: Path) -> None:
    class Controls:
        def poll(self, state: DisplayState) -> DisplayState:
            return state.apply_key("q")

        def close(self) -> None:
            pass

    content = b"wrong"
    (tmp_path / "model.bin").write_bytes(content)
    spec = DownloadSpec("owner/repo", tmp_path)
    plan = DownloadPlan(spec, "main", (ManifestFile("model.bin", len(content), "0" * 64),))
    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=RecordingRenderer(),
        controls=Controls(),
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )
    assert app.run(spec, plan=plan) == exit_code_for(ErrorCategory.INTEGRITY)


def test_controls_begin_from_requested_view_mode() -> None:
    seen: list[DisplayState] = []

    class Controls:
        def poll(self, state: DisplayState) -> DisplayState:
            seen.append(state)
            return state.apply_key("q")

        def close(self) -> None:
            pass

    app = WatchApplication(
        repository=FakeRepository(),
        observer=FakeObserver(),
        engine=ProgressEngine(),
        renderer=RecordingRenderer(),
        controls=Controls(),
        display_state=DisplayState(view_mode=ViewMode.DETAILED),
        clock=lambda: 5.0,
        sleeper=lambda _: None,
    )
    app.run(DownloadSpec("owner/repo", Path("out")))
    assert seen[0].view_mode is ViewMode.DETAILED
