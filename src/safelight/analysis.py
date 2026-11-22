"""Image checks on Lightroom previews: focus, exposure, tilt, bursts and duplicates.

Everything works on the JPEG previews Lightroom renders (with the user's edits), so the
results describe what the photographer sees. The measures are heuristics meant to sort
and shortlist, not to judge: the AI should look at the photos it is unsure about
(contact_sheet, preview_photo) before acting.
"""

from __future__ import annotations

import io
import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

ANALYSIS_SIZE = 512  # long edge of the previews analyzed, in pixels
# How much more peaked the best edge profile must be than a typical angle's to count as a
# real straight line. Horizontals need more: in interiors, receding lines in perspective
# give weak, misleading peaks (calibrated on 50 wide-angle interiors).
MIN_VERTICAL_STRENGTH = 1.6
STRONG_VERTICAL_STRENGTH = 2.2
MIN_HORIZON_STRENGTH = 2.5


@dataclass
class ImageStats:
    focus: float
    """Strength of the sharpest details (99th percentile of the Laplacian). Higher is
    sharper. Based on the sharpest areas, so a sharp subject on a blurred background still
    scores high."""
    brightness: float
    """Mean luminance, 0 (black) to 1 (white)."""
    clipped_highlights: float
    """Share of pixels at pure white."""
    clipped_shadows: float
    """Share of pixels at pure black."""
    tilt_deg: float | None
    """Estimated rotation of the dominant horizontal or vertical lines, in degrees;
    positive means the scene is turned counterclockwise. None when no clear line."""
    dhash: int
    """64-bit difference hash, for near-duplicate detection."""
    blank_edges: tuple[str, ...] = ()
    """Edges with an empty (white or black) band, typically left by a perspective
    correction without "Constrain Crop": top, bottom, left, right."""


def _gray(jpeg: bytes) -> np.ndarray:
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(jpeg))).convert("L")
    image.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE))
    return np.asarray(image, dtype=np.float32)


def _dhash(gray: np.ndarray) -> int:
    small = Image.fromarray(gray.astype(np.uint8)).resize((9, 8), Image.Resampling.LANCZOS)
    pixels = np.asarray(small, dtype=np.int16)
    bits = (pixels[:, 1:] > pixels[:, :-1]).flatten()
    return int("".join("1" if bit else "0" for bit in bits), 2)


def _focus(gray: np.ndarray) -> float:
    laplacian = (
        -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    )
    return float(np.percentile(np.abs(laplacian), 99))


