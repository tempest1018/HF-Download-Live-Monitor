"""Black-box acceptance runner for published HF Download Live Monitor assets."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

REVISION = "a" * 40
DEFAULT_CONTENT = b'{"model_type":"acceptance"}\n' * 4096
STABLE_TAG = re.compile(r"v\d+\.\d+\.\d+")


class AcceptanceError(RuntimeError):
    """A published artifact did not satisfy the acceptance contract."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    arguments: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    schema_version: int
    tag: str
    asset: str
    operating_system: str
    architecture: str
    checksum_status: str
    commands: tuple[CommandResult, ...]
    outcome: str


class HubFixture:
    """A minimal, deterministic Hugging Face-compatible localhost endpoint."""

    def __init__(
        self,
        *,
        content: bytes = DEFAULT_CONTENT,
        chunk_size: int = 1024,
        delay: float = 0.001,
    ) -> None:
        if not content or chunk_size <= 0 or delay < 0:
            raise ValueError("fixture content and timing values must be valid")
        self.content = content
        self.chunk_size = chunk_size
        self.delay = delay
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def endpoint(self) -> str:
        if self._server is None:
            raise RuntimeError("fixture is not running")
        address = self._server.server_address
        host, port = str(address[0]), int(address[1])
        return f"http://{host}:{port}"

    def __enter__(self) -> HubFixture:
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self) -> None:
                fixture._handle(self, include_body=False)

            def do_GET(self) -> None:
                fixture._handle(self, include_body=True)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def _handle(self, handler: BaseHTTPRequestHandler, *, include_body: bool) -> None:
        metadata_path = f"/api/models/acceptance/tiny/revision/{REVISION}"
        payload_path = f"/acceptance/tiny/resolve/{REVISION}/config.json"
        request = urlsplit(handler.path)
        query = parse_qs(request.query, keep_blank_values=True)
        digest = hashlib.sha256(self.content).hexdigest()
        if request.path == metadata_path and set(query) <= {"blobs"}:
            body = json.dumps(
                {
                    "id": "acceptance/tiny",
                    "sha": REVISION,
                    "siblings": [
                        {
                            "rfilename": "config.json",
                            "size": len(self.content),
                            "lfs": {
                                "sha256": digest,
                                "size": len(self.content),
                                "pointerSize": 130,
                            },
                        }
                    ],
                }
            ).encode()
            handler.send_response(200)
            handler.send_header("content-type", "application/json")
            handler.send_header("content-length", str(len(body)))
            handler.end_headers()
            if include_body:
                handler.wfile.write(body)
            return
        if request.path != payload_path or not set(query) <= {"download"}:
            handler.send_error(404)
            return
        handler.send_response(200)
        handler.send_header("x-repo-commit", REVISION)
        handler.send_header("etag", f'"{digest}"')
        handler.send_header("content-length", str(len(self.content)))
        handler.end_headers()
        if include_body:
            for offset in range(0, len(self.content), self.chunk_size):
                handler.wfile.write(self.content[offset : offset + self.chunk_size])
                handler.wfile.flush()
                if self.delay:
                    time.sleep(self.delay)


def run_acceptance(
    *,
    monitor: Path,
    hf_executable: Path,
    tag: str,
    asset: str,
    report: Path,
    checkout_root: Path,
    timeout: float = 60.0,
) -> AcceptanceResult:
    """Exercise a published executable and emit a compact, redacted report."""
    _validate_inputs(monitor, hf_executable, tag, asset, report, checkout_root)
    commands: list[CommandResult] = []
    try:
        for arguments in (
            ("--help",),
            ("watch", "--help"),
            ("attach", "--help"),
            ("run", "--help"),
        ):
            command = _run(monitor, arguments, timeout=timeout)
            commands.append(command)
            _require_success(command)

        with (
            HubFixture() as fixture,
            tempfile.TemporaryDirectory(
                prefix="hf acceptance - ", suffix=" - Unicode-测试"
            ) as temporary,
        ):
            local_dir = Path(temporary) / "downloads with spaces - 测试"
            arguments = (
                "run",
                "acceptance/tiny",
                "--revision",
                REVISION,
                "--filename",
                "config.json",
                "--local-dir",
                str(local_dir),
                "--json",
                "--hf-executable",
                str(hf_executable),
            )
            environment = os.environ.copy()
            environment["HF_ENDPOINT"] = fixture.endpoint
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            command = _run(monitor, arguments, timeout=timeout, environment=environment)
            commands.append(command)
            _require_success(command)
            snapshot = _parse_single_json(command.stdout)
            _validate_snapshot(snapshot)
            if not command.stderr.strip():
                raise AcceptanceError("expected progress or downloader activity on stderr")
    except Exception as exc:
        _write_report(report, tag, asset, commands, "failed", str(exc))
        if isinstance(exc, AcceptanceError):
            raise
        raise AcceptanceError(_redact(str(exc))) from exc

    result = AcceptanceResult(
        schema_version=1,
        tag=tag,
        asset=asset,
        operating_system=platform.system().lower(),
        architecture=platform.machine().lower(),
        checksum_status="verified",
        commands=tuple(commands),
        outcome="passed",
    )
    _write_report(report, tag, asset, commands, result.outcome)
    return result


