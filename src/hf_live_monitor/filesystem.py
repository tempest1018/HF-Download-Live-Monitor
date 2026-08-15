"""Race-resistant observation of Hugging Face local downloads."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path, PurePosixPath

from hf_live_monitor.compat import short_cache_hash
from hf_live_monitor.models import DownloadSpec, FileObservation, ManifestFile
from hf_live_monitor.security import resolve_repo_path


class FileSystemObserver:
    def observe(
        self,
        spec: DownloadSpec,
        manifest: tuple[ManifestFile, ...],
        now: float,
    ) -> tuple[FileObservation, ...]:
        partials = self._index_partials(spec.local_dir)
        observations: list[FileObservation] = []
        for item in manifest:
            final_size = _safe_size(resolve_repo_path(spec.local_dir, item.filename))
            repo_path = PurePosixPath(item.filename.replace("\\", "/"))
            key = (
                repo_path.parent.as_posix(),
                short_cache_hash(f"{repo_path.name}.metadata"),
            )
            partial_size = self._newest_size(partials.get(key, ()))
            visible = final_size if final_size is not None else partial_size or 0
            observations.append(
                FileObservation(
                    filename=item.filename,
                    expected_bytes=item.expected_bytes,
                    visible_bytes=visible,
                    final_bytes=final_size,
                    partial_bytes=partial_size,
                    observed_at=now,
                )
            )
        return tuple(observations)

    @staticmethod
    def _index_partials(local_dir: Path) -> dict[tuple[str, str], tuple[Path, ...]]:
        root = local_dir / ".cache" / "huggingface" / "download"
        found: dict[tuple[str, str], list[Path]] = defaultdict(list)
        try:
            paths = root.rglob("*.incomplete")
            for path in paths:
                relative = path.relative_to(root)
                prefix = path.name.split(".", 1)[0]
                found[(relative.parent.as_posix(), prefix)].append(path)
        except OSError:
            return {}
        return {key: tuple(value) for key, value in found.items()}

    @staticmethod
    def _newest_size(paths: tuple[Path, ...]) -> int | None:
        candidates: list[tuple[int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            candidates.append((stat.st_mtime_ns, stat.st_size))
        return max(candidates)[1] if candidates else None


def _safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None
