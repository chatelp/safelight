"""Every tool that changes photos, end to end against the fake catalog: it must send the
plugin the right command and parameters, act on the right photos only, and ask first when
the policy says so."""

import asyncio
import contextlib

import pytest
from mcp import Client
from test_tools import EXPECTED_TOOLS, lightroom

from safelight.hub_client import HubClient
from safelight.mcp_server import build_server

pytestmark = pytest.mark.anyio

ONE, TWO, THREE = "FAKE-0001", "FAKE-0002", "FAKE-0003"


@contextlib.asynccontextmanager
async def session(home, threshold=50):
    async with lightroom(home, threshold) as (catalog, client), Client(build_server(client)) as mcp:

        async def call(tool, **args):
            result = await mcp.call_tool(tool, args)
            assert not result.is_error, result.content
            return result.structured_content

        yield catalog, call, mcp


async def confirmed(call, tool, **args):
    """Calls a tool that must ask first, then approves."""
    gate = await call(tool, **args)
    assert gate["state"] == "confirmation_required"
    return await call(tool, **args, confirmation_token=gate["confirmation"]["token"])


# -- develop ------------------------------------------------------------------------------


async def test_copy_develop_settings(safelight_home):
    async with session(safelight_home) as (catalog, call, _):
        catalog.photos[ONE]["settings"].update(Exposure2012=1.5, Contrast2012=20)
        await call("copy_develop_settings", from_photo_id=ONE, photo_ids=[TWO], keys=["Exposure"])
        assert catalog.photos[TWO]["settings"]["Exposure2012"] == 1.5
        assert catalog.photos[TWO]["settings"]["Contrast2012"] == 0  # not asked for
        await call("copy_develop_settings", from_photo_id=ONE, photo_ids=[TWO])
        assert catalog.photos[TWO]["settings"]["Contrast2012"] == 20
        assert catalog.photos[THREE]["settings"]["Exposure2012"] == 0  # not a target


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("auto_tone", {"Exposure2012": 0.35, "Contrast2012": 0, "Temperature": 5500}),
        (
            "auto_white_balance",
            {"Exposure2012": 0.0, "Contrast2012": 0, "Temperature": 5500, "WhiteBalance": "Auto"},
        ),
        ("reset", {}),
    ],
)
async def test_auto_develop(safelight_home, action, expected):
    async with session(safelight_home) as (catalog, call, _):
        await call("auto_develop", action=action, photo_ids=[TWO])
        assert catalog.photos[TWO]["settings"] == expected
        assert catalog.photos[ONE]["settings"]["Temperature"] == 5500


async def test_apply_preset(safelight_home):
    async with session(safelight_home) as (catalog, call, mcp):
        await call("apply_preset", preset_id="PRESET-1", photo_ids=[ONE], amount=60)
        assert catalog.photos[ONE]["preset"] == ("Punchy", 60)
        assert "preset" not in catalog.photos[TWO]
        await call("apply_preset", preset_id="PRESET-2", photo_ids=[ONE])
        assert catalog.photos[ONE]["preset"] == ("Soft black and white", None)
        unknown = await mcp.call_tool("apply_preset", {"preset_id": "NOPE"})
        assert unknown.is_error


async def test_snapshots(safelight_home):
    async with session(safelight_home) as (catalog, call, _):
        await call("snapshot", action="create", name="Before", photo_ids=[ONE, TWO])
        assert [s["name"] for s in catalog.photos[TWO]["snapshots"]] == ["Before"]
        await call("snapshot", action="apply", name="Before", photo_ids=[TWO])
        assert catalog.photos[TWO]["applied_snapshot"] == "Before"
        await confirmed(call, "snapshot", action="delete", name="Before", photo_ids=[TWO])
        assert catalog.photos[TWO]["snapshots"] == []
        assert len(catalog.photos[ONE]["snapshots"]) == 1


@pytest.mark.parametrize("action", ["apply", "delete"])
@pytest.mark.parametrize("target", [{"photo_ids": [ONE, TWO]}, {"scope": "selected"}])
async def test_snapshot_never_acts_on_the_first_photo_only(safelight_home, action, target):
    async with session(safelight_home) as (catalog, call, mcp):
        await call("snapshot", action="create", name="Before", photo_ids=[ONE, TWO])
        result = await mcp.call_tool("snapshot", {"action": action, "name": "Before", **target})
        assert result.is_error
        assert "one photo" in result.content[0].text
        assert "applied_snapshot" not in catalog.photos[ONE]
        assert len(catalog.photos[ONE]["snapshots"]) == 1


