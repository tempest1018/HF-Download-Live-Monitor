#!/usr/bin/env python3
"""Live per-file Hugging Face local-dir download monitor."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

from huggingface_hub import HfApi


REFRESH_SECONDS = 0.25
RATE_WINDOW_SECONDS = 1.0


def human_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = max(0.0, float(value))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{value:.0f} B"
        value /= 1024.0
    return f"{value:.1f} TiB"


def current_download() -> tuple[str, str, Path] | None:
    """Return repo, revision and local-dir from the running `hf download`."""
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            raw = (proc / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        try:
            download_index = args.index("download")
        except ValueError:
            continue
        if download_index + 1 >= len(args) or "hf" not in " ".join(args[: download_index + 1]):
            continue
        repo = args[download_index + 1]
        try:
            local_dir = Path(args[args.index("--local-dir") + 1])
        except (ValueError, IndexError):
            continue
        try:
            revision = args[args.index("--revision") + 1]
        except (ValueError, IndexError):
            revision = "main"
        return repo, revision, local_dir
    return None


def sibling_size(sibling: object) -> int | None:
    size = getattr(sibling, "size", None)
    if size is not None:
        return int(size)
    lfs = getattr(sibling, "lfs", None)
    if isinstance(lfs, dict) and lfs.get("size") is not None:
        return int(lfs["size"])
    if lfs is not None and getattr(lfs, "size", None) is not None:
        return int(lfs.size)
    return None


def repository_files(repo: str, revision: str) -> dict[str, int]:
    info = HfApi().model_info(repo_id=repo, revision=revision, files_metadata=True)
    result: dict[str, int] = {}
    for sibling in info.siblings or []:
        size = sibling_size(sibling)
        if size is not None:
            result[sibling.rfilename] = size
    if not result:
        raise RuntimeError("Hub returned no file-size metadata")
    return result


def incomplete_for(local_dir: Path, filename: str) -> Path | None:
    target = local_dir / filename
    metadata = local_dir / ".cache" / "huggingface" / "download" / filename
    metadata = metadata.with_name(metadata.name + ".metadata")
    short_hash = base64.urlsafe_b64encode(hashlib.sha1(metadata.name.encode()).digest()).decode()
    candidates = list(metadata.parent.glob(f"{short_hash}.*.incomplete"))
    candidates.extend(metadata.parent.glob(f"{target.name}.*.incomplete"))
    candidates = [candidate for candidate in candidates if candidate.is_file()]
    return max(candidates, key=lambda item: item.stat().st_mtime_ns) if candidates else None


def shorten(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return "..." + value[-(width - 3) :]


def main() -> int:
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "60")
    last_context: tuple[str, str, Path] | None = None
    files: dict[str, int] = {}
    metadata_error = ""
    next_metadata_retry = 0.0
    histories: dict[str, deque[tuple[float, int]]] = defaultdict(deque)

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    try:
        while True:
            now = time.monotonic()
            context = current_download() or last_context
            if context is None:
                sys.stdout.write("\033[2J\033[HWaiting for an active `hf download` process...\n")
                sys.stdout.flush()
                time.sleep(REFRESH_SECONDS)
                continue

            if context != last_context:
                last_context = context
                histories.clear()
                files = {}
                metadata_error = ""
                next_metadata_retry = 0.0

            repo, revision, local_dir = context
            if not files and now >= next_metadata_retry:
                try:
                    files = repository_files(repo, revision)
                    metadata_error = ""
                except Exception as exc:
                    files = {}
                    metadata_error = f"Could not load repository sizes: {type(exc).__name__}: {exc}"
                    next_metadata_retry = now + 10.0

            rows: list[tuple[str, int, int, float, str]] = []
            downloaded_total = 0
            expected_total = 0
            completed_count = 0

            for filename, expected in files.items():
                final_path = local_dir / filename
                partial_path = incomplete_for(local_dir, filename)
                if final_path.is_file():
                    downloaded = min(final_path.stat().st_size, expected)
                    completed_count += 1
                elif partial_path is not None:
                    downloaded = min(partial_path.stat().st_size, expected)
                else:
                    downloaded = 0

                downloaded_total += downloaded
                expected_total += expected

                if partial_path is None:
                    continue

                key = str(partial_path)
                history = histories[key]
                history.append((now, downloaded))
                while len(history) > 2 and history[1][0] <= now - RATE_WINDOW_SECONDS:
                    history.popleft()
                elapsed = now - history[0][0]
                delta = max(0, downloaded - history[0][1])
                speed = delta / elapsed if elapsed > 0 else 0.0
                state = "ACTIVE" if speed > 0 else ("MEASURING" if elapsed < 0.75 else "WAIT/RETRY")
                rows.append((filename, downloaded, expected, speed, state))

            columns = shutil.get_terminal_size((120, 20)).columns
            name_width = max(24, columns - 58)
            output = [
                f"LIVE PER-FILE DOWNLOAD — 4 refreshes/sec, 1-second rolling speed",
                f"Repository: {repo}",
                f"{'FILE':<{name_width}}  {'DOWNLOADED / TOTAL':>23}  {'DONE':>7}  {'SPEED':>12}  STATE",
            ]
            total_speed = 0.0
            if rows:
                for filename, downloaded, expected, speed, state in sorted(rows):
                    total_speed += speed
                    progress = (downloaded / expected * 100.0) if expected else 0.0
                    sizes = f"{human_bytes(downloaded)} / {human_bytes(expected)}"
                    output.append(
                        f"{shorten(filename, name_width):<{name_width}}  {sizes:>23}  "
                        f"{progress:6.2f}%  {(human_bytes(speed) + '/s'):>12}  {state}"
                    )
            else:
                output.append("No active partial file is visible (metadata/finalization may be in progress).")

            repo_progress = (downloaded_total / expected_total * 100.0) if expected_total else 0.0
            output.extend(
                [
                    "-" * min(columns, 140),
                    f"REPOSITORY BYTES: {human_bytes(downloaded_total)} / {human_bytes(expected_total)}  "
                    f"| {repo_progress:6.2f}%  | FILES {completed_count}/{len(files)}  "
                    f"| ACTIVE {len(rows)}  | COMBINED {human_bytes(total_speed)}/s",
                ]
            )
            if metadata_error:
                output.append(metadata_error)

            sys.stdout.write("\033[2J\033[H" + "\n".join(output) + "\n")
            sys.stdout.flush()
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
