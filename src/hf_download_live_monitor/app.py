"""Watch-mode application orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from hf_download_live_monitor.controls import DisplayState
from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.models import (
    DownloadPlan,
    DownloadSpec,
    FileObservation,
    ManifestFile,
    MonitorError,
    ProgressSnapshot,
)
from hf_download_live_monitor.renderers import Renderer


class Repository(Protocol):
    def prepare(self, spec: DownloadSpec) -> DownloadPlan: ...

    def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]: ...


class Observer(Protocol):
    def observe(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        now: float,
    ) -> tuple[FileObservation, ...]: ...


class Controls(Protocol):
    def poll(self, state: DisplayState) -> DisplayState: ...

    def close(self) -> None: ...


class WatchApplication:
    def __init__(
        self,
        *,
        repository: Repository,
        observer: Observer,
        engine: ProgressEngine,
        renderer: Renderer,
        refresh: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        controls: Controls | None = None,
        display_state: DisplayState | None = None,
    ) -> None:
        if refresh <= 0:
            raise ValueError("refresh interval must be positive")
        self._repository = repository
        self._observer = observer
        self._engine = engine
        self._renderer = renderer
        self._refresh = refresh
        self._clock = clock
        self._sleeper = sleeper
        self._controls = controls
        self._display_state = display_state or DisplayState()

    def run(
        self,
        spec: DownloadSpec,
        *,
        manifest: tuple[ManifestFile, ...] | None = None,
        plan: DownloadPlan | None = None,
        once: bool = False,
        stop_when: Callable[[], bool] | None = None,
        handle_interrupt: bool = True,
    ) -> int:
        error_in_flight = False
        try:
            plan = plan or self._prepare_plan(spec, manifest)
            if once:
                snapshot = self._observe_and_render(plan, final=True)
                return self._exit_code(snapshot)
            while True:
                self._observe_and_render(plan, final=False)
                if self._controls is not None:
                    self._display_state = self._controls.poll(self._display_state)
                    update = getattr(self._renderer, "update_display_state", None)
                    if callable(update):
                        update(self._display_state)
                    if self._display_state.cancel_requested:
                        snapshot = self._observe_and_render(plan, final=True)
                        if snapshot.failed_files:
                            return self._exit_code(snapshot)
                        return exit_code_for(ErrorCategory.CANCELLED)
                if stop_when is not None and stop_when():
                    snapshot = self._observe_and_render(plan, final=True)
                    return self._exit_code(snapshot)
                self._sleeper(self._refresh)
        except KeyboardInterrupt:
            if handle_interrupt:
                return 0
            error_in_flight = True
            raise
        except BaseException:
            error_in_flight = True
            raise
        finally:
            close_error: BaseException | None = None
            try:
                self._renderer.close()
            except BaseException as exc:
                close_error = exc
            try:
                self._engine.close()
            except BaseException as exc:
                if close_error is None:
                    close_error = exc
            if self._controls is not None:
                try:
                    self._controls.close()
                except BaseException as exc:
                    if close_error is None:
                        close_error = exc
            if close_error is not None and not error_in_flight:
                raise close_error

    def _observe_and_render(self, plan: DownloadPlan, *, final: bool) -> ProgressSnapshot:
        now = self._clock()
        observations = self._observer.observe(plan.spec, plan.manifest, now)
        snapshot = self._engine.update(plan, observations, now=now, final=final)
        self._renderer.render(snapshot)
        return snapshot

    @staticmethod
    def _exit_code(snapshot: ProgressSnapshot) -> int:
        if snapshot.failed_files:
            return exit_code_for(ErrorCategory.INTEGRITY)
        return 0

    def _prepare_plan(
        self, spec: DownloadSpec, manifest: tuple[ManifestFile, ...] | None
    ) -> DownloadPlan:
        if manifest is not None:
            return DownloadPlan(spec, spec.revision, manifest)
        return self._load_plan(spec)

    def _load_plan(self, spec: DownloadSpec) -> DownloadPlan:
        delays = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
        for delay in delays:
            try:
                return self._repository.prepare(spec)
            except MonitorError as exc:
                if not exc.recoverable:
                    raise
                self._sleeper(delay)
        return self._repository.prepare(spec)
