"""Command-line interface for HF Download Live Monitor."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console

from hf_download_live_monitor.app import WatchApplication
from hf_download_live_monitor.attach import discover_downloads, select_download
from hf_download_live_monitor.engine import ProgressEngine
from hf_download_live_monitor.errors import exit_code_for
from hf_download_live_monitor.filesystem import FileSystemObserver
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

cli = typer.Typer(no_args_is_help=True, help="Monitor Hugging Face downloads.")


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
) -> None:
    """Attach to an active Hugging Face download."""
    if pid is not None and all_downloads:
        raise typer.BadParameter("--pid and --all cannot be used together")
    _validate_outputs(plain, json_output, jsonl)
    try:
        candidates = discover_downloads(system_process_provider())
        if all_downloads:
            if not once and len(candidates) > 1:
                raise MonitorError(
                    "all_requires_once",
                    "continuous --all display is not yet safe; use --all --once",
                )
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
    hf_executable: str = typer.Option("hf", "--hf-executable"),
) -> None:
    """Launch and monitor an official Hugging Face download."""
    _validate_outputs(plain, json_output, jsonl)
    spec = DownloadSpec(
        repo=repo,
        local_dir=local_dir.resolve(),
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
        )
        code = ManagedDownload(application).run(
            plan.spec, executable=hf_executable, manifest=plan.manifest
        )
    except MonitorError as exc:
        _exit_for_error(exc)
    except OSError as exc:
        _exit_for_error(MonitorError("launch_failed", str(exc)))
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
) -> None:
    """Watch an explicit Hugging Face local directory."""
    _validate_outputs(plain, json_output, jsonl)

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
        )
    except MonitorError as exc:
        _exit_for_error(exc)
    if code:
        raise typer.Exit(code=code)


def _validate_outputs(plain: bool, json_output: bool, jsonl: bool) -> None:
    if sum((plain, json_output, jsonl)) > 1:
        raise typer.BadParameter("--plain, --json, and --jsonl cannot be used together")


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
) -> int:
    application = _make_application(
        refresh=refresh,
        rate_window=rate_window,
        plain=plain,
        json_output=json_output,
        jsonl=jsonl,
        no_color=no_color,
        ascii_only=ascii_only,
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
) -> WatchApplication:
    if json_output:
        renderer = JsonRenderer()
    elif jsonl:
        renderer = JsonLinesRenderer()
    elif plain or not sys.stdout.isatty():
        renderer = PlainRenderer(ascii_only=ascii_only)
    else:
        renderer = RichRenderer(Console(no_color=no_color))
    return WatchApplication(
        repository=HubRepository(),
        observer=FileSystemObserver(),
        engine=ProgressEngine(rate_window=rate_window),
        renderer=renderer,
        refresh=refresh,
    )


def _exit_for_error(exc: MonitorError) -> NoReturn:
    typer.echo(f"Error [{exc.code}]: {exc.message}", err=True)
    raise typer.Exit(code=exit_code_for(exc.category)) from exc


def run() -> None:
    cli()
