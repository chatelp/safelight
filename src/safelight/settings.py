"""User settings of the hub, stored in settings.json next to hub.json.

They hold the user's stable choices, edited in the menu bar app (or `safelight settings`),
so that every AI client follows them without being told.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIRM_THRESHOLD = 50
LABELS = ("red", "yellow", "green", "blue", "purple", "none")
DEFAULT_CHANGE_LABEL = "purple"
MAX_LENS_NAMES = 50
MAX_NAME_LENGTH = 120


@dataclass
class Settings:
    # Changes touching more photos than this need the user's confirmation.
    confirm_threshold: int = DEFAULT_CONFIRM_THRESHOLD
    # When true, every command that changes the catalog is refused.
    read_only: bool = False
    # Color label Safelight gives the photos it changes, so the user can find and check
    # them ("none": no label). A photo that already has another label keeps it.
    change_label: str = DEFAULT_CHANGE_LABEL
    # Whether Safelight saves a snapshot before changing a photo's geometry.
    snapshots: bool = True
    # Lenses Lightroom names wrongly (adapted or manual lenses): what Lightroom reads, and
    # the real name. Safelight shows the real name and searches with either.
    lens_names: list[dict[str, str]] = field(default_factory=list)

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
        """Apply validated changes. With strict, an invalid value raises ValueError and
        nothing changes; without, invalid values are skipped (reading an old file)."""
        result = copy.deepcopy(self)
        for key, value in changes.items():
            try:
                result._set(key, value)
            except ValueError:
                if strict:
                    raise
        self.__dict__.update(result.__dict__)

    def _set(self, key: str, value: Any) -> None:
        if key == "confirm_threshold":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("confirm_threshold must be a non-negative integer")
            self.confirm_threshold = value
        elif key == "read_only":
            if not isinstance(value, bool):
                raise ValueError("read_only must be true or false")
            self.read_only = value
        elif key == "change_label":
            if value not in LABELS:
                raise ValueError(f"change_label must be one of {', '.join(LABELS)}")
            self.change_label = value
        elif key == "snapshots":
            if not isinstance(value, bool):
                raise ValueError("snapshots must be true or false")
            self.snapshots = value
        elif key == "lens_names":
            self.lens_names = _lens_names(value)
        else:
            raise ValueError(f"unknown setting: {key}")

    def real_lens(self, lightroom_name: str | None) -> str | None:
        """The real name of a lens Lightroom names `lightroom_name`, if the user gave one."""
        for pair in self.lens_names:
            if pair["lightroom"] == lightroom_name:
                return pair["real"]
        return None

    def lightroom_lens(self, name: str) -> str:
        """The name Lightroom uses for the lens the user calls `name` (real name, exact or
        a unique part of it, any case); `name` itself when it is not a known real name."""
        wanted = name.casefold().strip()
        exact = [p["lightroom"] for p in self.lens_names if p["real"].casefold() == wanted]
        if exact:
            return exact[0]
        partial = {p["lightroom"] for p in self.lens_names if wanted in p["real"].casefold()}
        return partial.pop() if len(partial) == 1 else name

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lens_names(value: Any) -> list[dict[str, str]]:
    message = 'lens_names must be a list of {"lightroom": name, "real": name}'
    if not isinstance(value, list) or len(value) > MAX_LENS_NAMES:
        raise ValueError(f"{message}, at most {MAX_LENS_NAMES}")
    pairs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"lightroom", "real"}:
            raise ValueError(message)
        lightroom, real = item["lightroom"], item["real"]
        if not all(
            isinstance(n, str) and 0 < len(n.strip()) <= MAX_NAME_LENGTH for n in (lightroom, real)
        ):
            raise ValueError(f"lens names must be 1 to {MAX_NAME_LENGTH} characters")
        if any(p["lightroom"] == lightroom.strip() for p in pairs):
            raise ValueError(f"{lightroom!r} is given twice")
        pairs.append({"lightroom": lightroom.strip(), "real": real.strip()})
    return pairs
