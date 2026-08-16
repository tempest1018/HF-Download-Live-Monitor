"""Pure parsing of supported Hugging Face download command lines."""

from __future__ import annotations

from pathlib import Path

from hf_download_live_monitor.models import DownloadSpec, RepoType
from hf_download_live_monitor.processes import ProcessRecord

_VALUE_OPTIONS = {
    "--local-dir",
    "--repo-type",
    "--revision",
    "--include",
    "--exclude",
    "--token",
    "--access-token",
}


def parse_download_process(process: ProcessRecord) -> DownloadSpec | None:
    download_index = _download_index(process.args)
    if download_index is None or download_index + 1 >= len(process.args):
        return None

    tokens = process.args[download_index + 1 :]
    repo: str | None = None
    local_dir: str | None = None
    repo_type = RepoType.MODEL
    revision = "main"
    filenames: list[str] = []
    includes: list[str] = []
    excludes: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        option, equals, inline_value = token.partition("=")
        if option in _VALUE_OPTIONS:
            if equals:
                value = inline_value
            elif index + 1 < len(tokens):
                index += 1
                value = tokens[index]
            else:
                return None
            if option == "--local-dir":
                local_dir = value
            elif option == "--repo-type":
                try:
                    repo_type = RepoType.parse(value)
                except ValueError:
                    return None
            elif option == "--revision":
                revision = value
            elif option == "--include":
                includes.append(value)
            elif option == "--exclude":
                excludes.append(value)
        elif token.startswith("-"):
            pass
        elif repo is None:
            repo = token
        else:
            filenames.append(token)
        index += 1

    if repo is None or local_dir is None:
        return None
    destination = Path(local_dir)
    if not destination.is_absolute():
        destination = process.cwd / destination
    return DownloadSpec(
        repo=repo,
        local_dir=destination.resolve(),
        repo_type=repo_type,
        revision=revision,
        filenames=tuple(filenames),
        includes=tuple(includes),
        excludes=tuple(excludes),
    )


def _download_index(args: tuple[str, ...]) -> int | None:
    for index, value in enumerate(args):
        if value != "download":
            continue
        prefix = args[:index]
        if any(Path(item).stem.lower() in {"hf", "hf.exe"} for item in prefix):
            return index
        if "-m" in prefix and any("huggingface" in item for item in prefix):
            return index
    return None
