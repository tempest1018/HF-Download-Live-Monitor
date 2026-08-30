from pathlib import Path

import pytest

from hf_download_live_monitor.security import (
    redact_args,
    redact_text,
    resolve_repo_path,
    sanitize_persisted_diagnostic,
)


@pytest.mark.parametrize(
    "args",
    [
        ("hf", "download", "owner/repo", "--token", "hf_secret"),
        ("hf", "download", "owner/repo", "--token=hf_secret"),
        ("HF_TOKEN=hf_secret", "hf", "download", "owner/repo"),
    ],
)
def test_redact_args_removes_tokens(args: tuple[str, ...]) -> None:
    assert "hf_secret" not in " ".join(redact_args(args))


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer hf_secret",
        "https://example.test/file?token=hf_secret&x=1",
        "HF_TOKEN=hf_secret",
    ],
)
def test_redact_text_removes_tokens(text: str) -> None:
    assert "hf_secret" not in redact_text(text)


def test_resolve_repo_path_accepts_nested_file(tmp_path: Path) -> None:
    assert (
        resolve_repo_path(tmp_path, "weights/model.bin")
        == (tmp_path / "weights" / "model.bin").resolve()
    )


@pytest.mark.parametrize(
    "filename", ["../secret", "weights/../../secret", "/etc/passwd", "C:/secret"]
)
def test_resolve_repo_path_rejects_escape(tmp_path: Path, filename: str) -> None:
    with pytest.raises(ValueError, match="unsafe repository path"):
        resolve_repo_path(tmp_path, filename)


def test_resolve_repo_path_rejects_sibling_prefix_trick(tmp_path: Path) -> None:
    root = tmp_path / "download"
    with pytest.raises(ValueError, match="unsafe repository path"):
        resolve_repo_path(root, "../download-other/file.bin")


def test_persisted_diagnostic_removes_tokens_paths_and_controls() -> None:
    value = "failed at C:\\Users\\person\\secret with token=hf_" + "x" * 40 + "\x00"
    sanitized = sanitize_persisted_diagnostic(value)
    assert "person" not in sanitized
    assert "hf_" not in sanitized
    assert "\x00" not in sanitized
    assert len(sanitized) <= 512
