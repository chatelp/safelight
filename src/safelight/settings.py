"""User settings of the hub, stored in settings.json next to hub.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIRM_THRESHOLD = 50


@dataclass
class Settings:
    # Changes touching more photos than this need the user's confirmation.
    confirm_threshold: int = DEFAULT_CONFIRM_THRESHOLD
    # When true, every command that changes the catalog is refused.
    read_only: bool = False

    @classmethod
    def load(cls, path: Path) -> Settings:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        settings = cls()
        settings.update(data if isinstance(data, dict) else {}, strict=False)
        return settings

    def update(self, changes: dict[str, Any], *, strict: bool = True) -> None:
        """Apply validated changes. With strict, invalid values raise ValueError."""
        for key, value in changes.items():
            try:
                if key == "confirm_threshold":
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        raise ValueError("confirm_threshold must be a non-negative integer")
                    self.confirm_threshold = value
                elif key == "read_only":
                    if not isinstance(value, bool):
                        raise ValueError("read_only must be true or false")
                    self.read_only = value
                elif strict:
                    raise ValueError(f"unknown setting: {key}")
            except ValueError:
                if strict:
                    raise

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
