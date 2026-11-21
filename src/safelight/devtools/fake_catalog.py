"""A small in-memory Lightroom catalog for the fake plugin.

Implements the plugin commands the MCP tools rely on, with the same parameters and
answers as plugin/Safelight.lrplugin (see docs/PLUGIN_COMMANDS.md), so the tools can be
tested, and clients developed, without Lightroom.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image

from .fake_plugin import CommandError, Handler, Progress

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
            }
        self.selected = list(self.photos)[:2]
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
        keys = ("uuid", "file_name", "format", "rating", "pick", "label", "title")
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
        color = COLORS[int(photo["uuid"][-1]) % len(COLORS)]
        buffer = io.BytesIO()
        Image.new("RGB", (size, size * 2 // 3), color).save(buffer, format="JPEG")
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
            "collections.list": self.collections_list,
            "collections.delete": self.collections_delete,
        }
