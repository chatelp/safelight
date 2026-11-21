"""The VERSION file is the single source of truth; the Lightroom plugin must match it."""

import re
from pathlib import Path

from safelight import __version__

ROOT = Path(__file__).resolve().parents[1]
INFO_LUA = ROOT / "plugin" / "Safelight.lrplugin" / "Info.lua"


def _version_file() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _numeric_parts(version: str) -> tuple[int, int, int]:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    assert match, f"VERSION must start with MAJOR.MINOR.PATCH, got {version!r}"
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def test_package_version_matches_version_file():
    assert __version__ == _version_file()


def test_plugin_version_matches_version_file():
    info = INFO_LUA.read_text(encoding="utf-8")
    match = re.search(
        r"VERSION\s*=\s*\{\s*major\s*=\s*(\d+),\s*minor\s*=\s*(\d+),\s*revision\s*=\s*(\d+)",
        info,
    )
    assert match, "Info.lua must declare VERSION = { major = .., minor = .., revision = .. }"
    assert tuple(int(part) for part in match.groups()) == _numeric_parts(_version_file()), (
        "Info.lua is out of date: run `uv run python scripts/sync_version.py`"
    )
