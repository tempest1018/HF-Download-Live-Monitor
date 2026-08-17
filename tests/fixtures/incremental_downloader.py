"""Deterministic no-network downloader used by integration tests."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import time
from pathlib import Path, PurePosixPath


def _partial_path(destination: Path, filename: str) -> Path:
    relative = PurePosixPath(filename.replace("\\", "/"))
    key = base64.urlsafe_b64encode(
        hashlib.sha1(f"{relative.name}.metadata".encode()).digest()
    ).decode()
    return (
        destination
        / ".cache"
        / "huggingface"
        / "download"
        / Path(*relative.parent.parts)
        / f"{key}.simulation.incomplete"
    )


def download(
    destination: Path,
    filename: str,
    source: Path,
    *,
    chunk_size: int,
    delay: float,
    corrupt: bool,
    ready_marker: Path,
    continue_marker: Path,
    handshake_timeout: float,
) -> None:
    content = source.read_bytes()
    if corrupt and content:
        content = content[:-1] + bytes((content[-1] ^ 0xFF,))
    partial = _partial_path(destination, filename)
    final = destination / Path(*PurePosixPath(filename.replace("\\", "/")).parts)
    partial.parent.mkdir(parents=True, exist_ok=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    with partial.open("wb") as stream:
        for offset in range(0, len(content), chunk_size):
            stream.write(content[offset : offset + chunk_size])
            stream.flush()
            os.fsync(stream.fileno())
            if offset == 0:
                ready_marker.touch()
                deadline = time.monotonic() + handshake_timeout
                while not continue_marker.is_file():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("monitor did not acknowledge partial download")
                    time.sleep(0.005)
            time.sleep(delay)
    os.replace(partial, final)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("filename")
    parser.add_argument("source", type=Path)
    parser.add_argument("--chunk-size", type=int, required=True)
    parser.add_argument("--delay", type=float, required=True)
    parser.add_argument("--corrupt", action="store_true")
    parser.add_argument("--ready-marker", type=Path, required=True)
    parser.add_argument("--continue-marker", type=Path, required=True)
    parser.add_argument("--handshake-timeout", type=float, default=5.0)
    args = parser.parse_args()
    if args.chunk_size <= 0 or args.delay < 0 or args.handshake_timeout <= 0:
        parser.error("chunk size and timeout must be positive; delay must be non-negative")
    download(
        args.destination,
        args.filename,
        args.source,
        chunk_size=args.chunk_size,
        delay=args.delay,
        corrupt=args.corrupt,
        ready_marker=args.ready_marker,
        continue_marker=args.continue_marker,
        handshake_timeout=args.handshake_timeout,
    )


if __name__ == "__main__":
    main()
