"""Straighten architecture and exhibition photos with Lightroom's Guided Upright.

Lightroom stores a Guided Upright correction as up to four guide segments
(`UprightFourSegments_N`, "x1,y1,x2,y2" in 0..1 image coordinates) and the transform it
computed from them (`UprightTransform_5`, a 3x3 matrix, row-major, that maps 0..1 image
coordinates to corrected ones). Lightroom only computes that matrix when guides are drawn
by hand, so Safelight computes it itself and writes both.

The correction is modelled as a rotation of the camera (H = K R K^-1): straight lines stay
straight and flat things keep their proportions, so prints and walls are not stretched.
Lines are found on a preview rendered without any transform:

- near-vertical lines (within `MAX_VERTICAL_DEG`) set pitch and roll. The verticals kept
  are the largest group that one correction straightens (lines inside a print, or a
  ceiling rail in perspective, disagree with it and are dropped);
- near-horizontal lines (within `MAX_FRONTAL_DEG`) belong to surfaces facing the camera
  (prints, frames) and set yaw, unless they would turn the view by more than
  `MAX_YAW_DEG` (a print hung at an angle, a corridor): then only verticals are used.

Up to four of the lines kept become the guides: the longest vertical on each side and
the longest horizontal at the top and at the bottom. The matrix is fitted to those
guides.

With two vertical and two horizontal guides (a view facing a wall), the correction is
made exact: the four guides come out exactly vertical and horizontal, like Lightroom's
own Guided Upright, and the remaining freedom (scale and position along each axis) is
taken from the rotation, so proportions stay those of the camera rotation. A rotation
alone often leaves a few tenths of a degree on such views, more with a shifted
tilt-shift lens (the rotation assumes the optical centre is the image centre). When the exact
correction moves the image too far from the rotation, a guide is probably wrong and the
rotation is kept.

The AI client can also choose the guides itself among the numbered lines found
(`plan_from_lines`). Moving a guide in Lightroom replaces the matrix with Lightroom's
own (see docs/LIGHTROOM_API.md).
"""

from __future__ import annotations

import io
import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

DETECT_MAX_DEG = 12.0  # lines searched within this angle of vertical or horizontal
MAX_VERTICAL_DEG = 8.0  # verticals converge with wide lenses, rarely more than this
MAX_FRONTAL_DEG = 4.0  # horizontals of surfaces facing the camera
MAX_YAW_DEG = 5.0  # horizontals asking to turn the view more than this are not frontal
MIN_LENGTH = 0.12  # fraction of the image height (verticals) or width (horizontals)
INLIER_DEG = 0.6  # lines further than this from the fitted correction are dropped
REVIEW_DEG = 0.3  # guides straightened less well than this deserve a look
MAX_CORRECTION_DEG = 15.0  # larger rotations are almost always a detection error
MAX_EXACT_SHIFT = 0.015  # exact correction: furthest a corner may move from the rotation
MIN_VERTICAL_SPREAD = 0.15  # chosen verticals at least this far apart (of the width)
DISAGREE_DEG = 0.45  # chosen verticals this far from the automatic ones: let the user decide
EDGE_MARGIN = 0.01  # lines hugging the image border are not lines of the scene
ANALYSIS_PX = 1600

# Transform settings put back to neutral before the Upright correction is applied, so
# that older manual corrections do not add up with it. The crop goes back to the full
# image: the corrected photo usually needs a new crop anyway.
RESET_SETTINGS: dict[str, Any] = {
    "PerspectiveUpright": 0,
    "PerspectiveVertical": 0,
    "PerspectiveHorizontal": 0,
    "PerspectiveRotate": 0,
    "PerspectiveAspect": 0,
    "PerspectiveScale": 100,
    "PerspectiveX": 0,
    "PerspectiveY": 0,
    "UprightFourSegmentsCount": 0,
    "CropLeft": 0,
    "CropTop": 0,
    "CropRight": 1,
    "CropBottom": 1,
    "CropAngle": 0,
}

