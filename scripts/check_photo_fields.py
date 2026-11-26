"""Check Safelight's photo fields in the real Lightroom, on a test photo only.

Run it after reloading the plugin (Plug-in Manager, Safelight, Reload) and accepting
Lightroom's catalog update:

    uv run python scripts/check_photo_fields.py

It works on one virtual copy named "Safelight Test..." in the collection "Safelight
Tests" and nothing else: it marks the copy validated, checks that an edit is refused,
takes the validation back, checks that the same edit then runs, and puts everything back
as it was (Exposure, the Safelight fields). It stops at the first failed check.
"""

from __future__ import annotations

import asyncio
import sys

from safelight.hub_client import HubClient
from safelight.mcp_server import Runner, list_photos
from safelight.search import PhotoFilters


def check(ok: bool, what: str) -> None:
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        sys.exit(1)


async def main() -> None:
    client = HubClient(client_name="check-photo-fields")
    runner = Runner(client)
    photos, _ = await list_photos(
        runner, None, filters=PhotoFilters(collection="Safelight Tests"), limit=50
    )
    copies = [
        p
        for p in photos
        if p.get("virtual_copy") and (p.get("copy_name") or "").startswith("Safelight Test")
    ]
    check(bool(copies), 'a "Safelight Test" virtual copy in the collection "Safelight Tests"')
    photo = copies[0]
    uuid = photo["uuid"]
    print(f"      using {photo['file_name']} ({photo['copy_name']}), {uuid}")
    check("validated" in photo, "photos.get reports Safelight's fields (plugin reloaded?)")

    before = (await runner.read(None, "develop.get", {"uuids": [uuid]}))["settings"]
    exposure = before.get("Exposure2012", 0)
    fields_before = {
        "validated": bool(photo.get("validated")),
        "geometry_by": photo.get("geometry_by") or "none",
        "reject_reason": photo.get("reject_reason") or "",
    }

    async def run(command: str, params: dict) -> dict:
        reply = await runner.settle(await client.run(command, params, wait_s=30))
        if reply.get("state") == "confirmation_required":
            sys.exit("The hub asks for approval: raise the threshold or use fewer photos.")
        return reply

    try:
        reply = await run(
            "metadata.set",
            {"uuids": [uuid], "fields": {"validated": True, "reject_reason": "check"}},
        )
        check(reply.get("state") == "succeeded", "metadata.set writes validated and a reason")
        (shown,) = (await runner.read(None, "photos.get", {"uuids": [uuid]}))["photos"]
        check(shown.get("validated") is True, "photos.get shows validated = true")
        check(shown.get("reject_reason") == "check", "photos.get shows the reject reason")

        edit = {"uuids": [uuid], "settings": {"Exposure": exposure + 0.1}}
        reply = await run("develop.apply", edit)
        failures = (reply.get("result") or {}).get("failures") or []
        check(
            any("VALIDATED" in str(f.get("error")) for f in failures),
            "develop.apply on the validated photo is refused (VALIDATED)",
        )
        after = (await runner.read(None, "develop.get", {"uuids": [uuid]}))["settings"]
        check(after.get("Exposure2012", 0) == exposure, "its Exposure did not change")

        dry = await run("develop.apply", {**edit, "dry_run": True})
        check((dry.get("result") or {}).get("affected") == 0, "the dry run counts 0 photos")

        reply = await run("metadata.set", {"uuids": [uuid], "fields": {"validated": False}})
        check(reply.get("state") == "succeeded", "the validation can be taken back")
        reply = await run("develop.apply", edit)
        after = (await runner.read(None, "develop.get", {"uuids": [uuid]}))["settings"]
        check(abs(after.get("Exposure2012", 0) - exposure - 0.1) < 1e-6, "then the edit runs")
    finally:
        await run("develop.apply", {"uuids": [uuid], "settings": {"Exposure": exposure}})
        await run("metadata.set", {"uuids": [uuid], "fields": fields_before})
        print("      put back: Exposure and Safelight's fields as they were")


if __name__ == "__main__":
    asyncio.run(main())
