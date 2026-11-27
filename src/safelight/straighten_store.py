"""What straighten_photos found on each photo, kept so the AI client can choose the guides.

For every photo it straightens, Safelight keeps the preview it analysed (rendered without
any transform), the straight lines it found there and the correction it wrote, in
`<Safelight home>/straighten/`. straighten_lines draws those lines, numbered, on that
preview; straighten_photos with `chosen_lines` makes a new correction from the numbers
chosen, without rendering the photo again. Nothing leaves the Mac; the folder can be
deleted at any time (the photos then need a new automatic run before choosing lines).
The folder is readable by the user only, and what is older than `KEEP_DAYS` is removed.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .contact_sheet import _font
from .paths import safelight_home
from .upright import ANALYSIS_PX, Line, Plan, geometry_settings

# Settings that move the lines of the image before Upright: if they change, the lines kept
# no longer match the photo.
LENS_KEYS = (
    "orientation",
    "LensProfileEnable",
    "LensManualDistortionAmount",
    "LensProfileDistortionScale",
)

VERTICAL_COLOR = (0, 200, 255)
HORIZONTAL_COLOR = (255, 170, 0)
GUIDE_COLOR = (255, 60, 200)
KEEP_DAYS = 90


def store_dir(home: Path | None = None) -> Path:
    return (home or safelight_home()) / "straighten"


def _name(photo_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", photo_id)


@dataclass
class Saved:
    photo_id: str
    width: int
    height: int
    focal_px: float
    lines: list[Line]
    guide_lines: list[int]
    matrix: list[float]  # the UprightTransform_5 Safelight wrote, row-major
    lens: dict[str, Any]
    auto_guide_lines: list[int]  # the guides of the automatic run
    auto_matrix: list[float]
    jpeg: bytes
    # The transform, Upright and crop before Safelight's first correction (None for photos
    # straightened by an older Safelight), to put the photo back as it was.
    before: dict[str, Any] | None = None

    def lens_changed(self, settings: dict[str, Any]) -> bool:
        return any(settings.get(key) != self.lens.get(key) for key in LENS_KEYS)

    def is_current(self, settings: dict[str, Any]) -> bool:
        """Whether the photo still has the correction Safelight wrote (nobody moved it)."""
        if settings.get("PerspectiveUpright") != 5:
            return False
        try:
            current = [float(v) for v in str(settings.get("UprightTransform_5")).split(",")]
        except ValueError:
            return False
        return len(current) == 9 and bool(np.allclose(current, self.matrix, atol=1e-6))

    @property
    def auto_verticals(self) -> list[tuple[float, float, float, float]]:
        return [self.lines[i].segment for i in self.auto_guide_lines if self.lines[i].vertical]


def _small_jpeg(jpeg: bytes) -> bytes:
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    image.thumbnail((ANALYSIS_PX, ANALYSIS_PX))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def save(
    photo_id: str,
    plan: Plan,
    settings: dict[str, Any],
    jpeg: bytes | None = None,
    home: Path | None = None,
) -> None:
    """Keep what was found and written. Without `jpeg`, a new choice of guides: the
    preview, lines and automatic guides already kept stay."""
    folder = store_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)  # previews can show people
    _forget_old(folder)
    matrix = [float(v) for v in plan.matrix.flatten()]
    data_path = folder / f"{_name(photo_id)}.json"
    if jpeg is None:
        data = json.loads(data_path.read_text())
        data.update(guide_lines=plan.guide_lines, matrix=matrix)
    else:
        (folder / f"{_name(photo_id)}.jpg").write_bytes(_small_jpeg(jpeg))
        data = {
            "photo_id": photo_id,
            "width": plan.width,
            "height": plan.height,
            "focal_px": plan.focal_px,
            "lines": [
                {"segment": list(line.segment), "length": line.length, "angle": line.angle}
                for line in plan.lines
            ],
            "guide_lines": plan.guide_lines,
            "matrix": matrix,
            "lens": {key: settings.get(key) for key in LENS_KEYS},
            "auto_guide_lines": plan.guide_lines,
            "auto_matrix": matrix,
            "before": geometry_settings(settings),
        }
    data_path.write_text(json.dumps(data))


def _forget_old(folder: Path) -> None:
    limit = time.time() - KEEP_DAYS * 86400
    for path in folder.iterdir():
        try:
            if path.suffix in (".json", ".jpg") and path.stat().st_mtime < limit:
                path.unlink()
        except OSError:
            continue


def load(photo_id: str, home: Path | None = None) -> Saved | None:
    folder = store_dir(home)
    try:
        data = json.loads((folder / f"{_name(photo_id)}.json").read_text())
        jpeg = (folder / f"{_name(photo_id)}.jpg").read_bytes()
    except (OSError, ValueError):
        return None
    lines = [Line(tuple(d["segment"]), d["length"], d["angle"]) for d in data["lines"]]
    return Saved(
        photo_id=data["photo_id"],
        width=int(data["width"]),
        height=int(data["height"]),
        focal_px=float(data["focal_px"]),
        lines=lines,
        guide_lines=list(data["guide_lines"]),
        matrix=list(data["matrix"]),
        lens=dict(data["lens"]),
        auto_guide_lines=list(data["auto_guide_lines"]),
        auto_matrix=list(data["auto_matrix"]),
        jpeg=jpeg,
        before=dict(data["before"]) if isinstance(data.get("before"), dict) else None,
    )


def describe(saved: Saved) -> list[dict[str, Any]]:
    """The numbered lines, for the AI client."""
    out = []
    for index, line in enumerate(saved.lines):
        x, y = line.center
        out.append(
            {
                "number": index + 1,
                "kind": "vertical" if line.vertical else "horizontal",
                "length_pct": round(100 * line.length),
                "angle_deg": round(line.angle, 2),
                "at_pct": round(100 * (x if line.vertical else y)),
                **({"guide": True} if index in saved.guide_lines else {}),
            }
        )
    return out


def draw(saved: Saved, size: int = 1200) -> bytes:
    """JPEG of the preview without transform, with every line drawn and numbered.
    Guides now in use are drawn thicker, in magenta."""
    image = Image.open(io.BytesIO(saved.jpeg)).convert("RGB")
    image.thumbnail((size, size))
    w, h = image.size
    # Dim the photo a little so that the lines and numbers stand out.
    image = Image.blend(image, Image.new("RGB", image.size, (0, 0, 0)), 0.25)
    pen = ImageDraw.Draw(image)
    font = _font(max(12, w // 60))
    stroke = max(2, w // 400)
    for index, line in enumerate(saved.lines):
        x1, y1, x2, y2 = line.segment
        guide = index in saved.guide_lines
        color = GUIDE_COLOR if guide else VERTICAL_COLOR if line.vertical else HORIZONTAL_COLOR
        pen.line((x1 * w, y1 * h, x2 * w, y2 * h), fill=color, width=stroke * (2 if guide else 1))
    for index, line in enumerate(saved.lines):
        x1, y1, x2, y2 = line.segment
        guide = index in saved.guide_lines
        color = GUIDE_COLOR if guide else VERTICAL_COLOR if line.vertical else HORIZONTAL_COLOR
        # Numbers sit at different places along the lines so neighbours do not overlap.
        t = (0.2, 0.5, 0.8)[index % 3]
        x, y = (x1 + t * (x2 - x1)) * w, (y1 + t * (y2 - y1)) * h
        label = str(index + 1)
        box = pen.textbbox((0, 0), label, font=font)
        bw, bh = box[2] - box[0] + 6, box[3] - box[1] + 6
        offset = (bw * 0.6, 0) if line.vertical else (0, -bh * 0.6)
        left = min(max(x + offset[0] - bw / 2, 0), w - bw)
        top = min(max(y + offset[1] - bh / 2, 0), h - bh)
        pen.rectangle((left, top, left + bw, top + bh), fill=(0, 0, 0), outline=color)
        pen.text((left + 3 - box[0], top + 3 - box[1]), label, fill=color, font=font)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()
