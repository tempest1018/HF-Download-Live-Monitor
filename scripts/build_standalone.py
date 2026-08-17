"""Build the platform-native HF Download Live Monitor executable."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path


def normalized_architecture(machine: str) -> str:
    value = machine.lower()
    if value in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    raise ValueError(f"unsupported architecture: {machine}")


def normalized_system(system: str) -> str:
    systems = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}
    try:
        return systems[system]
    except KeyError as error:
        raise ValueError(f"unsupported system: {system}") from error


def artifact_name(system: str, machine: str) -> str:
    os_name = normalized_system(system)
    architecture = normalized_architecture(machine)
    suffix = ".exe" if os_name == "windows" else ""
    return f"hf-download-live-monitor-{os_name}-{architecture}{suffix}"


def run_pyinstaller(arguments: list[str]) -> None:
    import PyInstaller.__main__

    PyInstaller.__main__.run(arguments)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    system = platform.system()
    labelled = Path("dist") / artifact_name(system, platform.machine())
    checksum = labelled.with_name(f"{labelled.name}.sha256")
    checksum_temp = checksum.with_name(f".{checksum.name}.tmp")
    for stale_path in (labelled, checksum, checksum_temp):
        stale_path.unlink(missing_ok=True)

    run_pyinstaller(["--clean", "--noconfirm", "hf_download_live_monitor.spec"])
    suffix = ".exe" if system == "Windows" else ""
    executable = Path("dist") / f"hf-download-live-monitor{suffix}"
    if not executable.is_file():
        raise FileNotFoundError(executable)

    executable.replace(labelled)
    digest = sha256_file(labelled)
    checksum_temp.write_text(f"{digest}  {labelled.name}\n", encoding="ascii")
    checksum_temp.replace(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