# Where Lightroom keeps an Upright correction, besides the settings above.
UPRIGHT_PREFIXES = ("UprightFourSegments_", "UprightTransform_")


def geometry_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """The transform, Upright and crop of a photo, to put them back as they were."""
    return {
        **RESET_SETTINGS,
        **{
            key: value
            for key, value in settings.items()
            if key in RESET_SETTINGS or key.startswith(UPRIGHT_PREFIXES)
        },
    }


@dataclass
class Line:
    segment: tuple[float, float, float, float]  # x1, y1, x2, y2 in 0..1
    length: float  # fraction of the image height (vertical) or width (horizontal)
    angle: float  # degrees from vertical (or horizontal)

    @property
    def vertical(self) -> bool:
        x1, y1, x2, y2 = self.segment
        return abs(y2 - y1) > abs(x2 - x1)

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.segment
        return (x1 + x2) / 2, (y1 + y2) / 2


@dataclass
class Plan:
    """The Guided Upright correction of one photo."""

    guides: list[tuple[float, float, float, float]]
    matrix: np.ndarray
    pitch_deg: float
    yaw_deg: float
    roll_deg: float
    guide_error_deg: float  # worst guide after correction
    lines_found: int
    lines_used: int
    warnings: list[str] = field(default_factory=list)
    model: str = "rotation"  # or "exact": see the module docstring
    lines: list[Line] = field(default_factory=list)  # every line found; numbered from 1
    guide_lines: list[int] = field(default_factory=list)  # indexes in `lines` of the guides
    width: int = 0  # the photo's size and focal length (pixels) the plan was made for
    height: int = 0
    focal_px: float = 0.0

    def settings(self) -> dict[str, Any]:
        """Develop settings that apply this correction."""
        out: dict[str, Any] = {
            **RESET_SETTINGS,
            "PerspectiveUpright": 5,
            "UprightFourSegmentsCount": len(self.guides),
            "UprightTransform_5": ",".join(f"{v:.9f}" for v in self.matrix.flatten()),
        }
        for index, guide in enumerate(self.guides):
            out[f"UprightFourSegments_{index}"] = ",".join(f"{v:.9f}" for v in guide)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "pitch_deg": round(self.pitch_deg, 2),
            "yaw_deg": round(self.yaw_deg, 2),
            "roll_deg": round(self.roll_deg, 2),
            "guides": len(self.guides),
            "guide_error_deg": round(self.guide_error_deg, 3),
            "lines_found": self.lines_found,
            "lines_used": self.lines_used,
            **({"warnings": self.warnings} if self.warnings else {}),
        }


# -- line detection -----------------------------------------------------------------------


