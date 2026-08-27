"""Black-box acceptance runner for published HF Download Live Monitor assets."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import signal
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
        files: dict[str, bytes] | None = None,
        corrupt_files: set[str] | None = None,
    ) -> None:
        if not content or chunk_size <= 0 or delay < 0:
            raise ValueError("fixture content and timing values must be valid")
        self.files = files or {"config.json": content}
        self.corrupt_files = corrupt_files or set()
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
        request = urlsplit(handler.path)
        query = parse_qs(request.query, keep_blank_values=True)
        filename = request.path.rsplit("/", 1)[-1]
        selected = self.files.get(filename)
        digest = hashlib.sha256(self.content).hexdigest()
        if request.path == metadata_path and set(query) <= {"blobs"}:
            body = json.dumps(
                {
                    "id": "acceptance/tiny",
                    "sha": REVISION,
                    "siblings": [
                        {
                            "rfilename": name,
                            "size": len(payload),
                            "lfs": {
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "size": len(payload),
                                "pointerSize": 130,
                            },
                        }
                        for name, payload in self.files.items()
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
        valid_paths = {
            f"/acceptance/tiny/resolve/{REVISION}/{name}" for name in self.files
        }
        if request.path not in valid_paths or not set(query) <= {"download"} or selected is None:
            handler.send_error(404)
            return
        digest = hashlib.sha256(selected).hexdigest()
        served = selected + b"corrupt" if filename in self.corrupt_files else selected
        handler.send_response(200)
        handler.send_header("x-repo-commit", REVISION)
        handler.send_header("etag", f'"{digest}"')
        handler.send_header("content-length", str(len(served)))
        handler.end_headers()
        if include_body:
            for offset in range(0, len(served), self.chunk_size):
                handler.wfile.write(served[offset : offset + self.chunk_size])
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
    multi: bool = False,
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
            if multi:
                with HubFixture(
                    files={
                        "config.json": DEFAULT_CONTENT * 32,
                        "corrupt.bin": DEFAULT_CONTENT * 32,
                    },
                    corrupt_files={"corrupt.bin"},
                    delay=0.003,
                ) as multi_fixture:
                    commands.append(
                        run_multi_acceptance(
                            monitor=monitor,
                            hf_executable=hf_executable,
                            fixture=multi_fixture,
                            root=Path(temporary),
                            timeout=timeout,
                        )
                    )
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


def run_multi_acceptance(
    *,
    monitor: Path,
    hf_executable: Path,
    fixture: HubFixture,
    root: Path,
    timeout: float,
) -> CommandResult:
    """Exercise continuous multi-attach and stop only the monitor after two finals."""
    children: list[subprocess.Popen[str]] = []
    monitor_process: subprocess.Popen[str] | None = None
    lines: list[str] = []
    environment = os.environ.copy()
    environment.update(
        HF_ENDPOINT=fixture.endpoint,
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        HF_HUB_DISABLE_PROGRESS_BARS="1",
    )
    try:
        for index, filename in enumerate(fixture.files):
            child_env = environment.copy()
            child_env["HF_HOME"] = str(root / f"hf-home-{index}")
            children.append(
                subprocess.Popen(
                    [
                        str(hf_executable),
                        "download",
                        "acceptance/tiny",
                        filename,
                        "--revision",
                        REVISION,
                        "--local-dir",
                        str(root / f"multi-{index}"),
                    ],
                    env=child_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            )
        executable = [str(monitor)]
        if monitor.suffix.lower() == ".py":
            executable.insert(0, sys.executable)
        monitor_process = subprocess.Popen(
            [*executable, "attach", "--all", "--jsonl", "--discovery-refresh", "0.05"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def collect() -> None:
            assert monitor_process is not None and monitor_process.stdout is not None
            lines.extend(monitor_process.stdout)

        reader = Thread(target=collect, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = _parse_jsonl_events(lines)
            if sum(item.get("event") == "session_finalized" for item in events) >= 2:
                break
            if monitor_process.poll() is not None:
                raise AcceptanceError("multi-download monitor exited before finalization")
            time.sleep(0.05)
        else:
            raise AcceptanceError("multi-download acceptance timed out")
        monitor_process.send_signal(signal.SIGINT)
        exit_code = monitor_process.wait(timeout=10)
        reader.join(timeout=2)
        events = _parse_jsonl_events(lines)
        _validate_supervisor_events(events)
        return CommandResult(("attach", "--all", "--jsonl"), exit_code, "".join(lines), "")
    finally:
        if monitor_process is not None and monitor_process.poll() is None:
            monitor_process.kill()
            monitor_process.wait(timeout=5)
        for child in children:
            if child.poll() is None:
                child.wait(timeout=10)


def _parse_jsonl_events(lines: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in tuple(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AcceptanceError("multi-download stdout contained invalid JSONL") from exc
        if not isinstance(payload, dict):
            raise AcceptanceError("multi-download JSONL event was not an object")
        events.append(cast(dict[str, Any], payload))
    return events


def _validate_supervisor_events(events: list[dict[str, Any]]) -> None:
    sequences = [item.get("sequence") for item in events]
    if sequences != sorted(set(sequences)):
        raise AcceptanceError("supervisor event sequences were not strictly increasing")
    finals = [item for item in events if item.get("event") == "session_finalized"]
    if len(finals) != 2 or not events or events[-1].get("event") != "supervisor_stopped":
        raise AcceptanceError("supervisor did not emit two finals and an orderly stop")
    lifecycles = {
        cast(dict[str, object], item.get("session", {})).get("lifecycle") for item in finals
    }
    if lifecycles != {"completed", "failed"}:
        raise AcceptanceError("supervisor final outcomes were not completed and failed")


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
    parser.add_argument("--multi", action="store_true")
    args = parser.parse_args()
    try:
        run_acceptance(**vars(args))
    except AcceptanceError as exc:
        print(f"acceptance failed: {_redact(str(exc))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
