"""Command-line interface for HF Download Live Monitor."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console

from hf_download_live_monitor.app import WatchApplication
from hf_download_live_monitor.attach import discover_downloads, select_download
from hf_download_live_monitor.controls import (
    DisplayState,
    KeyboardController,
    SupervisorDisplayState,
)
from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.errors import ErrorCategory, exit_code_for
from hf_download_live_monitor.filesystem import FileSystemObserver
from hf_download_live_monitor.history_cli import history_cli, make_history_recorder
from hf_download_live_monitor.layout import ViewMode
from hf_download_live_monitor.models import DownloadSpec, MonitorError, RepoType
from hf_download_live_monitor.preflight import validate_destination
from hf_download_live_monitor.processes import system_process_provider
from hf_download_live_monitor.renderers import (
    JsonLinesRenderer,
    JsonRenderer,
    PlainRenderer,
    RichRenderer,
)
from hf_download_live_monitor.repository import HubRepository
from hf_download_live_monitor.runner import ManagedDownload
from hf_download_live_monitor.security import redact_text
from hf_download_live_monitor.supervisor import DownloadSupervisor
from hf_download_live_monitor.supervisor_renderers import (
    SupervisorJsonLinesRenderer,
    SupervisorJsonRenderer,
    SupervisorPlainRenderer,
    SupervisorRichRenderer,
)

cli = typer.Typer(
    no_args_is_help=True,
    help="Monitor Hugging Face downloads.",
    rich_markup_mode=None,
)
cli.add_typer(history_cli, name="history")


@cli.callback()
def main() -> None:
    """Monitor Hugging Face downloads."""


@cli.command()
def attach(
    pid: int | None = typer.Option(None, "--pid", min=1),
    all_downloads: bool = typer.Option(False, "--all"),
    refresh: float = typer.Option(0.25, "--refresh", min=0.01),
    rate_window: float = typer.Option(2.0, "--rate-window", min=0.1),
    once: bool = typer.Option(False, "--once"),
    plain: bool = typer.Option(False, "--plain"),
    json_output: bool = typer.Option(False, "--json"),
    jsonl: bool = typer.Option(False, "--jsonl"),
    no_color: bool = typer.Option(False, "--no-color"),
    ascii_only: bool = typer.Option(False, "--ascii"),
    view: ViewMode = typer.Option(ViewMode.BALANCED, "--view"),
    reduced_motion: bool = typer.Option(False, "--reduced-motion"),
    discovery_refresh: float = typer.Option(1.0, "--discovery-refresh", min=0.01),
    retention: float = typer.Option(15.0, "--retention", min=0.0),
    max_sessions: int = typer.Option(32, "--max-sessions", min=1),
    record_history: bool = typer.Option(False, "--record-history"),
    no_record_history: bool = typer.Option(False, "--no-record-history"),
    include_identifiers: bool = typer.Option(
        False, "--include-identifiers", help="Opt in with --include-identifiers."
    ),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    """Attach to an active Hugging Face download."""
    if pid is not None and all_downloads:
        raise typer.BadParameter("--pid and --all cannot be used together")
    history_override = _history_override(record_history, no_record_history)
    _validate_outputs(plain, json_output, jsonl)
    try:
        if all_downloads and not once:
            code = _make_supervisor(
                refresh=refresh,
                rate_window=rate_window,
                discovery_refresh=discovery_refresh,
                retention=retention,
                max_sessions=max_sessions,
                plain=plain,
                json_output=json_output,
                jsonl=jsonl,
                no_color=no_color,
                ascii_only=ascii_only,
                view=view,
                reduced_motion=reduced_motion,
                record_history=history_override,
                include_identifiers=include_identifiers,
                history_path=history_path,
            ).run()
            if code:
                raise typer.Exit(code=code)
            return
        candidates = discover_downloads(system_process_provider())
        if all_downloads:
            selected = candidates
        else:
            selected = (select_download(candidates, pid=pid, interactive=sys.stdin.isatty()),)
        for candidate in selected:
            code = _watch_spec(
                candidate.spec,
                refresh=refresh,
                rate_window=rate_window,
                once=once,
                plain=plain,
                json_output=json_output,
                jsonl=jsonl,
                no_color=no_color,
                ascii_only=ascii_only,
                view=view,
                reduced_motion=reduced_motion,
                record_history=history_override,
                include_identifiers=include_identifiers,
                history_path=history_path,
                mode="attach",
            )
            if code:
                raise typer.Exit(code=code)
    except MonitorError as exc:
        _exit_for_error(exc)


@cli.command("run")
def run_download(
    repo: str,
    local_dir: Path = typer.Option(..., "--local-dir", file_okay=False),
    repo_type: RepoType = typer.Option(RepoType.MODEL, "--repo-type"),
    revision: str = typer.Option("main", "--revision"),
    filename: list[str] | None = typer.Option(None, "--filename"),
    include: list[str] | None = typer.Option(None, "--include"),
    exclude: list[str] | None = typer.Option(None, "--exclude"),
    refresh: float = typer.Option(0.25, "--refresh", min=0.01),
    rate_window: float = typer.Option(2.0, "--rate-window", min=0.1),
    plain: bool = typer.Option(False, "--plain"),
    json_output: bool = typer.Option(False, "--json"),
    jsonl: bool = typer.Option(False, "--jsonl"),
    no_color: bool = typer.Option(False, "--no-color"),
    ascii_only: bool = typer.Option(False, "--ascii"),
    view: ViewMode = typer.Option(ViewMode.BALANCED, "--view"),
    reduced_motion: bool = typer.Option(False, "--reduced-motion"),
    hf_executable: str = typer.Option("hf", "--hf-executable"),
    record_history: bool = typer.Option(False, "--record-history"),
    no_record_history: bool = typer.Option(False, "--no-record-history"),
    include_identifiers: bool = typer.Option(
        False, "--include-identifiers", help="Opt in with --include-identifiers."
    ),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    """Launch and monitor an official Hugging Face download."""
    _validate_outputs(plain, json_output, jsonl)
    history_override = _history_override(record_history, no_record_history)
    spec = DownloadSpec(
        repo=repo,
        local_dir=local_dir,
        repo_type=repo_type,
        revision=revision,
        filenames=tuple(filename or ()),
        includes=tuple(include or ()),
        excludes=tuple(exclude or ()),
    )
    try:
        plan = HubRepository().prepare(spec)
        validate_destination(plan)
        application = _make_application(
            refresh=refresh,
            rate_window=rate_window,
            plain=plain,
            json_output=json_output,
            jsonl=jsonl,
            no_color=no_color,
            ascii_only=ascii_only,
            view=view,
            reduced_motion=reduced_motion,
            record_history=history_override,
            include_identifiers=include_identifiers,
            history_path=history_path,
            mode="run",
        )
        code = ManagedDownload(application).run(plan.spec, executable=hf_executable, plan=plan)
    except MonitorError as exc:
        _exit_for_error(exc)
    except OSError as exc:
        _exit_for_error(
            MonitorError(
                "launch_failed",
                redact_text(str(exc)),
                category=ErrorCategory.DOWNLOADER,
            )
        )
    if code:
        raise typer.Exit(code=code)


@cli.command()
def watch(
    repo: str,
    local_dir: Path = typer.Option(..., "--local-dir", file_okay=False),
    repo_type: RepoType = typer.Option(RepoType.MODEL, "--repo-type"),
    revision: str = typer.Option("main", "--revision"),
    filename: list[str] | None = typer.Option(None, "--filename"),
    include: list[str] | None = typer.Option(None, "--include"),
    exclude: list[str] | None = typer.Option(None, "--exclude"),
    refresh: float = typer.Option(0.25, "--refresh", min=0.01),
    rate_window: float = typer.Option(2.0, "--rate-window", min=0.1),
    once: bool = typer.Option(False, "--once"),
    plain: bool = typer.Option(False, "--plain"),
    json_output: bool = typer.Option(False, "--json"),
    jsonl: bool = typer.Option(False, "--jsonl"),
    no_color: bool = typer.Option(False, "--no-color"),
    ascii_only: bool = typer.Option(False, "--ascii"),
    view: ViewMode = typer.Option(ViewMode.BALANCED, "--view"),
    reduced_motion: bool = typer.Option(False, "--reduced-motion"),
    record_history: bool = typer.Option(False, "--record-history"),
    no_record_history: bool = typer.Option(False, "--no-record-history"),
    include_identifiers: bool = typer.Option(
        False, "--include-identifiers", help="Opt in with --include-identifiers."
    ),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    """Watch an explicit Hugging Face local directory."""
    _validate_outputs(plain, json_output, jsonl)
    history_override = _history_override(record_history, no_record_history)

    spec = DownloadSpec(
        repo=repo,
        local_dir=local_dir,
        repo_type=repo_type,
        revision=revision,
        filenames=tuple(filename or ()),
        includes=tuple(include or ()),
        excludes=tuple(exclude or ()),
    )
    try:
        code = _watch_spec(
            spec,
            refresh=refresh,
            rate_window=rate_window,
            once=once,
            plain=plain,
            json_output=json_output,
            jsonl=jsonl,
            no_color=no_color,
            ascii_only=ascii_only,
            view=view,
            reduced_motion=reduced_motion,
            record_history=history_override,
            include_identifiers=include_identifiers,
            history_path=history_path,
            mode="watch",
        )
    except MonitorError as exc:
        _exit_for_error(exc)
    if code:
        raise typer.Exit(code=code)


def _validate_outputs(plain: bool, json_output: bool, jsonl: bool) -> None:
    if sum((plain, json_output, jsonl)) > 1:
        raise typer.BadParameter("--plain, --json, and --jsonl cannot be used together")


def _history_override(record: bool, no_record: bool) -> bool | None:
    if record and no_record:
        raise typer.BadParameter(
            "--record-history and --no-record-history cannot be used together"
        )
    if record:
        return True
    if no_record:
        return False
    return None


def _watch_spec(
    spec: DownloadSpec,
    *,
    refresh: float,
    rate_window: float,
    once: bool,
    plain: bool,
    json_output: bool,
    jsonl: bool,
    no_color: bool,
    ascii_only: bool,
    view: ViewMode,
    reduced_motion: bool,
    record_history: bool | None = None,
    include_identifiers: bool = False,
    history_path: Path | None = None,
    mode: str = "watch",
) -> int:
    application = _make_application(
        refresh=refresh,
        rate_window=rate_window,
        plain=plain,
        json_output=json_output,
        jsonl=jsonl,
        no_color=no_color,
        ascii_only=ascii_only,
        view=view,
        reduced_motion=reduced_motion,
        record_history=record_history,
        include_identifiers=include_identifiers,
        history_path=history_path,
        mode=mode,
    )
    return application.run(spec, once=once)


def _make_application(
    *,
    refresh: float,
    rate_window: float,
    plain: bool,
    json_output: bool,
    jsonl: bool,
    no_color: bool,
    ascii_only: bool,
    view: ViewMode,
    reduced_motion: bool,
    record_history: bool | None = None,
    include_identifiers: bool = False,
    history_path: Path | None = None,
    mode: str = "watch",
) -> WatchApplication:
    if json_output:
        renderer = JsonRenderer()
    elif jsonl:
        renderer = JsonLinesRenderer()
    elif plain or not sys.stdout.isatty():
        renderer = PlainRenderer(ascii_only=ascii_only)
    else:
        renderer = RichRenderer(
            Console(no_color=no_color),
            view_mode=view,
            ascii_only=ascii_only,
            reduced_motion=reduced_motion,
        )
    controls = KeyboardController() if isinstance(renderer, RichRenderer) else None
    history = make_history_recorder(
        record=record_history,
        include_identifiers=include_identifiers,
        history_path=history_path,
    )
    return WatchApplication(
        repository=HubRepository(),
        observer=FileSystemObserver(),
        engine=ProgressEngine(rate_window=rate_window),
        renderer=renderer,
        refresh=refresh,
        controls=controls,
        display_state=DisplayState(view_mode=view),
        history=history,
        mode=mode,
    )


def _make_supervisor(
    *,
    refresh: float,
    rate_window: float,
    discovery_refresh: float,
    retention: float,
    max_sessions: int,
    plain: bool,
    json_output: bool,
    jsonl: bool,
    no_color: bool,
    ascii_only: bool,
    view: ViewMode,
    reduced_motion: bool,
    record_history: bool | None = None,
    include_identifiers: bool = False,
    history_path: Path | None = None,
) -> DownloadSupervisor:
    state = SupervisorDisplayState(view_mode=view)
    if json_output:
        renderer = SupervisorJsonRenderer()
    elif jsonl:
        renderer = SupervisorJsonLinesRenderer()
    elif plain or not sys.stdout.isatty():
        renderer = SupervisorPlainRenderer()
    else:
        renderer = SupervisorRichRenderer(
            Console(no_color=no_color),
            display_state=state,
            ascii_only=ascii_only,
            reduced_motion=reduced_motion,
        )
    controls = KeyboardController() if isinstance(renderer, SupervisorRichRenderer) else None
    provider = system_process_provider()
    history = make_history_recorder(
        record=record_history,
        include_identifiers=include_identifiers,
        history_path=history_path,
    )
    return DownloadSupervisor(
        lambda: discover_downloads(provider),
        discovery_refresh=discovery_refresh,
        retention=retention,
        max_sessions=max_sessions,
        repository=HubRepository(),
        observer=FileSystemObserver(),
        engine_factory=lambda: ProgressEngine(rate_window=rate_window),
        refresh=refresh,
        renderer=renderer,
        controls=controls,
        display_state=state,
        history=history,
    )


def _exit_for_error(exc: MonitorError) -> NoReturn:
    typer.echo(f"Error [{exc.code}]: {exc.message}", err=True)
    raise typer.Exit(code=exit_code_for(exc.category)) from exc


def run() -> None:
    cli()