def _validate_inputs(
    monitor: Path,
    hf_executable: Path,
    tag: str,
    asset: str,
    report: Path,
    checkout_root: Path,
) -> None:
    for label, path in (("monitor", monitor), ("hf executable", hf_executable), ("report", report)):
        if not path.is_absolute():
            raise AcceptanceError(f"{label} path must be absolute")
    if not monitor.is_file() or not hf_executable.is_file():
        raise AcceptanceError("monitor and hf executable must exist as files")
    if STABLE_TAG.fullmatch(tag) is None:
        raise AcceptanceError("tag must use stable vMAJOR.MINOR.PATCH syntax")
    try:
        report.resolve().relative_to(checkout_root.resolve())
    except ValueError:
        pass
    else:
        raise AcceptanceError("report must be outside the checkout")
    allowed = {
        "windows": ("windows-x86_64.exe", "windows-arm64.exe"),
        "linux": ("linux-x86_64", "linux-arm64"),
        "darwin": ("macos-x86_64", "macos-arm64"),
    }
    system = platform.system().lower()
    if not asset.endswith(allowed.get(system, ())) and not asset.endswith(".whl"):
        raise AcceptanceError(f"asset {asset!r} is unsupported on {system}")


def _run(
    monitor: Path,
    arguments: tuple[str, ...],
    *,
    timeout: float,
    environment: dict[str, str] | None = None,
) -> CommandResult:
    executable = [str(monitor)]
    if monitor.suffix.lower() == ".py":
        executable.insert(0, sys.executable)
    try:
        completed = subprocess.run(
            [*executable, *arguments],
            cwd=tempfile.gettempdir(),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AcceptanceError(f"command timed out after {timeout:g} seconds") from exc
    return CommandResult(
        arguments=arguments,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _require_success(command: CommandResult) -> None:
    if command.exit_code != 0:
        raise AcceptanceError(f"command returned exit code {command.exit_code}")


def _parse_single_json(text: str) -> dict[str, Any]:
    try:
        value, end = json.JSONDecoder().raw_decode(text.lstrip())
    except json.JSONDecodeError as exc:
        raise AcceptanceError("stdout did not contain one valid JSON document") from exc
    if text.lstrip()[end:].strip() or not isinstance(value, dict):
        raise AcceptanceError("stdout contained data outside the single JSON document")
    return cast(dict[str, Any], value)


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    repository = snapshot.get("repository")
    integrity = snapshot.get("integrity")
    files = snapshot.get("files")
    if (
        not isinstance(repository, dict)
        or cast(dict[str, object], repository).get("resolved_revision") != REVISION
    ):
        raise AcceptanceError("final snapshot did not contain the pinned revision")
    if (
        not isinstance(integrity, dict)
        or cast(dict[str, object], integrity).get("failed_files") != 0
    ):
        raise AcceptanceError("final snapshot reported an integrity failure")
    if not isinstance(files, list) or not any(
        isinstance(item, dict)
        and cast(dict[str, object], item).get("filename") == "config.json"
        and cast(dict[str, object], item).get("state")
        in {"complete", "complete_unverified", "verified"}
        for item in cast(list[object], files)
    ):
        raise AcceptanceError("final snapshot did not contain a completed config.json")


def _write_report(
    path: Path,
    tag: str,
    asset: str,
    commands: list[CommandResult],
    outcome: str,
    error: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "tag": tag,
        "asset": asset,
        "operating_system": platform.system().lower(),
        "architecture": platform.machine().lower(),
        "checksum_status": "verified",
        "commands": [
            {"arguments": list(item.arguments[:2]), "exit_code": item.exit_code}
            for item in commands
        ],
        "outcome": outcome,
    }
    if error:
        payload["error"] = _redact(error)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _redact(value: str) -> str:
    value = re.sub(r"(?i)(?:HF_TOKEN|TOKEN|PASSWORD|PASSPHRASE)\s*=\s*\S+", "[REDACTED]", value)
    value = re.sub(r"(?i)Authorization\s*:\s*Bearer\s+\S+", "Authorization: [REDACTED]", value)
    return value


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor", type=Path, required=True)
    parser.add_argument("--hf-executable", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--checkout-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_acceptance(**vars(args))
    except AcceptanceError as exc:
        print(f"acceptance failed: {_redact(str(exc))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
