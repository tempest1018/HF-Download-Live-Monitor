import base64
import hashlib
from pathlib import Path

from hf_live_monitor.compat import incomplete_candidates, short_cache_hash


def test_short_cache_hash_matches_hugging_face_convention() -> None:
    expected = base64.urlsafe_b64encode(hashlib.sha1(b"model.bin.metadata").digest()).decode()
    assert short_cache_hash("model.bin.metadata") == expected


def test_incomplete_candidates_support_nested_filename(tmp_path: Path) -> None:
    metadata_dir = tmp_path / ".cache" / "huggingface" / "download" / "weights"
    metadata_dir.mkdir(parents=True)
    prefix = short_cache_hash("model.bin.metadata")
    candidate = metadata_dir / f"{prefix}.etag.incomplete"
    candidate.write_bytes(b"partial")

    assert incomplete_candidates(tmp_path, "weights/model.bin") == (candidate,)
