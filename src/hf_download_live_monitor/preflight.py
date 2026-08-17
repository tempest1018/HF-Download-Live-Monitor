"""Destination safety and capacity checks performed before download launch."""

from __future__ import annotations

import math
import shutil
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.models import DownloadPlan, MonitorError
from hf_download_live_monitor.security import redact_text, resolve_repo_path


class DiskUsage(Protocol):
    @property
    def free(self) -> int: ...


@dataclass(frozen=True, slots=True)
class PreflightResult:
    required_bytes: int
    available_bytes: int
    reserve_bytes: int


def validate_destination(
    plan: DownloadPlan,
    reserve_ratio: float = 0.10,
    disk_usage: Callable[[Path], DiskUsage] = shutil.disk_usage,
) -> PreflightResult:
    """Verify that the destination is writable and has conservative free capacity."""
    if not math.isfinite(reserve_ratio) or reserve_ratio < 0:
        raise ValueError("reserve ratio must be finite and non-negative")

    destination = plan.spec.local_dir
    probe: Path | None = None
    try:
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        if not destination.is_dir():
            raise OSError("destination is not a directory")
        probe = destination / f".hf-download-live-monitor-{uuid.uuid4().hex}.probe"
        with probe.open("x", encoding="ascii") as handle:
            handle.write("probe")

        remaining = 0
        for manifest_file in plan.manifest:
            final_path = resolve_repo_path(destination, manifest_file.filename)
            try:
                exact = (
                    final_path.is_file()
                    and final_path.stat().st_size == manifest_file.expected_bytes
                )
            except OSError:
                exact = False
            if not exact:
                remaining += manifest_file.expected_bytes

        reserve = math.ceil(remaining * reserve_ratio)
        required = remaining + reserve
        available = int(disk_usage(destination).free)
    except ValueError as exc:
        safe_destination = redact_text(str(destination))
        raise MonitorError(
            "destination_unwritable",
            f"cannot safely use destination {safe_destination}: unsafe repository file path",
            category=ErrorCategory.DESTINATION,
        ) from exc
    except OSError as exc:
        safe_destination = redact_text(str(destination))
        raise MonitorError(
            "destination_unwritable",
            f"cannot safely use destination {safe_destination}: {redact_text(str(exc))}",
            category=ErrorCategory.DESTINATION,
        ) from exc
    finally:
        if probe is not None:
            with suppress(OSError):
                probe.unlink(missing_ok=True)

    if available < required:
        safe_destination = redact_text(str(destination))
        raise MonitorError(
            "insufficient_disk_space",
            f"required={required} available={available} destination={safe_destination}; "
            "free space or choose another destination",
            category=ErrorCategory.DESTINATION,
        )
    return PreflightResult(required, available, reserve)