def _tilt(gray: np.ndarray) -> float | None:
    """Rotation that best aligns strong edges with the image axes, in degrees.

    Projection-profile method: rotate the edge map by candidate angles and keep the angle
    whose row and column sums are the most peaked (straight horizon, verticals). Positive
    means the scene is turned counterclockwise.
    """
    image = Image.fromarray(gray.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0))
    image.thumbnail((320, 320))
    smooth = np.asarray(image, dtype=np.float32)
    gx = np.zeros_like(smooth)
    gy = np.zeros_like(smooth)
    gx[:, 1:-1] = smooth[:, 2:] - smooth[:, :-2]
    gy[1:-1, :] = smooth[2:, :] - smooth[:-2, :]
    magnitude = np.hypot(gx, gy)
    if magnitude.max() <= 4:
        return None  # flat image
    # Keep the strongest edges; on mostly flat images (sky over sea) the percentile is 0.
    threshold = max(float(np.percentile(magnitude, 92)), 4.0)
    edges = Image.fromarray(np.clip(magnitude * (magnitude > threshold), 0, 255).astype(np.uint8))

    cache: dict[float, tuple[float, float]] = {}

    def profiles(angle: float) -> tuple[float, float]:
        key = round(angle, 2)
        if key not in cache:
            cache[key] = _profiles(key)
        return cache[key]

    def _profiles(angle: float) -> tuple[float, float]:
        rotated = np.asarray(
            edges.rotate(angle, resample=Image.Resampling.BILINEAR), dtype=np.float32
        )
        # Ignore the borders, where rotation brings in empty corners.
        h, w = rotated.shape
        core = rotated[h // 8 : -h // 8, w // 8 : -w // 8]
        return float(core.sum(axis=0).var()), float(core.sum(axis=1).var())

    def best_angle(index: int) -> tuple[float, float] | None:
        """Best angle for verticals (index 0) or horizontals (index 1), and its strength."""
        coarse = np.arange(-10.0, 10.01, 0.5)
        scores = np.array([profiles(a)[index] for a in coarse])
        baseline = float(np.median(scores))
        if baseline <= 0:
            return None
        center = float(coarse[int(np.argmax(scores))])
        fine = np.arange(center - 0.5, center + 0.51, 0.1)
        fine_scores = np.array([profiles(a)[index] for a in fine])
        return float(fine[int(np.argmax(fine_scores))]), float(fine_scores.max()) / baseline

    # In architecture and interiors, verticals stay vertical while horizontal lines recede
    # in perspective, so clear verticals win; then a clear horizon; then weaker verticals.
    verticals, horizontals = best_angle(0), best_angle(1)
    for found, minimum in (
        (verticals, STRONG_VERTICAL_STRENGTH),
        (horizontals, MIN_HORIZON_STRENGTH),
        (verticals, MIN_VERTICAL_STRENGTH),
    ):
        if found is not None and found[1] >= minimum:
            # Rotating by this angle levels the scene, so the scene is tilted the other way.
            return -found[0]
    return None


def _blank_edges(gray: np.ndarray) -> tuple[str, ...]:
    """Edges where the outermost pixels are flat white (or black) but the picture just
    inside is not: the empty wedge a perspective or rotation leaves without a crop."""
    found = []
    sides = {
        "top": (gray[0, :], gray[6, :]),
        "bottom": (gray[-1, :], gray[-7, :]),
        "left": (gray[:, 0], gray[:, 6]),
        "right": (gray[:, -1], gray[:, -7]),
    }
    for name, (edge, inside) in sides.items():
        white = (edge >= 245) & (inside < edge - 40)
        black = (edge <= 3) & (inside > edge + 40)
        if max(white.mean(), black.mean()) >= 0.25:
            found.append(name)
    return tuple(found)


def measure(jpeg: bytes, *, with_tilt: bool = True) -> ImageStats:
    gray = _gray(jpeg)
    tilt = _tilt(gray) if with_tilt else None
    return ImageStats(
        focus=_focus(gray),
        brightness=float(gray.mean() / 255.0),
        clipped_highlights=float((gray >= 250).mean()),
        clipped_shadows=float((gray <= 4).mean()),
        tilt_deg=None if tilt is None else round(tilt, 1) + 0.0,
        dhash=_dhash(gray),
        blank_edges=_blank_edges(gray),
    )


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


# -- per-photo findings ---------------------------------------------------------------------

BLUR_RELATIVE = 0.45  # below this share of the set's median focus: probably blurred
BLUR_ABSOLUTE = 18.0  # used for small sets (fewer than 5 photos)
TILT_MIN_DEG = 1.0
TILT_MAX_DEG = 8.0


@dataclass
class PhotoReport:
    photo_id: str
    file_name: str | None
    focus: float
    brightness: float
    clipped_highlights: float
    clipped_shadows: float
    tilt_deg: float | None
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "photo_id": self.photo_id,
            "file_name": self.file_name,
            "focus": round(self.focus, 1),
            "brightness": round(self.brightness, 3),
            "clipped_highlights_pct": round(self.clipped_highlights * 100, 2),
            "clipped_shadows_pct": round(self.clipped_shadows * 100, 2),
            "tilt_deg": None if self.tilt_deg is None else round(self.tilt_deg, 1),
            "issues": self.issues,
        }


def findings(
    photos: list[dict[str, Any]], stats: dict[str, ImageStats], checks: set[str]
) -> list[PhotoReport]:
    focus_values = [s.focus for s in stats.values()]
    median_focus = float(np.median(focus_values)) if focus_values else 0.0
    reports = []
    for photo in photos:
        s = stats.get(photo["uuid"])
        if s is None:
            continue
        report = PhotoReport(
            photo_id=photo["uuid"],
            file_name=photo.get("file_name"),
            focus=s.focus,
            brightness=s.brightness,
            clipped_highlights=s.clipped_highlights,
            clipped_shadows=s.clipped_shadows,
            tilt_deg=s.tilt_deg,
        )
        if "sharpness" in checks:
            if len(focus_values) >= 5:
                if s.focus < BLUR_RELATIVE * median_focus:
                    report.issues.append("possibly blurred (much softer than the others)")
            elif s.focus < BLUR_ABSOLUTE:
                report.issues.append("possibly blurred")
        if "exposure" in checks:
            if s.clipped_highlights > 0.02:
                report.issues.append(
                    f"blown highlights ({s.clipped_highlights * 100:.1f}% pure white)"
                )
            if s.clipped_shadows > 0.08:
                report.issues.append(f"crushed shadows ({s.clipped_shadows * 100:.1f}% pure black)")
            if s.brightness > 0.78:
                report.issues.append("very bright overall")
            elif s.brightness < 0.12:
                report.issues.append("very dark overall")
        if "horizon" in checks and s.blank_edges:
            report.issues.append(
                "empty border along the "
                + ", ".join(s.blank_edges)
                + " edge (perspective or rotation without a constrained crop)"
            )
        if (
            "horizon" in checks
            and s.tilt_deg is not None
            and TILT_MIN_DEG <= abs(s.tilt_deg) <= TILT_MAX_DEG
        ):
            direction = "counterclockwise" if s.tilt_deg > 0 else "clockwise"
            report.issues.append(f"tilted about {abs(s.tilt_deg):.1f}° {direction}")
        reports.append(report)
    return reports


