"""Discovery and selection of running Hugging Face downloads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hf_download_live_monitor.hf_command import parse_download_process
from hf_download_live_monitor.models import DownloadSpec, MonitorError
from hf_download_live_monitor.processes import ProcessIdentity, ProcessProvider


@dataclass(frozen=True, slots=True, order=True)
class SessionKey:
    repo_type: str
    repo: str
    local_dir: str
    revision: str
    process: ProcessIdentity


@dataclass(frozen=True, slots=True)
class DownloadCandidate:
    process: ProcessIdentity
    spec: DownloadSpec

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def key(self) -> SessionKey:
        return SessionKey(
            self.spec.repo_type.value,
            self.spec.repo,
            str(self.spec.local_dir.resolve(strict=False)),
            self.spec.revision,
            self.process,
        )


def discover_downloads(provider: ProcessProvider) -> tuple[DownloadCandidate, ...]:
    candidates: list[DownloadCandidate] = []
    for process in provider.discover():
        spec = parse_download_process(process)
        if spec is not None:
            candidates.append(DownloadCandidate(process.identity, spec))
    return tuple(sorted(candidates, key=lambda item: item.key))


def select_download(
    candidates: tuple[DownloadCandidate, ...],
    *,
    pid: int | None = None,
    interactive: bool = False,
    input_fn: Callable[[str], str] = input,
) -> DownloadCandidate:
    if pid is not None:
        for candidate in candidates:
            if candidate.pid == pid:
                return candidate
        raise MonitorError("pid_not_found", f"no supported Hugging Face download has PID {pid}")
    if not candidates:
        raise MonitorError(
            "no_download_found", "no active supported `hf download` process was found"
        )
    if len(candidates) == 1:
        return candidates[0]
    if not interactive:
        summary = ", ".join(str(item.pid) for item in candidates)
        raise MonitorError(
            "multiple_downloads",
            f"multiple downloads found (PIDs {summary}); select one with --pid",
        )
    choices = "\n".join(
        f"{index}. PID {item.pid}: {item.spec.repo} -> {item.spec.local_dir}"
        for index, item in enumerate(candidates, start=1)
    )
    try:
        selected = int(input_fn(f"{choices}\nSelect a download: "))
        return candidates[selected - 1]
    except (ValueError, IndexError) as exc:
        raise MonitorError("invalid_selection", "invalid download selection") from exc
