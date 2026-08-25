from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

EXPECTED_STANDALONES = (
    "hf-download-live-monitor-windows-x86_64.exe",
    "hf-download-live-monitor-windows-arm64.exe",
    "hf-download-live-monitor-linux-x86_64",
    "hf-download-live-monitor-linux-arm64",
    "hf-download-live-monitor-macos-x86_64",
    "hf-download-live-monitor-macos-arm64",
)
_PROJECT_NAME = "hf-download-live-monitor"
_CHECKSUM = re.compile(r"([0-9a-f]{64})  ([^/\\\r\n]+)\r?\n?\Z")
_STABLE_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")


class ReleaseValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedBundle:
    wheel: Path
    sdist: Path
    standalones: tuple[Path, ...]


def parse_stable_tag(tag: str) -> str:
    if _STABLE_TAG.fullmatch(tag) is None:
        raise ReleaseValidationError(f"expected stable tag vMAJOR.MINOR.PATCH, got {tag!r}")
    return tag[1:]


def project_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    section = re.search(r"(?ms)^\[project\]\s*$\n(.*?)(?=^\[|\Z)", text)
    if section is None:
        raise ReleaseValidationError("pyproject.toml has no [project] section")
    matches = re.findall(r'(?m)^version\s*=\s*"([^"]+)"\s*$', section.group(1))
    if len(matches) != 1 or _VERSION.fullmatch(matches[0]) is None:
        raise ReleaseValidationError("pyproject.toml must contain one stable project version")
    return matches[0]


def _metadata_version(data: bytes, source: str) -> str:
    if len(data) > 1024 * 1024:
        raise ReleaseValidationError(f"{source} metadata is too large")
    message = BytesParser().parsebytes(data)
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if names != [_PROJECT_NAME] or len(versions) != 1:
        raise ReleaseValidationError(f"{source} has invalid or ambiguous package metadata")
    version = versions[0]
    if _VERSION.fullmatch(version) is None:
        raise ReleaseValidationError(f"{source} does not contain a stable version")
    return version


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in name


def wheel_version(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            unsafe = [name for name in archive.namelist() if not _safe_member(name)]
            metadata = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and _safe_member(name)
            ]
            if unsafe or len(metadata) != 1:
                raise ReleaseValidationError("wheel has unsafe or ambiguous metadata members")
            return _metadata_version(archive.read(metadata[0]), "wheel")
    except (OSError, zipfile.BadZipFile) as error:
        raise ReleaseValidationError(f"cannot inspect wheel: {type(error).__name__}") from error


def sdist_version(path: Path) -> str:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if any(not _safe_member(member.name) for member in members):
                raise ReleaseValidationError("sdist has an unsafe archive member")
            metadata = [
                member
                for member in members
                if member.isfile()
                and len(PurePosixPath(member.name).parts) == 2
                and PurePosixPath(member.name).name == "PKG-INFO"
            ]
            if len(metadata) != 1 or metadata[0].size > 1024 * 1024:
                raise ReleaseValidationError("sdist has missing or ambiguous package metadata")
            stream = archive.extractfile(metadata[0])
            if stream is None:
                raise ReleaseValidationError("cannot read sdist package metadata")
            return _metadata_version(stream.read(), "sdist")
    except (OSError, tarfile.TarError) as error:
        raise ReleaseValidationError(f"cannot inspect sdist: {type(error).__name__}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_checksum(text: str, source: str) -> tuple[str, str]:
    match = _CHECKSUM.fullmatch(text)
    if match is None:
        raise ReleaseValidationError(f"{source} has invalid checksum syntax")
    return match.group(1), match.group(2)


def write_aggregate_checksums(directory: Path) -> Path:
    payloads = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name != "SHA256SUMS" and not path.name.endswith(".sha256")
    )
    target = directory / "SHA256SUMS"
    target.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in payloads), encoding="ascii"
    )
    return target


def _distribution_files(directory: Path) -> tuple[Path, Path]:
    wheels = list(directory.glob("hf_download_live_monitor-*.whl"))
    sdists = list(directory.glob("hf_download_live_monitor-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseValidationError("bundle must contain exactly one wheel and one sdist")
    return wheels[0], sdists[0]


def validate_bundle(directory: Path, *, tag: str, project_file: Path) -> ValidatedBundle:
    if not directory.is_dir():
        raise ReleaseValidationError("release bundle directory does not exist")
    if any(path.is_dir() for path in directory.iterdir()):
        raise ReleaseValidationError("release bundle must be flat")
    version = parse_stable_tag(tag)
    if project_version(project_file) != version:
        raise ReleaseValidationError("tag and project version mismatch")
    wheel, sdist = _distribution_files(directory)
    if wheel_version(wheel) != version or sdist_version(sdist) != version:
        raise ReleaseValidationError("package metadata version mismatch")
    if not wheel.name.startswith(f"hf_download_live_monitor-{version}-"):
        raise ReleaseValidationError("wheel filename version mismatch")
    if sdist.name != f"hf_download_live_monitor-{version}.tar.gz":
        raise ReleaseValidationError("sdist filename version mismatch")

    standalones = tuple(directory / name for name in EXPECTED_STANDALONES)
    payloads = (*standalones, wheel, sdist)
    expected_names = {path.name for path in payloads}
    expected_files = (
        expected_names | {f"{name}.sha256" for name in EXPECTED_STANDALONES} | {"SHA256SUMS"}
    )
    actual_files = {path.name for path in directory.iterdir() if path.is_file()}
    if actual_files != expected_files:
        difference = sorted(actual_files ^ expected_files)
        raise ReleaseValidationError(f"missing or unexpected release files: {difference}")

    for artifact in standalones:
        checksum_path = directory / f"{artifact.name}.sha256"
        checksum, name = _parse_checksum(
            checksum_path.read_text(encoding="ascii"), checksum_path.name
        )
        if name != artifact.name or checksum != _sha256(artifact):
            raise ReleaseValidationError(f"checksum mismatch for {artifact.name}")

    entries: dict[str, str] = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="ascii").splitlines(keepends=True):
        checksum, name = _parse_checksum(line, "SHA256SUMS")
        if name in entries:
            raise ReleaseValidationError(f"duplicate SHA256SUMS entry: {name}")
        entries[name] = checksum
    if set(entries) != expected_names:
        raise ReleaseValidationError("SHA256SUMS inventory mismatch")
    for payload in payloads:
        if entries[payload.name] != _sha256(payload):
            raise ReleaseValidationError(f"SHA256SUMS mismatch for {payload.name}")
    return ValidatedBundle(wheel, sdist, standalones)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an HF Download Live Monitor release")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--project-file", type=Path, required=True)
    parser.add_argument("--write-aggregate", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.write_aggregate:
            write_aggregate_checksums(arguments.directory)
        bundle = validate_bundle(
            arguments.directory, tag=arguments.tag, project_file=arguments.project_file
        )
    except (OSError, UnicodeError, ReleaseValidationError) as error:
        print(f"release validation failed: {error}", file=sys.stderr)
        return 1
    print(f"validated {arguments.tag}: {len(bundle.standalones)} standalone files, wheel, sdist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
