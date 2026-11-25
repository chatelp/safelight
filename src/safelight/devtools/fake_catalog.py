"""A small in-memory Lightroom catalog for the fake plugin.

Implements the plugin commands the MCP tools rely on, with the same parameters and
answers as plugin/Safelight.lrplugin (see docs/PLUGIN_COMMANDS.md), so the tools can be
tested, and clients developed, without Lightroom.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .fake_plugin import CommandError, Handler, Progress

# Photos 2 and 3 are a burst: 1 s apart, same scene (3 is the sharper frame).
CAPTURE_TIMES = [
    "2026-11-20T10:00:00+01:00",
    "2026-11-20T10:00:30+01:00",
    "2026-11-20T10:00:31+01:00",
    "2026-11-20T10:01:30+01:00",
    "2026-11-20T10:02:00+01:00",
    "2026-11-20T10:02:30+01:00",
    "2026-11-20T10:03:00+01:00",
    "2026-11-20T10:03:30+01:00",
]
COLORS = [(200, 80, 60), (60, 140, 200), (90, 170, 90), (220, 190, 60), (150, 90, 190)]


class FakeCatalog:
    def __init__(self, count: int = 8) -> None:
        self.photos: dict[str, dict[str, Any]] = {}
        for index in range(count):
            uuid = f"FAKE-{index + 1:04d}"
            self.photos[uuid] = {
                "uuid": uuid,
                "file_name": f"DSC{index + 1:05d}.ARW",
                "format": "RAW",
                "rating": index % 6,
                "pick": 0,
                "label": None,
                "title": None,
                "keywords": [],
                "settings": {"Exposure2012": 0.0, "Contrast2012": 0, "Temperature": 5500},
                "masks": [],
                "snapshots": [],
                "captured_at": CAPTURE_TIMES[index % len(CAPTURE_TIMES)],
                "camera": "ILCE-7M3",
                "focal_length_mm": [24, 35, 35, 50, 70, 85, 24, 135][index % 8],
                "aperture": [1.8, 2.8, 2.8, 4.0, 5.6, 1.4, 8.0, 2.0][index % 8],
            }
        self.selected = list(self.photos)[:2]
        self.presets = [
            {"uuid": "PRESET-1", "name": "Punchy", "folder": "Mine"},
            {"uuid": "PRESET-2", "name": "Soft black and white", "folder": "Shared"},
        ]
        self.exports: list[dict[str, Any]] = []
        self.collections: list[dict[str, Any]] = [
            {"id": 1, "name": "Portfolio", "kind": "collection", "photos": []}
        ]

    # -- helpers ----------------------------------------------------------------------------

    def _resolve(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if params.get("uuids"):
            missing = [u for u in params["uuids"] if u not in self.photos]
            if missing:
                raise CommandError("COMMAND_FAILED", f"No photo found with these ids: {missing}")
            return [self.photos[u] for u in params["uuids"]]
        ids = self.selected[:1] if params.get("target") == "active" else self.selected
        if not ids:
            raise CommandError("COMMAND_FAILED", "No photo selected in Lightroom.")
        return [self.photos[u] for u in ids]

    @staticmethod
    def _summary(photo: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "uuid",
            "file_name",
            "format",
            "rating",
            "pick",
            "label",
            "title",
            "captured_at",
            "camera",
            "focal_length_mm",
            "aperture",
        )
        out = {key: photo[key] for key in keys}
        out["keywords"] = ", ".join(photo["keywords"]) or None
        return out

    @staticmethod
    def _plan(photos: list[dict[str, Any]], summary: str) -> dict[str, Any]:
        return {"dry_run": True, "affected": len(photos), "summary": summary, "missing": []}

    # -- commands ---------------------------------------------------------------------------

    async def info(self, _: dict[str, Any], __: Progress) -> Any:
        return {
            "lightroom_version": "16.0.1 (fake)",
            "module": "library",
            "active_photo": self.selected[0] if self.selected else None,
            "selected_count": len(self.selected),
        }

    async def get(self, params: dict[str, Any], _: Progress) -> Any:
        photos = self._resolve(params)
        limit = int(params.get("limit", 100))
        return {"total": len(photos), "photos": [self._summary(p) for p in photos[:limit]]}

    async def search(self, params: dict[str, Any], _: Progress) -> Any:
        found = list(self.photos.values())
        search = params.get("search") or {}
        criteria = search.get("items", [search])
        for criterion in criteria:
            if criterion.get("criteria") == "rating" and criterion.get("operation") == ">=":
                found = [p for p in found if p["rating"] >= criterion["value"]]
        limit = int(params.get("limit", 50))
        return {"total": len(found), "photos": [self._summary(p) for p in found[:limit]]}

    async def preview(self, params: dict[str, Any], _: Progress) -> Any:
        photo = self._resolve(params)[0]
        size = int(params.get("size", 256))
        number = int(photo["uuid"][-4:])
        scene = 2 if number == 3 else number  # photo 3 is the same scene as photo 2
        image = Image.new("RGB", (size, size * 2 // 3), COLORS[scene % len(COLORS)])
        draw = ImageDraw.Draw(image)
        height = size * 2 // 3
        for step in range(scene + 1):  # a different pattern of bars per scene
            x = (step * 53 + scene * 29) % size
            top = (scene * 41 + step * 13) % (height // 2)
            draw.rectangle((x, top, x + size // 16, top + height // 3), fill=(20, 20, 20))
        horizon = height // 3 + (scene * 23) % (height // 2)
        draw.line((0, horizon, size, horizon), fill=(240, 240, 240), width=3)
        if number in (2, 5):  # photo 2: slightly soft burst frame; photo 5: blurred
            image = image.filter(ImageFilter.GaussianBlur(1 if number == 2 else 6))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        data = buffer.getvalue()
        return {"uuid": photo["uuid"], "bytes": len(data), "data": base64.b64encode(data).decode()}

    async def develop_get(self, params: dict[str, Any], _: Progress) -> Any:
        photo = self._resolve(params)[0]
        return {
            "uuid": photo["uuid"],
            "format": photo["format"],
            "settings": dict(photo["settings"]),
        }

    async def develop_apply(self, params: dict[str, Any], progress: Progress) -> Any:
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Change develop settings on {len(photos)} photos")
        for index, photo in enumerate(photos):
            await progress(index, len(photos), "Develop")
            for name, value in params["settings"].items():
                key = {"Exposure": "Exposure2012", "Contrast": "Contrast2012"}.get(name, name)
                if params.get("relative"):
                    value = photo["settings"].get(key, 0) + value
                photo["settings"][key] = value
        return {"requested": len(photos), "updated": len(photos), "failures": []}

    async def metadata_set(self, params: dict[str, Any], _: Progress) -> Any:
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Change metadata of {len(photos)} photos")
        for photo in photos:
            for name, value in params["fields"].items():
                photo[name] = value
        return {"requested": len(photos), "updated": len(photos), "failures": []}

    async def keywords_add(self, params: dict[str, Any], _: Progress) -> Any:
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, "Add keywords")
        for photo in photos:
            photo["keywords"].extend(k for k in params["keywords"] if k not in photo["keywords"])
        return {"requested": len(photos), "updated": len(photos), "keywords_not_created": []}

    async def masks_list(self, params: dict[str, Any], _: Progress) -> Any:
        photo = self._resolve(params)[0]
        return {"uuid": photo["uuid"], "masks": photo["masks"]}

    async def snapshots_list(self, params: dict[str, Any], _: Progress) -> Any:
        photo = self._resolve(params)[0]
        return {"uuid": photo["uuid"], "snapshots": photo["snapshots"]}

    # -- develop, continued: same checks on parameters as the plugin ------------------------

    @staticmethod
    def _batch(photos: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {"requested": len(photos), "updated": len(photos), "failures": [], **extra}

    async def develop_copy(self, params: dict[str, Any], _: Progress) -> Any:
        source = self.photos.get(params.get("from", ""))
        if source is None:
            raise CommandError("COMMAND_FAILED", f"No photo found with id {params.get('from')}")
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Copy develop settings to {len(photos)} photos")
        keys = params.get("keys") or list(source["settings"])
        for photo in photos:
            for name in keys:
                key = {"Exposure": "Exposure2012", "Contrast": "Contrast2012"}.get(name, name)
                photo["settings"][key] = source["settings"][key]
        return self._batch(photos)

    async def auto_tone(self, params: dict[str, Any], _: Progress) -> Any:
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Apply Auto Tone to {len(photos)} photos")
        for photo in photos:
            photo["settings"]["Exposure2012"] = 0.35
        return self._batch(photos)

    async def auto_white_balance(self, params: dict[str, Any], _: Progress) -> Any:
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Set white balance to Auto on {len(photos)} photos")
        for photo in photos:
            photo["settings"]["WhiteBalance"] = "Auto"
        return self._batch(photos)

    async def develop_reset(self, params: dict[str, Any], _: Progress) -> Any:
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Reset all develop settings of {len(photos)} photos")
        for photo in photos:
            photo["settings"] = {}
        return self._batch(photos)

    async def presets_list(self, _: dict[str, Any], __: Progress) -> Any:
        return {"count": len(self.presets), "presets": self.presets}

    async def apply_preset(self, params: dict[str, Any], _: Progress) -> Any:
        preset = next((p for p in self.presets if p["uuid"] == params.get("preset_uuid")), None)
        if preset is None:
            raise CommandError("COMMAND_FAILED", "No develop preset with this id")
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Apply preset to {len(photos)} photos")
        for photo in photos:
            photo["preset"] = (preset["name"], params.get("amount"))
        return self._batch(photos, preset=preset["name"])

    async def snapshots_create(self, params: dict[str, Any], _: Progress) -> Any:
        if not params.get("name"):
            raise CommandError("COMMAND_FAILED", "name is required")
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Create snapshot on {len(photos)} photos")
        for photo in photos:
            photo["snapshots"].append(
                {"id": f"S{len(photo['snapshots']) + 1}", "name": params["name"]}
            )
        return self._batch(photos)

    def _snapshot(self, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        photo = self._resolve(params)[0]  # the first photo only, like the plugin
        for item in photo["snapshots"]:
            if params.get("name") == item["name"]:
                return photo, item
        raise CommandError("COMMAND_FAILED", "No snapshot with this id or name on this photo")

    async def snapshots_apply(self, params: dict[str, Any], _: Progress) -> Any:
        photo, item = self._snapshot(params)
        photo["applied_snapshot"] = item["name"]
        return {"uuid": photo["uuid"], "applied": item["name"]}

    async def snapshots_delete(self, params: dict[str, Any], _: Progress) -> Any:
        photo, item = self._snapshot(params)
        if params.get("dry_run"):
            return {"dry_run": True, "affected": 1, "summary": "Delete snapshot"}
        photo["snapshots"].remove(item)
        return {"uuid": photo["uuid"], "deleted": item["name"]}

    # -- masks --------------------------------------------------------------------------------

    def _mask(self, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        photo = self._resolve(params)[0]
        for mask in photo["masks"]:
            if params.get("mask_id") in (mask["id"], mask["name"]):
                return photo, mask
        raise CommandError("COMMAND_FAILED", f"No mask '{params.get('mask_id')}' on this photo")

    async def masks_create(self, params: dict[str, Any], _: Progress) -> Any:
        if params.get("kind") not in ("subject", "sky", "background", "people"):
            raise CommandError("COMMAND_FAILED", "kind must be subject, sky, background or people")
        photo = self._resolve(params)[0]
        if params.get("dry_run"):
            return {"dry_run": True, "affected": 1, "summary": "Create a mask"}
        mask = {
            "id": f"M{len(photo['masks']) + 1}",
            "name": params["kind"],
            "inverted": False,
            "adjustments": {},
        }
        photo["masks"].append(mask)
        return {"uuid": photo["uuid"], "mask_id": mask["id"], "kind": params["kind"]}

    async def masks_adjust(self, params: dict[str, Any], _: Progress) -> Any:
        if not params.get("adjustments"):
            raise CommandError("COMMAND_FAILED", "adjustments must be a non-empty object")
        photo, mask = self._mask(params)
        if params.get("dry_run"):
            return {"dry_run": True, "affected": 1, "summary": "Adjust mask"}
        for name, value in params["adjustments"].items():
            base = mask["adjustments"].get(name, 0) if params.get("relative") else 0
            mask["adjustments"][name] = base + value
        return {"uuid": photo["uuid"], "mask": dict(mask)}

    async def masks_invert(self, params: dict[str, Any], _: Progress) -> Any:
        photo, mask = self._mask(params)
        if params.get("dry_run"):
            return {"dry_run": True, "affected": 1, "summary": "Invert mask"}
        mask["inverted"] = not mask["inverted"]
        return {"uuid": photo["uuid"], "inverted": mask["id"]}

    async def masks_delete(self, params: dict[str, Any], _: Progress) -> Any:
        photo, mask = self._mask(params)
        if params.get("dry_run"):
            return {"dry_run": True, "affected": 1, "summary": "Delete mask"}
        photo["masks"].remove(mask)
        return {"uuid": photo["uuid"], "deleted": mask["id"]}

    # -- keywords, organization, export -------------------------------------------------------

    async def keywords_remove(self, params: dict[str, Any], _: Progress) -> Any:
        if not params.get("keywords"):
            raise CommandError("COMMAND_FAILED", "keywords must be a non-empty list")
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Remove keywords from {len(photos)} photos")
        known = {k for photo in self.photos.values() for k in photo["keywords"]}
        for photo in photos:
            photo["keywords"] = [k for k in photo["keywords"] if k not in params["keywords"]]
        missing = [k for k in params["keywords"] if k not in known]
        return self._batch(photos, keywords_not_found=missing)

    async def keywords_list(self, params: dict[str, Any], _: Progress) -> Any:
        found = sorted({k for photo in self.photos.values() for k in photo["keywords"]})
        query = (params.get("query") or "").lower()
        found = [k for k in found if query in k.lower()]
        return {"total": len(found), "keywords": found[: int(params.get("limit", 500))]}

    async def rotate(self, params: dict[str, Any], _: Progress) -> Any:
        if params.get("direction") not in ("left", "right"):
            raise CommandError("COMMAND_FAILED", "direction must be left or right")
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Rotate {len(photos)} photos")
        for photo in photos:
            photo["rotation"] = params["direction"]
        return self._batch(photos)

    async def create_virtual_copies(self, params: dict[str, Any], _: Progress) -> Any:
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Create a virtual copy of {len(photos)} photos")
        copies = []
        for photo in photos:
            uuid = f"{photo['uuid']}-COPY"
            self.photos[uuid] = {**photo, "uuid": uuid, "copy_name": params.get("name")}
            copies.append(uuid)
        return {"requested": len(photos), "copies": copies, "missing": []}

    async def export(self, params: dict[str, Any], _: Progress) -> Any:
        if params.get("format", "JPEG") not in ("JPEG", "TIFF", "PNG", "DNG", "ORIGINAL"):
            raise CommandError("COMMAND_FAILED", "unknown format")
        if not str(params.get("destination", "")).startswith("/"):
            raise CommandError("COMMAND_FAILED", "destination must be an absolute folder path")
        if params.get("if_exists", "rename") not in ("rename", "overwrite", "skip"):
            raise CommandError("COMMAND_FAILED", "if_exists must be rename, overwrite or skip")
        photos = self._resolve(params)
        if params.get("dry_run"):
            return self._plan(photos, f"Export {len(photos)} photos")
        self.exports.append(params)
        files = [f"{params['destination']}/{p['file_name']}" for p in photos]
        return {"destination": params["destination"], "files": files, "failures": []}

    async def collections_list(self, _: dict[str, Any], __: Progress) -> Any:
        return {
            "count": len(self.collections),
            "collections": [
                {k: v for k, v in c.items() if k != "photos"} for c in self.collections
            ],
        }

    async def collections_delete(self, params: dict[str, Any], _: Progress) -> Any:
        found = [c for c in self.collections if c["id"] == params.get("collection_id")]
        if not found:
            raise CommandError("COMMAND_FAILED", "No such collection")
        if params.get("dry_run"):
            return {"dry_run": True, "affected": len(found[0]["photos"]), "summary": "Delete"}
        self.collections.remove(found[0])
        return {"deleted": found[0]["name"]}

    def handlers(self) -> dict[str, Handler]:
        return {
            "catalog.info": self.info,
            "photos.get": self.get,
            "photos.search": self.search,
            "photos.preview": self.preview,
            "develop.get": self.develop_get,
            "develop.apply": self.develop_apply,
            "metadata.set": self.metadata_set,
            "keywords.add": self.keywords_add,
            "masks.list": self.masks_list,
            "snapshots.list": self.snapshots_list,
            "develop.copy": self.develop_copy,
            "develop.auto_tone": self.auto_tone,
            "develop.auto_white_balance": self.auto_white_balance,
            "develop.reset": self.develop_reset,
            "develop.apply_preset": self.apply_preset,
            "presets.list": self.presets_list,
            "snapshots.create": self.snapshots_create,
            "snapshots.apply": self.snapshots_apply,
            "snapshots.delete": self.snapshots_delete,
            "masks.create": self.masks_create,
            "masks.adjust": self.masks_adjust,
            "masks.invert": self.masks_invert,
            "masks.delete": self.masks_delete,
            "keywords.remove": self.keywords_remove,
            "keywords.list": self.keywords_list,
            "photos.rotate": self.rotate,
            "photos.create_virtual_copies": self.create_virtual_copies,
            "export.photos": self.export,
            "collections.list": self.collections_list,
            "collections.delete": self.collections_delete,
        }
