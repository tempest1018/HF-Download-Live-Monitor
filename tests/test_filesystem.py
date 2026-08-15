import os
from pathlib import Path

from hf_live_monitor.compat import short_cache_hash
from hf_live_monitor.filesystem import FileSystemObserver
from hf_live_monitor.models import DownloadSpec, ManifestFile


def make_partial(root: Path, filename: str, etag: str, content: bytes) -> Path:
    repo_path = Path(*filename.split("/"))
    directory = root / ".cache" / "huggingface" / "download" / repo_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    prefix = short_cache_hash(f"{repo_path.name}.metadata")
    path = directory / f"{prefix}.{etag}.incomplete"
    path.write_bytes(content)
    return path


def test_observe_reports_missing_partial_complete_and_undersized(tmp_path: Path) -> None:
    (tmp_path / "complete.bin").write_bytes(b"1234")
    (tmp_path / "short.bin").write_bytes(b"12")
    make_partial(tmp_path, "partial.bin", "one", b"123")
    manifest = (
        ManifestFile("missing.bin", 5),
        ManifestFile("partial.bin", 5),
        ManifestFile("complete.bin", 4),
        ManifestFile("short.bin", 4),
        ManifestFile("empty.bin", 0),
    )
    (tmp_path / "empty.bin").write_bytes(b"")

    observed = FileSystemObserver().observe(
        DownloadSpec("owner/repo", tmp_path), manifest, now=10.0
    )
    by_name = {item.filename: item for item in observed}

    assert by_name["missing.bin"].visible_bytes == 0
    assert by_name["partial.bin"].partial_bytes == 3
    assert by_name["complete.bin"].final_bytes == 4
    assert by_name["short.bin"].final_bytes == 2
    assert by_name["empty.bin"].final_bytes == 0


def test_observe_uses_newest_incomplete_candidate(tmp_path: Path) -> None:
    old = make_partial(tmp_path, "model.bin", "old", b"12")
    new = make_partial(tmp_path, "model.bin", "new", b"1234")
    os.utime(old, ns=(1, 1))
    os.utime(new, ns=(2, 2))

    observed = FileSystemObserver().observe(
        DownloadSpec("owner/repo", tmp_path), (ManifestFile("model.bin", 10),), now=2.0
    )
    assert observed[0].partial_bytes == 4
    assert observed[0].visible_bytes == 4
