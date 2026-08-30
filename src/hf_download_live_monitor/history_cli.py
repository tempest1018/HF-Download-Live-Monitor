"""User-controlled commands and policy resolution for private local history."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

import typer
from rich.console import Console

from hf_download_live_monitor.history_models import HistoryConfig, HistoryQuery
from hf_download_live_monitor.history_paths import resolve_history_paths
from hf_download_live_monitor.history_recorder import (
    HistoryRecorder,
    NullHistoryRecorder,
    SQLiteHistoryRecorder,
)
from hf_download_live_monitor.history_renderers import (
    history_record_to_dict,
    history_table,
    records_to_jsonl,
)
from hf_download_live_monitor.history_store import HistoryStore, inspect_history_health
from hf_download_live_monitor.models import MonitorError

history_cli = typer.Typer(
    no_args_is_help=True,
    help="Control private local history.",
    rich_markup_mode=None,
)


def make_history_recorder(
    *,
    record: bool | None,
    include_identifiers: bool,
    history_path: Path | None,
) -> HistoryRecorder:
    paths = resolve_history_paths(override=history_path)
    store = HistoryStore.open(paths, create=False)
    config = HistoryConfig.defaults() if store is None else store.load_config()
    enabled = config.enabled if record is None else record
    if not enabled:
        if store is not None:
            store.close()
        if include_identifiers:
            raise typer.BadParameter("--include-identifiers requires history recording")
        return NullHistoryRecorder()
    if store is None:
        store = HistoryStore.open(paths, create=True)
    if store is None:
        return NullHistoryRecorder()
    return SQLiteHistoryRecorder(store, include_identifiers=include_identifiers)


def _store(path: Path | None, *, create: bool) -> HistoryStore | None:
    return HistoryStore.open(resolve_history_paths(override=path), create=create)


@history_cli.command("status")
def status(
    history_path: Path | None = typer.Option(None, "--history-path"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    paths = resolve_history_paths(override=history_path)
    health = inspect_history_health(paths)
    store = HistoryStore.open(paths, create=False) if health.value == "healthy" else None
    config = HistoryConfig.defaults() if store is None else store.load_config()
    count = 0 if store is None else len(store.list_records(HistoryQuery(limit=1_000)))
    if store is not None:
        store.close()
    payload = {
        "schema_version": 1,
        "kind": "history_status",
        "health": health.value,
        "enabled": config.enabled,
        "retention_days": config.retention_days,
        "max_size_bytes": config.max_size_bytes,
        "record_count": count,
        "database": str(paths.database),
    }
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
    else:
        typer.echo(
            f"History: {health.value}; enabled={str(config.enabled).lower()}; "
            f"records={count}; retention={config.retention_days or 'unlimited'} days"
        )


@history_cli.command("enable")
def enable(history_path: Path | None = typer.Option(None, "--history-path")) -> None:
    store = _store(history_path, create=True)
    if store is None:
        raise typer.Exit(code=1)
    config = store.load_config()
    store.save_config(HistoryConfig(True, config.retention_days, config.max_size_bytes))
    store.close()
    typer.echo("Local history enabled. Readable identifiers remain disabled by default.")


@history_cli.command("disable")
def disable(history_path: Path | None = typer.Option(None, "--history-path")) -> None:
    store = _store(history_path, create=False)
    if store is None:
        typer.echo("Local history is already disabled.")
        return
    config = store.load_config()
    store.save_config(HistoryConfig(False, config.retention_days, config.max_size_bytes))
    store.close()
    typer.echo("Local history disabled; existing user-owned records were retained.")


@history_cli.command("configure")
def configure(
    retention: str = typer.Option("30", "--retention-days"),
    max_size_mib: int = typer.Option(64, "--max-size-mib", min=1),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    try:
        retention_days = None if retention.lower() == "unlimited" else int(retention)
        config = HistoryConfig(
            enabled=True,
            retention_days=retention_days,
            max_size_bytes=max_size_mib * 1024 * 1024,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--retention-days") from exc
    store = _store(history_path, create=True)
    if store is None:
        raise typer.Exit(code=1)
    store.save_config(config)
    store.close()
    typer.echo("Local history policy updated and enabled.")


@history_cli.command("list")
def list_history(
    limit: int = typer.Option(100, "--limit", min=1, max=1_000),
    json_output: bool = typer.Option(False, "--json"),
    jsonl: bool = typer.Option(False, "--jsonl"),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    if json_output and jsonl:
        raise typer.BadParameter("--json and --jsonl cannot be used together")
    store = _store(history_path, create=False)
    records = () if store is None else store.list_records(HistoryQuery(limit=limit))
    if store is not None:
        store.close()
    if jsonl:
        typer.echo(records_to_jsonl(records))
    elif json_output:
        typer.echo(json.dumps([history_record_to_dict(item) for item in records], sort_keys=True))
    else:
        Console().print(history_table(records))


@history_cli.command("show")
def show(
    session_id: str,
    json_output: bool = typer.Option(False, "--json"),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    store = _store(history_path, create=False)
    record = None if store is None else store.get_record(session_id)
    if store is not None:
        store.close()
    if record is None:
        typer.echo("History record was not found.", err=True)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(json.dumps(history_record_to_dict(record), sort_keys=True))
    else:
        Console().print(history_table((record,)))


@history_cli.command("export")
def export_history(
    jsonl: bool = typer.Option(False, "--jsonl"),
    include_identifiers: bool = typer.Option(False, "--include-identifiers"),
    yes: bool = typer.Option(False, "--yes"),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    if include_identifiers:
        _confirm("Export readable repository names and local paths?", yes=yes)
    store = _store(history_path, create=False)
    records = () if store is None else store.list_records(HistoryQuery(limit=1_000))
    if store is not None:
        store.close()
    if jsonl:
        typer.echo(records_to_jsonl(records, include_identifiers=include_identifiers))
    else:
        typer.echo(
            json.dumps(
                [
                    history_record_to_dict(item, include_identifiers=include_identifiers)
                    for item in records
                ],
                sort_keys=True,
            )
        )


@history_cli.command("delete")
def delete(
    session_id: str,
    yes: bool = typer.Option(False, "--yes"),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    _confirm(f"Delete local history record {session_id}?", yes=yes)
    store = _store(history_path, create=False)
    removed = store is not None and store.delete(session_id)
    if store is not None:
        store.close()
    typer.echo(f"Removed {1 if removed else 0} history record(s).")


@history_cli.command("clear")
def clear(
    before: str = typer.Option(..., "--before", help="UTC date in YYYY-MM-DD form."),
    yes: bool = typer.Option(False, "--yes"),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    try:
        before_date = date.fromisoformat(before)
    except ValueError as exc:
        raise typer.BadParameter("must use YYYY-MM-DD", param_hint="--before") from exc
    _confirm(f"Delete local history before {before_date.isoformat()} UTC?", yes=yes)
    cutoff = datetime.combine(before_date, time.min, tzinfo=timezone.utc).timestamp()
    store = _store(history_path, create=False)
    removed = 0 if store is None else store.clear_before(cutoff)
    if store is not None:
        store.close()
    typer.echo(f"Removed {removed} history record(s).")


@history_cli.command("purge")
def purge(
    yes: bool = typer.Option(False, "--yes"),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    _confirm("Permanently remove all local history data?", yes=yes)
    store = _store(history_path, create=False)
    removed = 0 if store is None else store.purge()
    typer.echo(f"Removed {removed} local history file(s). History is disabled.")


@history_cli.command("vacuum")
def vacuum(history_path: Path | None = typer.Option(None, "--history-path")) -> None:
    store = _store(history_path, create=False)
    if store is not None:
        store.vacuum()
        store.close()
    typer.echo("Local history compaction completed.")


@history_cli.command("recover")
def recover(
    output: Path = typer.Option(..., "--output"),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    store = _store(history_path, create=False)
    if store is None:
        typer.echo("No local history database exists.", err=True)
        raise typer.Exit(code=1)
    store.recover(output)
    store.close()
    typer.echo("Created and validated a separate recovery candidate.")


@history_cli.command("reset")
def reset(
    preserve_corrupt: bool = typer.Option(False, "--preserve-corrupt"),
    yes: bool = typer.Option(False, "--yes"),
    history_path: Path | None = typer.Option(None, "--history-path"),
) -> None:
    if not preserve_corrupt:
        raise typer.BadParameter("reset requires --preserve-corrupt")
    _confirm("Preserve corrupt files and create a clean local store?", yes=yes)
    paths = resolve_history_paths(override=history_path)
    preserved, store = HistoryStore.reset_preserving_corrupt(paths)
    store.close()
    typer.echo(f"Preserved corrupt history as {preserved.name}; created a clean store.")


def _confirm(message: str, *, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise typer.BadParameter("non-interactive destructive operations require --yes")
    typer.confirm(message, abort=True)


def map_history_error(exc: MonitorError) -> None:
    typer.echo(f"Error [{exc.code}]: {exc.message}", err=True)
    raise typer.Exit(code=1) from exc
