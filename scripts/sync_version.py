"""Copy the version from VERSION into the Lightroom plugin's Info.lua."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFO_LUA = ROOT / "plugin" / "Safelight.lrplugin" / "Info.lua"
PATTERN = re.compile(r"VERSION\s*=\s*\{[^}]*\}")


def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        print(f"VERSION must start with MAJOR.MINOR.PATCH, got {version!r}", file=sys.stderr)
        return 1
    major, minor, patch = match.groups()
    replacement = (
        f"VERSION = {{ major = {major}, minor = {minor}, revision = {patch}, build = 0, "
        f'display = "{version}" }}'
    )
    info = INFO_LUA.read_text(encoding="utf-8")
    updated, count = PATTERN.subn(replacement, info, count=1)
    if count != 1:
        print(f"No VERSION table found in {INFO_LUA}", file=sys.stderr)
        return 1
    if updated != info:
        INFO_LUA.write_text(updated, encoding="utf-8")
        print(f"Info.lua set to {version}")
    else:
        print(f"Info.lua already at {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