# -- masks --------------------------------------------------------------------------------


async def test_masks(safelight_home):
    async with session(safelight_home) as (catalog, call, _):
        created = await call(
            "create_mask", kind="sky", photo_id=TWO, adjustments={"Exposure": -0.5}
        )
        mask_id = created["result"]["mask_id"]
        (mask,) = catalog.photos[TWO]["masks"]
        assert mask["adjustments"] == {"Exposure": -0.5}
        assert catalog.photos[ONE]["masks"] == []

        relative = {"Exposure": -0.25}
        await call("edit_mask", mask=mask_id, photo_id=TWO, adjustments=relative, relative=True)
        assert mask["adjustments"] == {"Exposure": -0.75}
        await call("edit_mask", mask="sky", photo_id=TWO, adjustments={"Exposure": 1.0})
        assert mask["adjustments"] == {"Exposure": 1.0}

        await call("change_mask", action="invert", mask=mask_id, photo_id=TWO)
        assert mask["inverted"] is True
        await confirmed(call, "change_mask", action="delete", mask=mask_id, photo_id=TWO)
        assert catalog.photos[TWO]["masks"] == []


async def test_masks_default_to_the_active_photo(safelight_home):
    async with session(safelight_home) as (catalog, call, _):
        await call("create_mask", kind="subject")
        assert len(catalog.photos[ONE]["masks"]) == 1
        assert catalog.photos[ONE]["masks"][0]["adjustments"] == {}


# -- keywords, lists, organization ----------------------------------------------------------


async def test_edit_keywords(safelight_home):
    async with session(safelight_home) as (catalog, call, mcp):
        catalog.photos[ONE]["keywords"] = ["old", "kept"]
        catalog.photos[THREE]["keywords"] = ["old"]
        result = await call("edit_keywords", add=["new"], remove=["old"], photo_ids=[ONE])
        assert catalog.photos[ONE]["keywords"] == ["kept", "new"]
        assert catalog.photos[THREE]["keywords"] == ["old"]
        assert set(result["result"]) == {"added", "removed"}
        await call("edit_keywords", remove=["kept"], photo_ids=[ONE])
        assert catalog.photos[ONE]["keywords"] == ["new"]
        assert (await mcp.call_tool("edit_keywords", {"photo_ids": [ONE]})).is_error


async def test_edit_keywords_can_confirm_both_steps(safelight_home):
    """Adding and removing are two changes, each with its own approval."""
    async with session(safelight_home, threshold=1) as (catalog, call, _):
        for photo in catalog.photos.values():
            photo["keywords"] = ["old"]
        both = {"add": ["new"], "remove": ["old"]}
        second = await confirmed(call, "edit_keywords", **both)
        assert second["state"] == "confirmation_required"  # for the removal
        assert "without `add`" in second["message"]
        assert catalog.photos[ONE]["keywords"] == ["old", "new"]
        done = await call(
            "edit_keywords",
            remove=["old"],
            confirmation_token=second["confirmation"]["token"],
        )
        assert done["state"] == "succeeded"
        assert catalog.photos[ONE]["keywords"] == ["new"]


async def test_list_catalog_items(safelight_home):
    async with session(safelight_home) as (catalog, call, _):
        catalog.photos[ONE]["keywords"] = ["Places > France", "People"]
        presets = await call("list_catalog_items", kind="presets")
        assert [p["uuid"] for p in presets["items"]] == ["PRESET-1", "PRESET-2"]
        by_folder = await call("list_catalog_items", kind="presets", query="SHARED")
        assert [p["uuid"] for p in by_folder["items"]] == ["PRESET-2"]
        first = await call("list_catalog_items", kind="presets", limit=1)
        assert first["total"] == 2 and len(first["items"]) == 1
        collections = await call("list_catalog_items", kind="collections")
        assert [c["name"] for c in collections["items"]] == ["Portfolio"]
        keywords = await call("list_catalog_items", kind="keywords", query="france")
        assert keywords["keywords"] == ["Places > France"]


