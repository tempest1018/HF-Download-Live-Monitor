import hashlib
from pathlib import Path


def test_preserved_prototype_matches_recorded_checksum() -> None:
    prototype = Path("docs/prototypes/hf_live_file_monitor.py")
    expected = (
        Path("docs/prototypes/hf_live_file_monitor_checksum.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert hashlib.sha256(prototype.read_bytes()).hexdigest() == expected
