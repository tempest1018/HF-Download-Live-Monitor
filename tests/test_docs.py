from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "filename",
    [
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "docs/json-schema.md",
        "docs/architecture.md",
    ],
)
def test_distribution_document_exists_and_is_not_empty(filename: str) -> None:
    assert Path(filename).read_text(encoding="utf-8").strip()


def test_readme_documents_all_output_modes() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    for value in ("--plain", "--json", "--jsonl", "--once"):
        assert value in readme


def test_complete_user_manual_is_linked_and_covers_operations() -> None:
    readme = Path("README.md").read_text(encoding="utf-8").lower()
    manual_path = Path("docs/user-manual.md")
    assert "docs/user-manual.md" in readme
    manual = manual_path.read_text(encoding="utf-8").lower()
    required_topics = (
        "prerequisites",
        "authentication",
        "installation",
        "verify the installation",
        "quick start",
        "watch mode",
        "attach mode",
        "run mode",
        "output formats",
        "update",
        "downgrade",
        "uninstall",
        "troubleshooting",
        "privacy",
        "support",
    )
    for topic in required_topics:
        assert topic in manual
