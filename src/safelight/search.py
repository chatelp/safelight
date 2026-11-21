"""Simple photo filters turned into Lightroom's smart-collection search description.

AI clients fill a `PhotoFilters` object ("4 stars or more, shot last week with the
24-70") and this module builds the `searchDesc` table that `catalog:findPhotos`
understands. The criteria names and operations come from the Lightroom Classic SDK
(`LrCatalog:findPhotos`).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

LABEL_CODES = {"red": 1, "yellow": 2, "green": 3, "blue": 4, "purple": 5}
PICK_CODES = {"picked": 1, "unflagged": 0, "rejected": -1}


class PhotoFilters(BaseModel):
    """Every field is optional; photos must match all the fields given."""

    text: str | None = Field(
        default=None, description="Words found anywhere: file name, title, caption, keywords, ..."
    )
    keywords: str | None = Field(default=None, description="Words that must all be in keywords")
    title: str | None = Field(default=None, description="Words in the title")
    caption: str | None = Field(default=None, description="Words in the caption")
    file_name: str | None = Field(default=None, description="Part of the file name")
    folder: str | None = Field(default=None, description="Part of the folder name or path")
    collection: str | None = Field(default=None, description="Part of a collection name")
    camera: str | None = Field(
        default=None, description="Exact camera model as Lightroom shows it, e.g. ILCE-7M3"
    )
    lens: str | None = Field(
        default=None, description="Exact lens name as Lightroom shows it, e.g. FE 24-70mm F4 ZA OSS"
    )
    rating_min: int | None = Field(default=None, ge=0, le=5)
    rating_max: int | None = Field(default=None, ge=0, le=5)
    pick: Literal["picked", "unflagged", "rejected"] | None = None
    label: Literal["red", "yellow", "green", "blue", "purple", "none"] | None = None
    captured_from: date | None = Field(default=None, description="First capture day included")
    captured_to: date | None = Field(default=None, description="Last capture day included")
    captured_in_last_days: int | None = Field(default=None, ge=1)
    iso_min: int | None = Field(default=None, ge=0)
    iso_max: int | None = Field(default=None, ge=0)
    file_format: Literal["RAW", "DNG", "JPG", "TIFF", "PNG", "PSD", "HEIC", "VIDEO"] | None = None
    has_gps: bool | None = None
    edited: bool | None = Field(default=None, description="True for photos with develop changes")
    cropped: bool | None = None
    orientation: Literal["portrait", "landscape", "square"] | None = None


def _criterion(criteria: str, operation: str, value: Any, **extra: Any) -> dict[str, Any]:
    return {"criteria": criteria, "operation": operation, "value": value, **extra}


def _boolean(criteria: str, value: bool) -> dict[str, Any]:
    return _criterion(criteria, "isTrue" if value else "isFalse", True)


def build_search(filters: PhotoFilters) -> dict[str, Any] | None:
    """searchDesc for catalog:findPhotos, or None when no filter is set."""
    f = filters
    parts: list[dict[str, Any]] = []
    for criteria, value in (
        ("all", f.text),
        ("keywords", f.keywords),
        ("title", f.title),
        ("caption", f.caption),
        ("filename", f.file_name),
        ("folder", f.folder),
        ("collection", f.collection),
    ):
        if value:
            parts.append(_criterion(criteria, "all", value))
    if f.camera:
        parts.append(_criterion("camera", "==", f.camera))
    if f.lens:
        parts.append(_criterion("lens", "==", f.lens))
    if f.rating_min is not None:
        parts.append(_criterion("rating", ">=", f.rating_min))
    if f.rating_max is not None:
        parts.append(_criterion("rating", "<=", f.rating_max))
    if f.pick is not None:
        parts.append(_criterion("pick", "==", PICK_CODES[f.pick]))
    if f.label is not None:
        if f.label == "none":
            parts.append(_criterion("labelColor", "==", "none"))
        else:
            parts.append(_criterion("labelColor", "==", LABEL_CODES[f.label]))
    if f.captured_from is not None:
        # "is after" is strict, so start the day before.
        parts.append(
            _criterion(
                "captureTime", ">", date.fromordinal(f.captured_from.toordinal() - 1).isoformat()
            )
        )
    if f.captured_to is not None:
        parts.append(
            _criterion(
                "captureTime", "<", date.fromordinal(f.captured_to.toordinal() + 1).isoformat()
            )
        )
    if f.captured_in_last_days is not None:
        parts.append(
            _criterion("captureTime", "inLast", f.captured_in_last_days, value_units="days")
        )
    if f.iso_min is not None:
        parts.append(_criterion("isoSpeedRating", ">=", f.iso_min))
    if f.iso_max is not None:
        parts.append(_criterion("isoSpeedRating", "<=", f.iso_max))
    if f.file_format is not None:
        parts.append(_criterion("fileFormat", "==", f.file_format))
    if f.has_gps is not None:
        parts.append(_boolean("hasGPSData", f.has_gps))
    if f.edited is not None:
        parts.append(_boolean("hasAdjustments", f.edited))
    if f.cropped is not None:
        parts.append(_boolean("cropped", f.cropped))
    if f.orientation is not None:
        parts.append(_criterion("aspectRatio", "==", f.orientation))

    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    # JSON cannot express Lua's mixed tables ({combine = ..., [1] = ..., [2] = ...}), so
    # combined searches travel as {"combine": ..., "items": [...]}; the plugin converts them.
    return {"combine": "intersect", "items": parts}
