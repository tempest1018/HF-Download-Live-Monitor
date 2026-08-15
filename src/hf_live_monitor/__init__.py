"""HF Live Monitor."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hf-live-monitor")
except PackageNotFoundError:
    __version__ = "0.1.0.dev0"

__all__ = ["__version__"]