# -- bursts and duplicates ------------------------------------------------------------------

BURST_HASH_DISTANCE = 22  # frames of one burst still look alike
DUPLICATE_HASH_DISTANCE = 6  # near-identical pictures, whenever they were taken


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _timestamp(photo: dict[str, Any]) -> float | None:
    moment = parse_time(photo.get("captured_at"))
    if moment is None:
        return None
    # Naive times (no zone) are compared with each other only; fine within one shoot.
    return moment.timestamp() if moment.tzinfo else moment.replace(tzinfo=None).timestamp()


def groups(
    photos: list[dict[str, Any]], stats: dict[str, ImageStats], max_gap_s: float = 2.0
) -> list[dict[str, Any]]:
    """Bursts (frames shot in quick succession that look alike) and near-duplicates."""
    ids = [p["uuid"] for p in photos if p["uuid"] in stats]
    by_id = {p["uuid"]: p for p in photos}
    parent = {photo_id: photo_id for photo_id in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    timed = sorted(
        (t, photo_id) for photo_id in ids if (t := _timestamp(by_id[photo_id])) is not None
    )
    burst_ids: set[str] = set()
    for (t1, a), (t2, b) in itertools.pairwise(timed):
        same_camera = by_id[a].get("camera") == by_id[b].get("camera")
        if (
            t2 - t1 <= max_gap_s
            and same_camera
            and hamming(stats[a].dhash, stats[b].dhash) <= BURST_HASH_DISTANCE
        ):
            union(a, b)
            burst_ids.update((a, b))
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if hamming(stats[a].dhash, stats[b].dhash) <= DUPLICATE_HASH_DISTANCE:
                union(a, b)

    members: dict[str, list[str]] = {}
    for photo_id in ids:
        members.setdefault(find(photo_id), []).append(photo_id)

    out = []
    for group in members.values():
        if len(group) < 2:
            continue
        ranked = sorted(group, key=lambda pid: _keeper_score(by_id[pid], stats[pid], group, stats))
        best = ranked[-1]
        out.append(
            {
                "kind": "burst" if any(pid in burst_ids for pid in group) else "duplicates",
                "photo_ids": sorted(group, key=lambda pid: _timestamp(by_id[pid]) or 0),
                "best_photo_id": best,
                "why_best": _why(best, group, stats),
            }
        )
    out.sort(key=lambda g: -len(g["photo_ids"]))
    return out


def _keeper_score(
    photo: dict[str, Any], s: ImageStats, group: list[str], stats: dict[str, ImageStats]
) -> float:
    top = max(stats[pid].focus for pid in group) or 1.0
    score = s.focus / top  # sharpness relative to the group, 0..1
    score -= 4 * max(0.0, s.clipped_highlights - 0.01)
    score -= 2 * max(0.0, s.clipped_shadows - 0.05)
    score += 0.05 * int(photo.get("rating") or 0)
    if photo.get("pick") == 1:
        score += 0.2
    elif photo.get("pick") == -1:
        score -= 0.5
    return score


def _why(best: str, group: list[str], stats: dict[str, ImageStats]) -> str:
    others = [stats[pid].focus for pid in group if pid != best]
    if others and stats[best].focus >= max(others):
        return "sharpest frame of the group"
    return "best balance of sharpness, exposure, rating and flag"


ISSUE_KINDS = {
    "blurred": "possibly blurred",
    "highlights": "blown highlights",
    "shadows": "crushed shadows",
    "very bright": "very bright",
    "very dark": "very dark",
    "tilted": "tilted",
    "border": "empty border",
}


def summarize(reports: list[PhotoReport], grouped: list[dict[str, Any]]) -> dict[str, Any]:
    flagged = [r for r in reports if r.issues]
    in_groups = sum(len(g["photo_ids"]) for g in grouped)
    counts: dict[str, int] = {}
    for report in flagged:
        for issue in report.issues:
            for marker, label in ISSUE_KINDS.items():
                if marker in issue:
                    counts[label] = counts.get(label, 0) + 1
                    break
    return {
        "issue_counts": counts,
        "photos_analyzed": len(reports),
        "photos_with_issues": len(flagged),
        "groups": len(grouped),
        "photos_in_groups": in_groups,
        "could_set_aside": in_groups - len(grouped),
    }


def angle_from_tilt(tilt_deg: float) -> float:
    """Lightroom's CropAngle that levels a photo tilted by `tilt_deg` (counterclockwise +)."""
    return -tilt_deg if math.isfinite(tilt_deg) else 0.0
