"""Stable public error categories and process exit codes."""

from enum import Enum


class ErrorCategory(str, Enum):
    USAGE = "usage"
    ACCESS = "access"
    REPOSITORY = "repository"
    DESTINATION = "destination"
    DOWNLOADER = "downloader"
    MONITOR = "monitor"
    INTEGRITY = "integrity"
    CANCELLED = "cancelled"


_EXIT_CODES = {category: index for index, category in enumerate(ErrorCategory, start=2)}


def exit_code_for(category: ErrorCategory) -> int:
    """Return the stable process exit code for an error category."""
    return _EXIT_CODES[category]
