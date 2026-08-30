"""Transactional SQLite persistence for private local history."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.history_models import (
    HistoryCheckpoint,
    HistoryConfig,
    HistoryDiagnostic,
    HistoryHealth,
    HistoryOutcome,
    HistoryQuery,
    HistoryRecord,
)
from hf_download_live_monitor.history_paths import (
    HistoryPaths,
    ensure_private_history_directory,
    validate_history_paths,
)
from hf_download_live_monitor.models import MonitorError, RepoType

SCHEMA_VERSION = 1
_KEY_BYTES = 32
_BUSY_TIMEOUT_MS = 100
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        "CREATE INDEX IF NOT EXISTS sessions_outcome_idx ON sessions(outcome, updated_at_utc DESC)",
    ),
}
_SCHEMA = """
CREATE TABLE metadata(schema_version INTEGER NOT NULL, created_at_utc REAL NOT NULL);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sessions(
  session_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  repository_hmac TEXT NOT NULL,
  destination_hmac TEXT NOT NULL,
  repository_label TEXT NOT NULL,
  destination_label TEXT NOT NULL,
  repository_identifier TEXT,
  destination_identifier TEXT,
  repo_type TEXT NOT NULL,
  revision_kind TEXT NOT NULL,
  started_at_utc REAL NOT NULL,
  updated_at_utc REAL NOT NULL,
  ended_at_utc REAL,
  outcome TEXT,
  expected_bytes INTEGER NOT NULL,
  downloaded_bytes INTEGER NOT NULL,
  average_rate REAL NOT NULL,
  peak_rate REAL NOT NULL,
  waiting_seconds REAL NOT NULL,
  longest_wait_seconds REAL NOT NULL,
  verified_files INTEGER NOT NULL,
  unverified_files INTEGER NOT NULL,
  failed_files INTEGER NOT NULL,
  interrupted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE diagnostics(
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
  observed_at_utc REAL NOT NULL,
  category TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  recoverable INTEGER NOT NULL
);
CREATE INDEX sessions_updated_idx ON sessions(updated_at_utc DESC, session_id);
CREATE INDEX sessions_outcome_idx ON sessions(outcome, updated_at_utc DESC);
"""


class HistoryStore:
    """Own a validated SQLite history database and its local pseudonym key."""

    def __init__(
        self,
        paths: HistoryPaths,
        connection: sqlite3.Connection,
        key: bytes,
    ) -> None:
        self.paths = paths
        self._connection = connection
        self._key = key
        self._closed = False

    @classmethod
    def open(cls, paths: HistoryPaths, *, create: bool) -> HistoryStore | None:
        validate_history_paths(paths)
        if not paths.database.exists() and not create:
            return None
        if create:
            ensure_private_history_directory(paths)
        connection = cls._connect(paths.database, create=create)
        try:
            cls._configure(connection)
            cls._initialize_or_validate(connection, create=create)
            key = cls._load_or_create_key(paths.pseudonym_key, create=create)
        except BaseException:
            connection.close()
            raise
        return cls(paths, connection, key)

    @staticmethod
    def _connect(database: Path, *, create: bool) -> sqlite3.Connection:
        try:
            if create:
                connection = sqlite3.connect(database, timeout=_BUSY_TIMEOUT_MS / 1000)
            else:
                uri = database.resolve().as_uri() + "?mode=rw"
                connection = sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_MS / 1000)
        except sqlite3.Error as exc:
            raise _store_error("history_unavailable", "history database is unavailable") from exc
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode = WAL")

    @staticmethod
    def _initialize_or_validate(connection: sqlite3.Connection, *, create: bool) -> None:
        has_metadata = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone()
        if has_metadata is None:
            if not create:
                raise _store_error("history_schema_invalid", "history schema is missing")
            with connection:
                connection.executescript(_SCHEMA)
                connection.execute(
                    "INSERT INTO metadata(schema_version, created_at_utc) VALUES (?, ?)",
                    (SCHEMA_VERSION, time.time()),
                )
                _write_config(connection, HistoryConfig.defaults())
            return
        row = connection.execute("SELECT schema_version FROM metadata").fetchone()
        if row is None:
            raise _store_error("history_schema_invalid", "history schema metadata is missing")
        version = int(row[0])
        if version > SCHEMA_VERSION:
            raise _store_error(
                "history_schema_newer", "history database uses a newer unsupported schema"
            )
        if version < SCHEMA_VERSION:
            HistoryStore._migrate(connection, version)

    @staticmethod
    def _migrate(connection: sqlite3.Connection, version: int) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for target in range(version + 1, SCHEMA_VERSION + 1):
                statements = MIGRATIONS.get(target)
                if statements is None:
                    raise sqlite3.DatabaseError("missing migration")
                for statement in statements:
                    connection.execute(statement)
                connection.execute("UPDATE metadata SET schema_version = ?", (target,))
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise _store_error("history_migration_failed", "history migration failed") from exc

    @staticmethod
    def _load_or_create_key(path: Path, *, create: bool) -> bytes:
        if path.is_symlink():
            raise _store_error("history_path_invalid", "history key must not be a symbolic link")
        if not path.exists():
            if not create:
                raise _store_error("history_key_missing", "history pseudonym key is missing")
            key = secrets.token_bytes(_KEY_BYTES)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            descriptor = os.open(path, flags, 0o600)
            try:
                os.write(descriptor, key)
            finally:
                os.close(descriptor)
            if os.name != "nt":
                path.chmod(0o600)
        key = path.read_bytes()
        if len(key) != _KEY_BYTES:
            raise _store_error("history_key_invalid", "history pseudonym key is invalid")
        return key

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def schema_version(self) -> int:
        row = self._connection.execute("SELECT schema_version FROM metadata").fetchone()
        if row is None:
            raise _store_error("history_schema_invalid", "history schema metadata is missing")
        return int(row[0])

    def load_config(self) -> HistoryConfig:
        values = {
            str(row["key"]): str(row["value"])
            for row in self._connection.execute("SELECT key, value FROM settings")
        }
        try:
            retention = values.get("retention_days", "30")
            return HistoryConfig(
                enabled=values.get("enabled", "false") == "true",
                retention_days=None if retention == "unlimited" else int(retention),
                max_size_bytes=int(values.get("max_size_bytes", str(64 * 1024 * 1024))),
            )
        except (ValueError, TypeError) as exc:
            raise _store_error(
                "history_config_invalid", "history configuration is invalid"
            ) from exc

    def save_config(self, config: HistoryConfig) -> None:
        with self._transaction():
            _write_config(self._connection, config)

    def pseudonymize(self, value: str, *, label: str) -> tuple[str, str]:
        digest = hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).hexdigest()
        return digest, f"{label}-{digest[:8]}"

    def checkpoint(self, checkpoint: HistoryCheckpoint) -> None:
        values = _checkpoint_values(checkpoint)
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO sessions (
                  session_id, mode, repository_hmac, destination_hmac,
                  repository_label, destination_label, repository_identifier,
                  destination_identifier, repo_type, revision_kind, started_at_utc,
                  updated_at_utc, ended_at_utc, outcome, expected_bytes,
                  downloaded_bytes, average_rate, peak_rate, waiting_seconds,
                  longest_wait_seconds, verified_files, unverified_files, failed_files,
                  interrupted
                ) VALUES (
                  ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                ON CONFLICT(session_id) DO UPDATE SET
                  updated_at_utc=excluded.updated_at_utc,
                  ended_at_utc=excluded.ended_at_utc,
                  outcome=excluded.outcome,
                  expected_bytes=excluded.expected_bytes,
                  downloaded_bytes=excluded.downloaded_bytes,
                  average_rate=excluded.average_rate,
                  peak_rate=excluded.peak_rate,
                  waiting_seconds=excluded.waiting_seconds,
                  longest_wait_seconds=excluded.longest_wait_seconds,
                  verified_files=excluded.verified_files,
                  unverified_files=excluded.unverified_files,
                  failed_files=excluded.failed_files,
                  interrupted=excluded.interrupted
                """,
                values,
            )

    def add_diagnostic(self, session_id: str, diagnostic: HistoryDiagnostic) -> None:
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO diagnostics(
                  session_id, observed_at_utc, category, code, message, recoverable
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    diagnostic.observed_at_utc,
                    diagnostic.category,
                    diagnostic.code,
                    diagnostic.message,
                    int(diagnostic.recoverable),
                ),
            )

    def finalize(self, checkpoint: HistoryCheckpoint) -> None:
        if checkpoint.outcome is None or checkpoint.ended_at_utc is None:
            raise ValueError("final history checkpoint requires outcome and end time")
        self.checkpoint(checkpoint)

    def get_record(self, session_id: str) -> HistoryRecord | None:
        row = self._connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return None if row is None else self._record_from_row(row)

    def list_records(self, query: HistoryQuery) -> tuple[HistoryRecord, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if query.outcomes:
            placeholders = ",".join("?" for _ in query.outcomes)
            clauses.append(f"outcome IN ({placeholders})")
            parameters.extend(item.value for item in query.outcomes)
        if query.since_utc is not None:
            clauses.append("updated_at_utc >= ?")
            parameters.append(query.since_utc)
        if query.before_utc is not None:
            clauses.append("updated_at_utc < ?")
            parameters.append(query.before_utc)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        parameters.append(query.limit)
        rows = self._connection.execute(
            f"SELECT * FROM sessions{where} ORDER BY updated_at_utc DESC, session_id LIMIT ?",
            parameters,
        )
        return tuple(self._record_from_row(row) for row in rows)

    def _record_from_row(self, row: sqlite3.Row) -> HistoryRecord:
        diagnostics = tuple(
            HistoryDiagnostic(
                observed_at_utc=float(item["observed_at_utc"]),
                category=str(item["category"]),
                code=str(item["code"]),
                message=str(item["message"]),
                recoverable=bool(item["recoverable"]),
            )
            for item in self._connection.execute(
                """
                SELECT observed_at_utc, category, code, message, recoverable
                FROM diagnostics WHERE session_id = ?
                ORDER BY observed_at_utc, id LIMIT 100
                """,
                (str(row["session_id"]),),
            )
        )
        return HistoryRecord(_row_to_checkpoint(row), diagnostics)

    def delete(self, session_id: str) -> bool:
        with self._transaction():
            cursor = self._connection.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
        return cursor.rowcount == 1

    def clear_before(self, cutoff_utc: float) -> int:
        if cutoff_utc < 0:
            raise ValueError("history cutoff must be non-negative")
        with self._transaction():
            cursor = self._connection.execute(
                "DELETE FROM sessions WHERE updated_at_utc < ?", (cutoff_utc,)
            )
        return cursor.rowcount

    def mark_stale_interrupted(self, *, now_utc: float) -> int:
        if now_utc < 0:
            raise ValueError("history timestamp must be non-negative")
        with self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE sessions
                SET outcome = ?, interrupted = 1, ended_at_utc = ?, updated_at_utc = ?
                WHERE outcome IS NULL
                """,
                (HistoryOutcome.INTERRUPTED.value, now_utc, now_utc),
            )
        return cursor.rowcount

    def enforce_limits(self, *, now_utc: float) -> int:
        config = self.load_config()
        removed = 0
        if config.retention_days is not None:
            cutoff = now_utc - config.retention_days * 86_400
            with self._transaction():
                cursor = self._connection.execute(
                    "DELETE FROM sessions WHERE outcome IS NOT NULL AND updated_at_utc < ?",
                    (cutoff,),
                )
            removed += cursor.rowcount
        try:
            self._connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            return removed
        while _database_size(self.paths) > config.max_size_bytes:
            with self._transaction():
                cursor = self._connection.execute(
                    """
                    DELETE FROM sessions WHERE session_id = (
                      SELECT session_id FROM sessions
                      WHERE outcome IS NOT NULL
                      ORDER BY updated_at_utc, session_id LIMIT 1
                    )
                    """
                )
            if cursor.rowcount == 0:
                raise _store_error(
                    "history_capacity_reached",
                    "history size cap cannot be met without removing active data",
                )
            removed += cursor.rowcount
        return removed

    def health(self) -> HistoryHealth:
        try:
            row = self._connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError:
            return HistoryHealth.CORRUPT
        return (
            HistoryHealth.HEALTHY
            if row is not None and str(row[0]).lower() == "ok"
            else HistoryHealth.CORRUPT
        )

    def vacuum(self) -> None:
        try:
            self._connection.execute("VACUUM")
        except sqlite3.Error as exc:
            raise _store_error("history_vacuum_failed", "history vacuum failed") from exc

    def recover(self, output: Path) -> None:
        if not output.is_absolute():
            raise _store_error("history_recovery_invalid", "recovery output must be absolute")
        if output.exists():
            raise _store_error("history_recovery_invalid", "recovery output already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        destination: sqlite3.Connection | None = None
        try:
            destination = sqlite3.connect(output)
            self._connection.backup(destination)
            row = destination.execute("PRAGMA quick_check").fetchone()
            if row is None or str(row[0]).lower() != "ok":
                raise sqlite3.DatabaseError("recovery check failed")
        except (sqlite3.Error, OSError) as exc:
            if destination is not None:
                destination.close()
                destination = None
            if output.exists():
                output.unlink()
            raise _store_error("history_recovery_failed", "history recovery failed") from exc
        finally:
            if destination is not None:
                destination.close()
        if os.name != "nt":
            output.chmod(0o600)

    @classmethod
    def reset_preserving_corrupt(
        cls, paths: HistoryPaths, *, now_utc: float | None = None
    ) -> tuple[Path, HistoryStore]:
        validate_history_paths(paths)
        if inspect_history_health(paths) is not HistoryHealth.CORRUPT:
            raise _store_error("history_reset_refused", "history reset requires a corrupt database")
        stamp = time.strftime(
            "%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now_utc is None else now_utc)
        )
        preserved = paths.database.with_name(f"{paths.database.name}.corrupt-{stamp}")
        moves: list[tuple[Path, Path]] = []
        sources = (
            paths.database,
            Path(f"{paths.database}-wal"),
            Path(f"{paths.database}-shm"),
        )
        for source in sources:
            if source.exists():
                target = source.with_name(f"{source.name}.corrupt-{stamp}")
                if target.exists():
                    raise _store_error(
                        "history_reset_refused", "preserved history target already exists"
                    )
                moves.append((source, target))
        for source, target in moves:
            source.replace(target)
        replacement = cls.open(paths, create=True)
        if replacement is None:  # pragma: no cover - create=True guarantees a store
            raise _store_error("history_reset_failed", "history reset failed")
        config = replacement.load_config()
        replacement.save_config(HistoryConfig(True, config.retention_days, config.max_size_bytes))
        return preserved, replacement

    def purge(self) -> int:
        self.close()
        removed = 0
        for path in _managed_files(self.paths):
            if path.exists() and not path.is_symlink():
                path.unlink()
                removed += 1
        if not self.paths.custom_database:
            with suppress(OSError):
                self.paths.directory.rmdir()
        return removed

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        if self._closed:
            raise _store_error("history_unavailable", "history database is closed")
        try:
            with self._connection:
                yield
        except sqlite3.Error as exc:
            raise _store_error("history_write_failed", "history database write failed") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._connection.close()


def _write_config(connection: sqlite3.Connection, config: HistoryConfig) -> None:
    values = {
        "enabled": "true" if config.enabled else "false",
        "retention_days": (
            "unlimited" if config.retention_days is None else str(config.retention_days)
        ),
        "max_size_bytes": str(config.max_size_bytes),
    }
    connection.executemany(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        tuple(values.items()),
    )


def _checkpoint_values(checkpoint: HistoryCheckpoint) -> tuple[object, ...]:
    return (
        checkpoint.session_id,
        checkpoint.mode,
        checkpoint.repository_hmac,
        checkpoint.destination_hmac,
        checkpoint.repository_label,
        checkpoint.destination_label,
        checkpoint.repository_identifier,
        checkpoint.destination_identifier,
        checkpoint.repo_type.value,
        checkpoint.revision_kind,
        checkpoint.started_at_utc,
        checkpoint.updated_at_utc,
        checkpoint.ended_at_utc,
        None if checkpoint.outcome is None else checkpoint.outcome.value,
        checkpoint.expected_bytes,
        checkpoint.downloaded_bytes,
        checkpoint.average_rate,
        checkpoint.peak_rate,
        checkpoint.waiting_seconds,
        checkpoint.longest_wait_seconds,
        checkpoint.verified_files,
        checkpoint.unverified_files,
        checkpoint.failed_files,
        1 if checkpoint.outcome is HistoryOutcome.INTERRUPTED else 0,
    )


def _row_to_checkpoint(row: sqlite3.Row) -> HistoryCheckpoint:
    outcome_value = row["outcome"]
    return HistoryCheckpoint(
        session_id=str(row["session_id"]),
        mode=str(row["mode"]),
        repository_hmac=str(row["repository_hmac"]),
        destination_hmac=str(row["destination_hmac"]),
        repository_label=str(row["repository_label"]),
        destination_label=str(row["destination_label"]),
        repository_identifier=row["repository_identifier"],
        destination_identifier=row["destination_identifier"],
        repo_type=RepoType(str(row["repo_type"])),
        revision_kind=str(row["revision_kind"]),
        started_at_utc=float(row["started_at_utc"]),
        updated_at_utc=float(row["updated_at_utc"]),
        ended_at_utc=None if row["ended_at_utc"] is None else float(row["ended_at_utc"]),
        outcome=None if outcome_value is None else HistoryOutcome(str(outcome_value)),
        expected_bytes=int(row["expected_bytes"]),
        downloaded_bytes=int(row["downloaded_bytes"]),
        average_rate=float(row["average_rate"]),
        peak_rate=float(row["peak_rate"]),
        waiting_seconds=float(row["waiting_seconds"]),
        longest_wait_seconds=float(row["longest_wait_seconds"]),
        verified_files=int(row["verified_files"]),
        unverified_files=int(row["unverified_files"]),
        failed_files=int(row["failed_files"]),
    )


def _store_error(code: str, message: str) -> MonitorError:
    return MonitorError(code, message, category=ErrorCategory.MONITOR)


def inspect_history_health(paths: HistoryPaths) -> HistoryHealth:
    validate_history_paths(paths)
    if not paths.database.exists():
        return HistoryHealth.NEVER_ENABLED
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            paths.database.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=_BUSY_TIMEOUT_MS / 1000,
        )
        row = connection.execute("PRAGMA quick_check").fetchone()
        return (
            HistoryHealth.HEALTHY
            if row is not None and str(row[0]).lower() == "ok"
            else HistoryHealth.CORRUPT
        )
    except sqlite3.OperationalError:
        return HistoryHealth.LOCKED
    except sqlite3.DatabaseError:
        return HistoryHealth.CORRUPT
    except OSError:
        return HistoryHealth.UNAVAILABLE
    finally:
        if connection is not None:
            connection.close()


def _managed_files(paths: HistoryPaths) -> tuple[Path, ...]:
    return (
        paths.database,
        Path(str(paths.database) + "-wal"),
        Path(str(paths.database) + "-shm"),
        paths.pseudonym_key,
    )


def _database_size(paths: HistoryPaths) -> int:
    return sum(path.stat().st_size for path in _managed_files(paths) if path.exists())
