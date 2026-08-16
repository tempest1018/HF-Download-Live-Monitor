"""Build the platform-native HF Download Live Monitor executable."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path

import PyInstaller.__main__


def main() -> int:
    PyInstaller.__main__.run(["--clean", "--noconfirm", "hf_download_live_monitor.spec"])
    suffix = ".exe" if platform.system() == "Windows" else ""
    executable = Path("dist") / f"hf-download-live-monitor{suffix}"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    checksum = executable.with_name(f"{executable.name}.sha256")
    checksum.write_text(f"{digest}  {executable.name}\n", encoding="ascii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
