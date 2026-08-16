import hashlib
from pathlib import Path


def test_preserved_prototype_matches_recorded_checksum() -> None:
    prototype = Path("docs/prototypes/hf_live_file_monitor.py")
    expected = (
        Path("docs/prototypes/hf_live_file_monitor_checksum.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    # Git may materialize this historical text file with CRLF on Windows. The
    # recorded digest protects its content, independent of checkout policy.
    content = prototype.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(content).hexdigest() == expected
