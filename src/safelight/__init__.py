"""Safelight for Lightroom Classic."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("safelight")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