def _sobel(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = np.pad(gray, 1, mode="edge")
    gx = 2 * (p[1:-1, 2:] - p[1:-1, :-2]) + (p[:-2, 2:] - p[:-2, :-2]) + (p[2:, 2:] - p[2:, :-2])
    gy = 2 * (p[2:, 1:-1] - p[:-2, 1:-1]) + (p[2:, :-2] - p[:-2, :-2]) + (p[2:, 2:] - p[:-2, 2:])
    return gx, gy


def _lines_along_y(gray: np.ndarray, max_angle: float, max_lines: int = 14) -> list[Line]:
    """Long straight lines within max_angle of the vertical axis of `gray`."""
    h, w = gray.shape
    gx, gy = _sobel(gray)
    mag = np.hypot(gx, gy)
    threshold = max(float(np.percentile(mag, 92)), 20.0)
    across = np.degrees(np.arctan2(np.abs(gy), np.abs(gx))) < max_angle + 3
    ys, xs = np.nonzero((mag > threshold) & across)
    if len(xs) < 20:
        return []
    weights = mag[ys, xs]
    dy = ys - h / 2.0
    used = np.zeros(len(xs), bool)
    found: list[Line] = []
    angles = np.arange(-max_angle, max_angle + 1e-9, 0.2)
    for _ in range(max_lines * 2):
        live = np.where(used, 0.0, weights)
        best = (0.0, 0.0, 0.0)
        for angle in angles:
            pos = np.round(xs - dy * math.tan(math.radians(angle))).astype(int) + w
            hist = np.bincount(np.clip(pos, 0, 3 * w), weights=live, minlength=3 * w + 1)
            i = int(np.argmax(hist))
            if hist[i] > best[0]:
                best = (float(hist[i]), float(angle), float(i - w))
        if best[0] <= 0:
            break
        _, angle, offset = best
        near = ~used & (np.abs(xs - dy * math.tan(math.radians(angle)) - offset) < 2.0)
        if near.sum() < 20:
            used |= near
            continue
        k, b = np.polyfit(dy[near], xs[near], 1)
        near = ~used & (np.abs(xs - (k * dy + b)) < 1.5)
        used |= np.abs(xs - (k * dy + b)) < 3.0
        if near.sum() < 20:
            continue
        k, b = np.polyfit(dy[near], xs[near], 1)
        rows = np.unique(ys[near])
        gaps = np.nonzero(np.diff(rows) > max(6, 0.01 * h))[0]
        starts = np.concatenate([[0], gaps + 1])
        ends = np.concatenate([gaps, [len(rows) - 1]])
        j = int(np.argmax(rows[ends] - rows[starts]))
        y0, y1 = float(rows[starts[j]]), float(rows[ends[j]])
        length = (y1 - y0) / h
        if length < MIN_LENGTH:
            continue
        x0 = k * (y0 - h / 2.0) + b
        x1 = k * (y1 - h / 2.0) + b
        segment = (float(x0 / w), float(y0 / h), float(x1 / w), float(y1 / h))
        found.append(Line(segment, float(length), math.degrees(math.atan(k))))
        if len(found) >= max_lines:
            break
    return found


def find_lines(image: Image.Image) -> list[Line]:
    """Near-vertical and near-horizontal straight lines of a photo."""
    gray = np.asarray(image.convert("L"), dtype=float)
    lines = _lines_along_y(gray, DETECT_MAX_DEG)
    for line in _lines_along_y(gray.T, DETECT_MAX_DEG):
        y1, x1, y2, x2 = line.segment
        lines.append(Line((x1, y1, x2, y2), line.length, line.angle))
    return [line for line in lines if not _on_border(line)]


def _on_border(line: Line) -> bool:
    x1, y1, x2, y2 = line.segment
    if line.vertical:
        return min(x1, x2) < EDGE_MARGIN or max(x1, x2) > 1 - EDGE_MARGIN
    return min(y1, y2) < EDGE_MARGIN or max(y1, y2) > 1 - EDGE_MARGIN


# -- camera rotation model ----------------------------------------------------------------


def _rotation(pitch: float, yaw: float, roll: float) -> np.ndarray:
    cx, sx = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cz, sz = math.cos(roll), math.sin(roll)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ rx @ ry


def _project(matrix: np.ndarray, x: float, y: float) -> np.ndarray:
    q = matrix @ np.array([x, y, 1.0])
    return q[:2] / q[2]


def homography(
    pitch: float, yaw: float, roll: float, width: int, height: int, focal_px: float
) -> np.ndarray:
    """Matrix in Lightroom's convention (0..1 coordinates) for a camera rotation (radians).

    The centre of the image stays in place and keeps its scale, like Lightroom's own
    Upright matrices."""
    to_pixels = np.array([[width, 0, -width / 2], [0, height, -height / 2], [0, 0, 1.0]])
    k = np.diag([focal_px, focal_px, 1.0])
    m = np.linalg.inv(to_pixels) @ k @ _rotation(pitch, yaw, roll) @ np.linalg.inv(k) @ to_pixels
    c = _project(m, 0.5, 0.5)
    m = np.array([[1, 0, 0.5 - c[0]], [0, 1, 0.5 - c[1]], [0, 0, 1.0]]) @ m
    eps = 1e-4
    centre = _project(m, 0.5, 0.5)
    a = _project(m, 0.5 + eps, 0.5) - centre
    b = _project(m, 0.5, 0.5 + eps) - centre
    s = math.sqrt(abs(a[0] * b[1] - a[1] * b[0])) / eps
    m = np.array([[1 / s, 0, 0.5 - 0.5 / s], [0, 1 / s, 0.5 - 0.5 / s], [0, 0, 1.0]]) @ m
    return m / m[2, 2]


def guide_errors(
    matrix: np.ndarray, segments: list[tuple[float, float, float, float]]
) -> list[float]:
    """Angle (radians) between each corrected segment and the vertical or horizontal."""
    out = []
    for x1, y1, x2, y2 in segments:
        dx, dy = _project(matrix, x2, y2) - _project(matrix, x1, y1)
        angle = math.atan2(dx, dy) if abs(y2 - y1) > abs(x2 - x1) else -math.atan2(dy, dx)
        out.append((angle + math.pi / 2) % math.pi - math.pi / 2)
    return out


def fit_rotation(
    segments: list[tuple[float, float, float, float]],
    width: int,
    height: int,
    focal_px: float,
    start: tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    """Pitch, yaw and roll (radians) that best straighten the segments."""
    has_vertical = any(abs(s[3] - s[1]) > abs(s[2] - s[0]) for s in segments)
    has_horizontal = any(abs(s[3] - s[1]) <= abs(s[2] - s[0]) for s in segments)
    free = [has_vertical, has_horizontal, True]

    def angles(x: np.ndarray) -> list[float]:
        values = iter(x)
        return [float(next(values)) if flag else 0.0 for flag in free]

    def residuals(x: np.ndarray) -> np.ndarray:
        return np.array(guide_errors(homography(*angles(x), width, height, focal_px), segments))

    x = np.array(
        [a for a, flag in zip(start, free, strict=True) if flag] if start else [0.0] * sum(free)
    )
    cost = float(np.sum(residuals(x) ** 2))
    damping = 1e-3
    for _ in range(100):  # Levenberg-Marquardt with a numerical Jacobian
        r = residuals(x)
        jac = np.empty((len(r), len(x)))
        for j in range(len(x)):
            step = np.zeros(len(x))
            step[j] = 1e-6
            jac[:, j] = (residuals(x + step) - r) / 1e-6
        a = jac.T @ jac
        delta = np.linalg.solve(a + damping * np.diag(np.diag(a) + 1e-12), -jac.T @ r)
        trial = x + delta
        trial_cost = float(np.sum(residuals(trial) ** 2))
        if trial_cost < cost:
            x, cost, damping = trial, trial_cost, damping / 3
            if np.max(np.abs(delta)) < 1e-10:
                break
        else:
            damping *= 10
            if damping > 1e8:
                break
    pitch, yaw, roll = angles(x)
    return pitch, yaw, roll


def _is_vertical(segment: tuple[float, float, float, float]) -> bool:
    x1, y1, x2, y2 = segment
    return abs(y2 - y1) > abs(x2 - x1)


def _corners(matrix: np.ndarray) -> np.ndarray:
    return np.array([_project(matrix, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))])


def exact_homography(
    segments: list[tuple[float, float, float, float]],
    width: int,
    height: int,
    near: np.ndarray,
) -> np.ndarray | None:
    """Matrix that makes two vertical and two horizontal segments exactly vertical and
    horizontal, as close as possible to `near` (a camera rotation fitted to them).

    Such a matrix sends the meeting point of the verticals straight up, the meeting point
    of the horizontals sideways, and the line through both points to infinity. That fixes
    it up to a scale and a shift along each axis, taken from `near`. None when the
    segments are not two verticals and two horizontals, or when the result moves a corner
    of the image further than MAX_EXACT_SHIFT (of the long side) from `near`."""
    verticals = [s for s in segments if _is_vertical(s)]
    horizontals = [s for s in segments if not _is_vertical(s)]
    if len(verticals) != 2 or len(horizontals) != 2:
        return None

    def line(segment: tuple[float, float, float, float]) -> np.ndarray:
        x1, y1, x2, y2 = segment
        return np.cross([x1 * width, y1 * height, 1.0], [x2 * width, y2 * height, 1.0])

    up = np.cross(line(verticals[0]), line(verticals[1]))
    side = np.cross(line(horizontals[0]), line(horizontals[1]))
    if np.linalg.norm(up) == 0 or np.linalg.norm(side) == 0:
        return None
    up, side = up / np.linalg.norm(up), side / np.linalg.norm(side)
    horizon = np.cross(up, side)
    base = np.array([np.cross(up, horizon), np.cross(side, horizon), horizon])
    if abs(np.linalg.det(base)) < 1e-12:
        return None
    to_pixels = np.diag([float(width), float(height), 1.0])
    base = np.linalg.inv(to_pixels) @ base @ to_pixels
    # Scale and shift along each axis: least squares against `near` on a grid of points.
    grid = np.linspace(0.0, 1.0, 9)
    points = np.array([(x, y, 1.0) for x in grid for y in grid]).T
    mapped = base @ points
    if np.any(np.abs(mapped[2]) < 1e-12):
        return None
    mapped = mapped[:2] / mapped[2]
    target = near @ points
    target = target[:2] / target[2]
    fit = np.eye(3)
    for axis in (0, 1):
        design = np.column_stack([mapped[axis], np.ones(mapped.shape[1])])
        (scale, shift), *_ = np.linalg.lstsq(design, target[axis], rcond=None)
        fit[axis, axis], fit[axis, 2] = scale, shift
    matrix = fit @ base
    if abs(matrix[2, 2]) < 1e-12:
        return None
    matrix = matrix / matrix[2, 2]
    moved = np.abs((_corners(matrix) - _corners(near)) * [width, height]).max()
    if not np.isfinite(moved) or moved > MAX_EXACT_SHIFT * max(width, height):
        return None
    return matrix


def disagreement_deg(
    matrix: np.ndarray,
    verticals: list[tuple[float, float, float, float]],
    other_matrix: np.ndarray,
    other_verticals: list[tuple[float, float, float, float]],
) -> float:
    """How far two corrections are from agreeing on the verticals: the worst angle left
    on each one's verticals by the other one's matrix."""
    left = guide_errors(matrix, other_verticals) + guide_errors(other_matrix, verticals)
    return max((abs(math.degrees(e)) for e in left), default=0.0)


# -- planning -----------------------------------------------------------------------------


def focal_px(long_side: int, focal_35mm: float | None, focal_mm: float | None) -> float:
    """Focal length in pixels of the long side, from the 35 mm equivalent focal length."""
    focal = focal_35mm or focal_mm or 28.0
    return float(focal) / 36.0 * max(long_side, 1)


def _spread_pair(lines: list[Line], axis: int) -> list[Line]:
    """The two long lines furthest apart along `axis` (0: x, 1: y)."""
    if len(lines) < 2:
        return lines[:1]
    longest = max(line.length for line in lines)
    long_ones = [line for line in lines if line.length >= 0.5 * longest]
    if len(long_ones) < 2:
        long_ones = sorted(lines, key=lambda line: -line.length)[:4]
    first = min(long_ones, key=lambda line: line.center[axis])
    last = max(long_ones, key=lambda line: line.center[axis])
    return [first] if first is last else [first, last]


def _pick_guides(verticals: list[Line], horizontals: list[Line]) -> list[Line]:
    """Up to four guides for Lightroom: two verticals far apart, two horizontals far apart."""
    guides = _spread_pair(verticals, 0) + _spread_pair(horizontals, 1)
    if len(guides) < 2:  # Lightroom needs at least two guides
        rest = sorted(
            (line for line in verticals + horizontals if line not in guides),
            key=lambda line: -line.length,
        )
        guides += rest[: 2 - len(guides)]
    return guides


def _errors_deg(rotation, lines, width, height, f) -> list[float]:
    matrix = homography(*rotation, width, height, f)
    return [abs(math.degrees(e)) for e in guide_errors(matrix, [line.segment for line in lines])]


def _vertical_consensus(verticals: list[Line], width: int, height: int, f: float) -> list[Line]:
    """The largest set of verticals (by total length) that one correction straightens."""
    if len(verticals) < 2:
        return verticals
    best_score, best = -1.0, verticals[:1]
    for pair in itertools.combinations(verticals, 2):
        rotation = fit_rotation([line.segment for line in pair], width, height, f)
        if max(abs(math.degrees(a)) for a in rotation) > MAX_CORRECTION_DEG:
            continue
        errors = _errors_deg(rotation, verticals, width, height, f)
        inliers = [v for v, e in zip(verticals, errors, strict=True) if e < INLIER_DEG]
        score = sum(v.length for v in inliers)
        if score > best_score:
            best_score, best = score, inliers
    return best


def _frontal_horizontals(
    verticals: list[Line], horizontals: list[Line], width: int, height: int, f: float
) -> list[Line]:
    """Horizontals that agree with the verticals and with each other, if any."""
    best_score, best = 0.0, []
    for candidate in horizontals:
        rotation = fit_rotation(
            [line.segment for line in [*verticals, candidate]], width, height, f
        )
        if abs(math.degrees(rotation[1])) > MAX_YAW_DEG:
            continue
        errors = _errors_deg(rotation, [*verticals, *horizontals], width, height, f)
        if max(errors[: len(verticals)], default=0.0) >= INLIER_DEG:
            continue
        inliers = [
            h for h, e in zip(horizontals, errors[len(verticals) :], strict=True) if e < INLIER_DEG
        ]
        score = sum(h.length for h in inliers)
        if score > best_score:
            best_score, best = score, inliers
    return best


def plan(
    image: Image.Image,
    width: int,
    height: int,
    focal_35mm: float | None = None,
    focal_mm: float | None = None,
    frontal_horizontals: bool = True,
) -> Plan | None:
    """The Guided Upright correction for a photo rendered without transform, or None
    when it has no usable vertical line."""
    lines = find_lines(image)
    verticals = [line for line in lines if line.vertical and abs(line.angle) <= MAX_VERTICAL_DEG]
    if not verticals:
        return None
    f = focal_px(max(width, height), focal_35mm, focal_mm)
    verticals = _vertical_consensus(verticals, width, height, f)
    horizontals: list[Line] = []
    if frontal_horizontals:
        frontal = [
            line for line in lines if not line.vertical and abs(line.angle) <= MAX_FRONTAL_DEG
        ]
        horizontals = _frontal_horizontals(verticals, frontal, width, height, f)
    if len(horizontals) < 2:  # one horizontal line is too little to turn the view
        horizontals = []
    keep = verticals + horizontals
    # The correction is fitted to every line kept, which is steadier than the guides alone.
    pitch, yaw, roll = fit_rotation([line.segment for line in keep], width, height, f)
    if horizontals and abs(math.degrees(yaw)) > MAX_YAW_DEG:
        horizontals = []
        keep = verticals
        pitch, yaw, roll = fit_rotation([line.segment for line in keep], width, height, f)
    guides = _pick_guides(verticals, horizontals)
    if len(guides) < 2:
        return None
    segments = [g.segment for g in guides]
    # Then fit the guides exactly, unless that moves far from the fit on every line.
    refined = fit_rotation(segments, width, height, f, start=(pitch, yaw, roll))
    close = all(
        abs(math.degrees(a - b)) < 1 for a, b in zip(refined, (pitch, yaw, roll), strict=True)
    )
    if close and abs(math.degrees(refined[1])) <= MAX_YAW_DEG:
        pitch, yaw, roll = refined
    result = _finish(
        (pitch, yaw, roll), segments, width, height, f, lines_found=len(lines), lines_used=len(keep)
    )
    result.lines = lines
    result.guide_lines = [lines.index(g) for g in guides]
    if sum(g.vertical for g in guides) < 2:
        result.warnings.append("only one vertical line found")
    return result


def _finish(
    rotation: tuple[float, float, float],
    segments: list[tuple[float, float, float, float]],
    width: int,
    height: int,
    f: float,
    lines_found: int,
    lines_used: int,
) -> Plan:
    """The plan for a fitted rotation, made exact when the guides allow it."""
    matrix = homography(*rotation, width, height, f)
    warnings = []
    model = "rotation"
    if sum(map(_is_vertical, segments)) == 2 and len(segments) == 4:
        exact = exact_homography(segments, width, height, matrix)
        if exact is None:
            warnings.append("the four guides do not fit one view: maybe a wrong line, check it")
        else:
            matrix, model = exact, "exact"
    worst = max(abs(math.degrees(e)) for e in guide_errors(matrix, segments))
    result = Plan(
        guides=segments,
        matrix=matrix,
        pitch_deg=math.degrees(rotation[0]),
        yaw_deg=math.degrees(rotation[1]),
        roll_deg=math.degrees(rotation[2]),
        guide_error_deg=worst,
        lines_found=lines_found,
        lines_used=lines_used,
        warnings=warnings,
        model=model,
        width=width,
        height=height,
        focal_px=f,
    )
    if worst > REVIEW_DEG:
        result.warnings.append(f"guides straightened within {worst:.2f} deg only: check it")
    if max(abs(result.pitch_deg), abs(result.yaw_deg), abs(result.roll_deg)) > MAX_CORRECTION_DEG:
        result.warnings.append("very large correction: probably a wrong line, check it")
    return result


def plan_from_lines(
    lines: list[Line], chosen: list[int], width: int, height: int, f: float
) -> Plan:
    """The correction for guides chosen among `lines` (indexes). Raises ValueError when
    the choice cannot make a Guided Upright correction."""
    if not 2 <= len(chosen) <= 4:
        raise ValueError("choose two to four lines")
    if len(set(chosen)) != len(chosen) or not all(0 <= i < len(lines) for i in chosen):
        raise ValueError(f"line numbers go from 1 to {len(lines)}, each at most once")
    picked = [lines[i] for i in chosen]
    if not any(line.vertical for line in picked):
        raise ValueError("choose at least one vertical line")
    across = [line.center[0] for line in picked if line.vertical]
    if len(across) >= 2 and max(across) - min(across) < MIN_VERTICAL_SPREAD:
        # Two verticals close together cannot tell a tilt from a convergence.
        raise ValueError("the verticals are too close together: take one on each side")
    segments = [line.segment for line in picked]
    rotation = fit_rotation(segments, width, height, f)
    result = _finish(
        rotation, segments, width, height, f, lines_found=len(lines), lines_used=len(picked)
    )
    if any("very large" in w for w in result.warnings):
        raise ValueError("these lines ask for a very large correction: probably a wrong line")
    result.lines = lines
    result.guide_lines = list(chosen)
    return result


def plan_from_jpeg(
    jpeg: bytes, photo: dict[str, Any], frontal_horizontals: bool = True
) -> Plan | None:
    image = Image.open(io.BytesIO(jpeg))
    image.draft("RGB", (ANALYSIS_PX, ANALYSIS_PX))
    image = image.convert("RGB")
    if max(image.size) > ANALYSIS_PX:
        image.thumbnail((ANALYSIS_PX, ANALYSIS_PX))
    # The preview has the photo's displayed proportions; scale it to the photo's size.
    long_side = max(int(photo.get("width") or 0), int(photo.get("height") or 0)) or max(image.size)
    scale = long_side / max(image.size)
    width, height = round(image.width * scale), round(image.height * scale)
    return plan(
        image,
        width,
        height,
        focal_35mm=photo.get("focal_length_35mm"),
        focal_mm=photo.get("focal_length_mm"),
        frontal_horizontals=frontal_horizontals,
    )