@pytest.mark.parametrize("direction", ["left", "right"])
async def test_rotate(safelight_home, direction):
    async with session(safelight_home) as (catalog, call, _):
        await call("organize_photos", action=f"rotate_{direction}", photo_ids=[TWO])
        assert catalog.photos[TWO]["rotation"] == direction
        assert "rotation" not in catalog.photos[ONE]


async def test_virtual_copies(safelight_home):
    async with session(safelight_home) as (catalog, call, _):
        result = await call(
            "organize_photos",
            action="create_virtual_copies",
            photo_ids=[TWO],
            copy_name="Try",
        )
        (copy,) = result["result"]["copies"]
        assert catalog.photos[copy]["copy_name"] == "Try"


# -- export ---------------------------------------------------------------------------------


async def test_export_photos(safelight_home):
    async with session(safelight_home) as (catalog, call, mcp):
        result = await call("export_photos", destination="/tmp/out", photo_ids=[TWO])
        assert result["result"]["files"] == ["/tmp/out/DSC00002.ARW"]
        sent = catalog.exports[-1]
        assert sent["if_exists"] == "rename"  # existing files are kept by default
        assert (sent["format"], sent["quality"], sent["color_space"]) == ("JPEG", 85, "sRGB")
        assert "long_edge_px" not in sent and "sharpen" not in sent

        options = {
            "format": "TIFF",
            "quality": 70,
            "long_edge_px": 2048,
            "color_space": "AdobeRGB",
            "if_exists": "skip",
            "sharpen": "matte",
            "strip_metadata": True,
            "remove_location": True,
        }
        await call("export_photos", destination="/tmp/out", photo_ids=[TWO], **options)
        assert options.items() <= catalog.exports[-1].items()
        assert (await mcp.call_tool("export_photos", {"destination": "out"})).is_error


# -- jobs -----------------------------------------------------------------------------------


async def test_cancel_a_running_job(safelight_home):
    async with session(safelight_home) as (_, call, _mcp):
        client = HubClient(safelight_home)
        params = {"count": 40, "delay_s": 0.1}
        started = await client.run("fake.bulk_edit", params, wait_s=0.2)
        assert started["state"] == "running"
        await call("safelight_cancel_job", job_id=started["job_id"])
        for _ in range(100):
            job = await call("safelight_job", job_id=started["job_id"])
            if job["state"] != "running":
                break
            await asyncio.sleep(0.02)
        assert job["state"] == "cancelled"


# -- approvals and coverage -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("copy_develop_settings", {"from_photo_id": THREE}),
        ("auto_develop", {"action": "reset"}),
        ("apply_preset", {"preset_id": "PRESET-1"}),
        ("snapshot", {"action": "create", "name": "x", "scope": "selected"}),
        ("edit_keywords", {"add": ["x"]}),
        ("edit_keywords", {"remove": ["x"]}),
        ("organize_photos", {"action": "rotate_left"}),
        ("organize_photos", {"action": "create_virtual_copies"}),
        ("export_photos", {"destination": "/tmp/out"}),
    ],
)
async def test_past_the_threshold_tools_ask_then_accept_the_token(safelight_home, tool, args):
    async with session(safelight_home, threshold=1) as (catalog, call, _):
        gate = await call(tool, **args)
        assert gate["state"] == "confirmation_required"
        assert gate["confirmation"]["affected"] == 2
        untouched = {i for i, p in catalog.photos.items() if p["settings"]["Temperature"] == 5500}
        assert len(untouched) == len(catalog.photos) == 8 and catalog.exports == []
        done = await call(tool, **args, confirmation_token=gate["confirmation"]["token"])
        assert done["state"] == "succeeded"


def test_every_tool_is_really_called_by_a_test():
    """A tool listed in EXPECTED_TOOLS but never called is not tested."""
    import pathlib
    import re

    here = pathlib.Path(__file__).parent
    called = set()
    for path in here.glob("test_*.py"):
        called |= set(
            re.findall(r'(?:call_tool|call|confirmed)\(\s*(?:call,\s*)?"(\w+)"', path.read_text())
        )
    assert EXPECTED_TOOLS - called == set()
