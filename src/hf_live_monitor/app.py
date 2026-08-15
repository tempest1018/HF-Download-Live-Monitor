"""Watch-mode application orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from hf_live_monitor.engine import ProgressEngine
from hf_live_monitor.models import DownloadSpec, FileObservation, ManifestFile, MonitorError
from hf_live_monitor.renderers import Renderer


class Repository(Protocol):
    def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]: ...


class Observer(Protocol):
    def observe(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        now: float,
    ) -> tuple[FileObservation, ...]: ...


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

    def run(
        self,
        spec: DownloadSpec,
        *,
        once: bool = False,
        stop_when: Callable[[], bool] | None = None,
        handle_interrupt: bool = True,
    ) -> int:
        try:
            manifest = self._load_manifest(spec)
            while True:
                now = self._clock()
                observations = self._observer.observe(spec, manifest, now)
                snapshot = self._engine.update(spec, manifest, observations, now)
                self._renderer.render(snapshot)
                if once or (stop_when is not None and stop_when()):
                    return 0
                self._sleeper(self._refresh)
        except KeyboardInterrupt:
            if handle_interrupt:
                return 0
            raise
        finally:
            self._renderer.close()

    def _load_manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]:
        delays = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
        for delay in delays:
            try:
                return self._repository.manifest(spec)
            except MonitorError as exc:
                if not exc.recoverable:
                    raise
                self._sleeper(delay)
        return self._repository.manifest(spec)
