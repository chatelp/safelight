"""Where Safelight keeps its runtime files."""

from __future__ import annotations

import os
from pathlib import Path

from .protocol import DEFAULT_PORT


def safelight_home() -> Path:
    """Directory for hub.json and logs. The Lightroom plugin reads hub.json from here."""
    override = os.environ.get("SAFELIGHT_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "Safelight"


def hub_config_path(home: Path | None = None) -> Path:
    return (home or safelight_home()) / "hub.json"


def hub_log_path(home: Path | None = None) -> Path:
    return (home or safelight_home()) / "hub.log"


def configured_port() -> int:
    """Port the hub binds to. SAFELIGHT_PORT=0 picks a free port (used by tests)."""
    return int(os.environ.get("SAFELIGHT_PORT", DEFAULT_PORT))
