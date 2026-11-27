"""The MCP tools, end to end: MCP client -> relay -> hub -> fake plugin with a fake catalog."""

import asyncio
import contextlib
import copy
import json

import mcp.types as types
import numpy as np
import pytest
from mcp import Client

from safelight import hub as hub_module
from safelight import mcp_server, straighten_store
from safelight.devtools.fake_catalog import FakeCatalog
from safelight.devtools.fake_plugin import CommandError, FakePlugin
from safelight.hub import Hub, HubServer
from safelight.hub_client import HubClient
from safelight.mcp_server import build_server
from safelight.settings import Settings
from safelight.upright import Line, Plan, plan_from_lines

pytestmark = pytest.mark.anyio


@contextlib.asynccontextmanager
async def lightroom(home, threshold=50, **settings):
    hub = Hub(settings=Settings(confirm_threshold=threshold, **settings))
    server = HubServer(home, port=0, hub=hub)
    task = asyncio.create_task(server.serve())
    while not (server.port and (home / "hub.json").exists()):
        await asyncio.sleep(0.02)
    catalog = FakeCatalog()
    plugin = FakePlugin(server.port, server.token, catalog.handlers())
    plugin_task = asyncio.create_task(plugin.run())
    client = HubClient(home)
    for _ in range(200):
        if (await client.status(start=False))["plugin"]["connected"]:
            break
        await asyncio.sleep(0.02)
    try:
        yield catalog, client
    finally:
        plugin.stop()
        server.stop()
        await asyncio.wait_for(task, 5)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(plugin_task, 5)


EXPECTED_TOOLS = {
    "undo_straighten",
    "safelight_status",
    "safelight_guide",
    "safelight_job",
    "safelight_cancel_job",
    "get_photos",
    "search_photos",
    "preview_photo",
    "contact_sheet",
    "analyze_photos",
    "get_develop_settings",
    "edit_develop",
    "copy_develop_settings",
    "auto_develop",
    "apply_preset",
    "snapshot",
    "create_mask",
    "edit_mask",
    "change_mask",
    "set_metadata",
    "edit_keywords",
    "list_catalog_items",
    "manage_collection",
    "organize_photos",
    "export_photos",
    "straighten_photos",
    "straighten_lines",
}


async def test_tool_list_and_read_only_annotations(safelight_home):
    async with Client(build_server(HubClient(safelight_home))) as mcp:
        tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    read_only = {n for n, t in tools.items() if t.annotations and t.annotations.read_only_hint}
    assert read_only == {
        "safelight_status",
        "safelight_guide",
        "safelight_job",
        "get_photos",
        "search_photos",
        "preview_photo",
        "contact_sheet",
        "analyze_photos",
        "get_develop_settings",
        "list_catalog_items",
        "straighten_lines",
    }
    destructive = {n for n, t in tools.items() if t.annotations.destructive_hint}
    assert destructive == {
        "auto_develop",
        "change_mask",
        "edit_keywords",
        "export_photos",
        "manage_collection",
        "set_metadata",
        "snapshot",
    }
    for tool in tools.values():  # some directories refuse tools with a missing hint
        hints = tool.annotations
        for hint in ("read_only", "destructive", "idempotent", "open_world"):
            assert isinstance(getattr(hints, f"{hint}_hint"), bool), (tool.name, hint)
    for tool in tools.values():
        assert tool.description and len(tool.description) > 30, tool.name


async def test_status_reports_active_photo(safelight_home):
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        status = (await mcp.call_tool("safelight_status", {})).structured_content
    assert status["lightroom"]["available"] is True
    assert status["lightroom"]["active_photo_id"] == "FAKE-0001"
    assert status["lightroom"]["selected_count"] == 2
    assert status["confirm_threshold"] == 50


async def test_search_and_get(safelight_home):
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        found = await mcp.call_tool("search_photos", {"filters": {"rating_min": 4}})
        selected = await mcp.call_tool("get_photos", {})
        empty = await mcp.call_tool("search_photos", {"filters": {}})
    assert found.structured_content["total"] == 2  # ratings 4 and 5
    assert [p["uuid"] for p in selected.structured_content["photos"]] == ["FAKE-0001", "FAKE-0002"]
    assert empty.is_error


async def test_preview_and_contact_sheet_return_images(safelight_home):
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        preview = await mcp.call_tool("preview_photo", {"size": 256})
        sheet = await mcp.call_tool("contact_sheet", {"filters": {"rating_min": 1}, "columns": 3})
    assert any(isinstance(c, types.ImageContent) for c in preview.content)
    images = [c for c in sheet.content if isinstance(c, types.ImageContent)]
    texts = [c.text for c in sheet.content if isinstance(c, types.TextContent)]
    assert images and images[0].mime_type == "image/jpeg"
    assert "1. FAKE-0002" in texts[0]


async def test_edit_develop_relative(safelight_home):
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        await mcp.call_tool("edit_develop", {"settings": {"Exposure": 0.5}, "scope": "active"})
        result = await mcp.call_tool(
            "edit_develop", {"settings": {"Exposure": -0.2}, "relative": True, "scope": "active"}
        )
    assert result.structured_content["state"] == "succeeded"
    assert catalog.photos["FAKE-0001"]["settings"]["Exposure2012"] == pytest.approx(0.3)


async def test_large_change_returns_a_token_without_elicitation(safelight_home):
    async with (
        lightroom(safelight_home, threshold=1) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        args = {"rating": 5}
        gate = (await mcp.call_tool("set_metadata", args)).structured_content
        assert gate["state"] == "confirmation_required"
        assert gate["confirmation"]["affected"] == 2
        assert catalog.photos["FAKE-0001"]["rating"] == 0
        args["confirmation_token"] = gate["confirmation"]["token"]
        done = (await mcp.call_tool("set_metadata", args)).structured_content
    assert done["state"] == "succeeded"
    assert catalog.photos["FAKE-0001"]["rating"] == 5


@pytest.mark.parametrize("approve", [True, False])
async def test_large_change_asks_the_user_when_the_client_can(safelight_home, approve):
    asked = []

    async def answer(_context, params):
        asked.append(params.message)
        return types.ElicitResult(action="accept", content={"approve": approve})

    async with (
        lightroom(safelight_home, threshold=1) as (catalog, _),
        # "legacy" keeps a back-channel for server-to-client requests, as stdio clients do.
        Client(
            build_server(HubClient(safelight_home)), elicitation_callback=answer, mode="legacy"
        ) as mcp,
    ):
        result = (await mcp.call_tool("set_metadata", {"label": "red"})).structured_content
    assert asked and "2 photos" in asked[0]
    if approve:
        assert result["state"] == "succeeded"
        assert catalog.photos["FAKE-0002"]["label"] == "red"
    else:
        assert result["state"] == "declined"
        assert catalog.photos["FAKE-0002"]["label"] is None


async def test_destructive_change_always_asks(safelight_home):
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        gate = await mcp.call_tool("manage_collection", {"action": "delete", "collection_id": 1})
    assert gate.structured_content["state"] == "confirmation_required"


async def test_lightroom_errors_become_tool_errors(safelight_home):
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        result = await mcp.call_tool("get_photos", {"photo_ids": ["NOPE"]})
    assert result.is_error
    assert "NOPE" in result.content[0].text


async def test_develop_settings_include_masks_and_snapshots(safelight_home):
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        result = (await mcp.call_tool("get_develop_settings", {})).structured_content
    assert result["settings"]["Temperature"] == 5500
    assert result["masks"] == [] and result["snapshots"] == []


async def test_falls_back_to_a_token_when_the_client_cannot_be_asked(safelight_home):
    """A client may advertise elicitation on a transport without a back-channel."""

    async def answer(_context, _params):
        raise AssertionError("must not be called")

    async with (
        lightroom(safelight_home, threshold=1),
        Client(
            build_server(HubClient(safelight_home)), elicitation_callback=answer, mode="auto"
        ) as mcp,
    ):
        result = (await mcp.call_tool("set_metadata", {"label": "red"})).structured_content
    assert result["state"] == "confirmation_required"


async def test_analyze_photos_finds_the_burst_and_the_blurred_photo(safelight_home):
    ids = [f"FAKE-{n:04d}" for n in range(1, 9)]
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        result = await mcp.call_tool("analyze_photos", {"photo_ids": ids})
    report = result.structured_content
    assert not result.is_error, result.content
    assert report["summary"]["photos_analyzed"] == 8
    assert report["groups"] == [
        {
            "kind": "burst",
            "photo_ids": ["FAKE-0002", "FAKE-0003"],
            "best_photo_id": "FAKE-0003",
            "why_best": "sharpest frame of the group",
        }
    ]
    flagged = {p["photo_id"]: p["issues"] for p in report["photos_with_issues"]}
    assert any("blurred" in issue for issue in flagged["FAKE-0005"])


async def test_search_filters_on_focal_length_after_the_search(safelight_home):
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        result = await mcp.call_tool(
            "search_photos", {"filters": {"rating_min": 0, "focal_length_min_mm": 50}}
        )
    found = result.structured_content
    assert found["total"] == 4
    assert all(p["focal_length_mm"] >= 50 for p in found["photos"])


# -- straighten_photos --------------------------------------------------------------------

EARLIER = {"PerspectiveVertical": 7, "PerspectiveRotate": -0.5, "CropTop": 0.01}


def _plan():
    guides = [(0.1, 0.1, 0.1, 0.9), (0.9, 0.1, 0.9, 0.9)]
    return Plan(guides, np.eye(3), 0.0, 0.0, 0.0, 0.0, lines_found=2, lines_used=2)


def _assert_put_back(photo):
    assert {key: photo["settings"][key] for key in EARLIER} == EARLIER
    assert photo["settings"]["PerspectiveUpright"] == 0


async def test_straighten_puts_back_a_photo_without_lines(safelight_home, monkeypatch):
    plans = iter([_plan(), None])
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", lambda *_, **__: next(plans))
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        for photo in catalog.photos.values():
            photo["settings"].update(EARLIER)
        result = (await mcp.call_tool("straighten_photos", {})).structured_content["result"]
        first, second = (catalog.photos[i] for i in catalog.selected)
    assert [e["id"] for e in result["straightened"]] == ["FAKE-0001"]
    assert first["settings"]["PerspectiveUpright"] == 5
    assert first["settings"]["PerspectiveVertical"] == 0
    assert result["not_straightened"][0]["put_back"] is True
    _assert_put_back(second)


async def test_straighten_puts_back_an_earlier_guided_upright(safelight_home, monkeypatch):
    guided = {
        "PerspectiveUpright": 5,
        "UprightFourSegmentsCount": 2,
        "UprightFourSegments_0": "0.1,0.1,0.1,0.9",
        "UprightTransform_5": "1,0,0,0,1,0,0,0,1",
    }
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", lambda *_, **__: None)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        photo = catalog.photos["FAKE-0001"]
        photo["settings"].update(guided)
        args = {"photo_ids": ["FAKE-0001"], "replace_existing_upright": True}
        await mcp.call_tool("straighten_photos", args)
    assert {key: photo["settings"][key] for key in guided} == guided


async def test_straighten_never_redoes_an_existing_correction_unless_asked(
    safelight_home, monkeypatch
):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", lambda *_, **__: _plan())
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        by_hand, other = (catalog.photos[i] for i in catalog.selected)
        by_hand["settings"].update(PerspectiveUpright=5, UprightTransform_5="by hand", CropTop=0.02)
        kept = dict(by_hand["settings"])
        result = (await mcp.call_tool("straighten_photos", {})).structured_content["result"]
        assert by_hand["settings"] == kept
        assert other["settings"]["PerspectiveUpright"] == 5
        assert [e["id"] for e in result["already_corrected_skipped"]] == ["FAKE-0001"]

        again = await mcp.call_tool("straighten_photos", {})  # both are corrected now
        assert again.is_error and "2 photos already" in again.content[0].text
        assert by_hand["settings"] == kept

        args = {"photo_ids": ["FAKE-0001"], "replace_existing_upright": True}
        await mcp.call_tool("straighten_photos", args)
        assert by_hand["settings"]["UprightTransform_5"] != "by hand"


async def test_straighten_check_only_counts_and_changes_nothing(safelight_home, monkeypatch):
    def no_rendering(*_, **__):
        raise AssertionError("check_only must not render or analyse photos")

    monkeypatch.setattr(mcp_server, "plan_from_jpeg", no_rendering)
    monkeypatch.setattr(mcp_server, "fetch_previews", no_rendering)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        ids = ["FAKE-0001", "FAKE-0002", "FAKE-0003"]
        catalog.photos["FAKE-0001"]["settings"].update(PerspectiveUpright=5)
        catalog.photos["FAKE-0003"]["validated"] = True
        before = copy.deepcopy(catalog.photos)
        args = {"photo_ids": ids, "check_only": True}
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        assert catalog.photos == before
        assert (await HubClient(safelight_home).review())["review"] == []
        assert straighten_store.load("FAKE-0002", safelight_home) is None
        assert result["summary"]["to_straighten"] == 1
        assert [e["id"] for e in result["to_straighten"]] == ["FAKE-0002"]
        assert [e["id"] for e in result["already_corrected_skipped"]] == ["FAKE-0001"]
        assert [e["id"] for e in result["validated_skipped"]] == ["FAKE-0003"]

        catalog.photos["FAKE-0002"]["settings"].update(PerspectiveUpright=5)
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        assert result["summary"]["to_straighten"] == 0  # nothing left: counts, no error
        assert result["summary"]["already_corrected_skipped"] == 2


async def test_straighten_check_only_says_when_nothing_can_be_done(safelight_home):
    async with (
        lightroom(safelight_home, read_only=True) as _,
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        args = {"photo_ids": ["FAKE-0001"], "check_only": True}
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        assert any("read-only" in note for note in result["notes"])
    async with (
        lightroom(safelight_home, threshold=0) as _,
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        refused = await mcp.call_tool("straighten_photos", args)
        assert refused.is_error and "threshold" in refused.content[0].text


async def test_undo_straighten_puts_back_and_leaves_photos_to_the_user(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        ids = ["FAKE-0001", "FAKE-0002", "FAKE-0003", "FAKE-0004", "FAKE-0005"]
        for i in ids:
            catalog.photos[i]["settings"].update(EARLIER)
        await mcp.call_tool("straighten_photos", {"photo_ids": ids})
        wrong, by_hand, moved, validated, kept = (catalog.photos[i] for i in ids)
        by_hand["settings"].update(PerspectiveUpright=0, CropTop=0.05)  # changed by hand
        moved["settings"]["UprightTransform_5"] = "1,0,0.01,0,1,0,0,0,1"  # the user moved it
        validated["validated"] = True
        args = {"photo_ids": [*ids[:4], "FAKE-0006"]}
        result = (await mcp.call_tool("undo_straighten", args)).structured_content["result"]

        assert [e["id"] for e in result["put_back"]] == ["FAKE-0001"]
        _assert_put_back(wrong)  # the transform and crop from before
        assert [e["id"] for e in result["marked_only"]] == ["FAKE-0002"]
        assert by_hand["settings"]["PerspectiveUpright"] == 0
        assert by_hand["settings"]["CropTop"] == 0.05  # what the user did stays
        assert wrong["geometry_by"] == by_hand["geometry_by"] == "by_hand"
        assert wrong["label"] == "yellow"  # Safelight's color for photos to do by hand
        assert moved["geometry_by"] == "safelight" and kept["geometry_by"] == "safelight"
        reasons = {e["id"]: e["reason"] for e in result["skipped"]}
        assert "changed since" in reasons["FAKE-0003"]
        assert "validated" in reasons["FAKE-0004"]
        assert "not straightened by Safelight" in reasons["FAKE-0006"]
        review = (await HubClient(safelight_home).review())["review"]
        assert {e["id"] for e in review if "by hand" in e["reason"]} == {"FAKE-0001", "FAKE-0002"}

        again = await mcp.call_tool("straighten_photos", {"photo_ids": ids[:2]})
        assert again.is_error and "2 photos already" in again.content[0].text
        assert wrong["settings"]["PerspectiveUpright"] == 0  # left to the user


async def test_undo_straighten_finishes_what_an_interrupted_run_left(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    real_apply = FakeCatalog.develop_apply
    refuse = {"on": True}

    async def flaky_apply(self, params, progress):
        if refuse["on"] and "CropTop" in params["settings"] and not params.get("dry_run"):
            raise CommandError("COMMAND_FAILED", "Lightroom is busy")
        return await real_apply(self, params, progress)

    monkeypatch.setattr(FakeCatalog, "develop_apply", flaky_apply)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        photo = catalog.photos["FAKE-0001"]
        photo["settings"].update(EARLIER)
        refuse["on"] = False
        await mcp.call_tool("straighten_photos", {"photo_ids": ["FAKE-0001"]})
        refuse["on"] = True
        args = {"photo_ids": ["FAKE-0001"]}
        first = (await mcp.call_tool("undo_straighten", args)).structured_content["result"]
        assert "run it again" in first["not_done"][0]["reason"]
        assert photo["settings"]["PerspectiveUpright"] == 0
        assert photo["geometry_by"] == "safelight"  # not yet left to the user
        check = await mcp.call_tool("straighten_photos", {**args, "check_only": True})
        assert (
            "taken off"
            in check.structured_content["result"]["already_corrected_skipped"][0]["reason"]
        )
        refuse["on"] = False
        again = (await mcp.call_tool("undo_straighten", args)).structured_content["result"]
        assert [e["id"] for e in again["put_back"]] == ["FAKE-0001"]
        _assert_put_back(photo)
        assert photo["geometry_by"] == "by_hand"


async def test_undo_straighten_goes_back_to_before_the_first_correction(
    safelight_home, monkeypatch
):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        first, mine = catalog.photos["FAKE-0001"], catalog.photos["FAKE-0002"]
        first["settings"].update(EARLIER)
        mine["settings"].update(EARLIER)
        mine["geometry_by"] = "user"  # corrected by the user with sliders
        ids = ["FAKE-0001", "FAKE-0002"]
        await mcp.call_tool(
            "straighten_photos", {"photo_ids": ids, "replace_existing_upright": True}
        )
        await mcp.call_tool(
            "straighten_photos", {"photo_ids": ids, "replace_existing_upright": True}
        )
        await mcp.call_tool("straighten_photos", {"chosen_lines": {"FAKE-0001": [1, 2, 3, 4]}})
        result = await mcp.call_tool("undo_straighten", {"photo_ids": ids})
        assert result.structured_content["result"]["summary"]["put_back"] == 2
        _assert_put_back(first)
        _assert_put_back(mine)
        assert first["geometry_by"] == "by_hand"
        assert mine["geometry_by"] == "user"  # the user's own correction is back


async def test_undo_straighten_respects_what_happened_since(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    real_set = FakeCatalog.metadata_set
    refuse = {"on": False}

    async def flaky_set(self, params, progress):
        if refuse["on"] and not params.get("dry_run"):
            raise CommandError("COMMAND_FAILED", "Lightroom is busy")
        return await real_set(self, params, progress)

    monkeypatch.setattr(FakeCatalog, "metadata_set", flaky_set)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        ids = ["FAKE-0001", "FAKE-0002", "FAKE-0003", "FAKE-0004"]
        for i in ids:
            catalog.photos[i]["settings"].update(EARLIER)
        await mcp.call_tool("straighten_photos", {"photo_ids": ids})
        taken_off, recropped, field_failed, twice = (catalog.photos[i] for i in ids)

        # A: correction taken off, then straightened again: undo still goes to EARLIER.
        taken_off["settings"]["PerspectiveUpright"] = 0
        args = {"photo_ids": ["FAKE-0001"], "replace_existing_upright": True}
        await mcp.call_tool("straighten_photos", args)
        # C: the user cropped the empty corners: not touched.
        recropped["settings"].update(CropTop=0.1, CropLeft=0.1)
        kept = dict(recropped["settings"])
        result = await mcp.call_tool("undo_straighten", {"photo_ids": ids[:2]})
        result = result.structured_content["result"]
        _assert_put_back(taken_off)
        assert recropped["settings"] == kept and recropped["geometry_by"] == "safelight"
        assert "crop" in result["skipped"][0]["reason"]

        # B: put back, but the field failed: the next run only sets the field.
        refuse["on"] = True
        await mcp.call_tool("undo_straighten", {"photo_ids": ["FAKE-0003"]})
        refuse["on"] = False
        _assert_put_back(field_failed)
        assert field_failed["geometry_by"] == "safelight"
        again = await mcp.call_tool("undo_straighten", {"photo_ids": ["FAKE-0003"]})
        assert [e["id"] for e in again.structured_content["result"]["marked_only"]] == ["FAKE-0003"]
        assert field_failed["geometry_by"] == "by_hand"

        # D: running it twice changes nothing the second time.
        await mcp.call_tool("undo_straighten", {"photo_ids": ["FAKE-0004"]})
        before = dict(twice["settings"])
        second = await mcp.call_tool("undo_straighten", {"photo_ids": ["FAKE-0004"]})
        second = second.structured_content["result"]
        assert second["summary"]["put_back"] == 0
        assert second["skipped"][0]["reason"] == "already put back"
        assert twice["settings"] == before


async def _until(condition, seconds=5.0):
    for _ in range(int(seconds / 0.05)):
        if condition():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_colors_follow_the_state_and_only_safelight_s_own(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    monkeypatch.setattr(hub_module, "UPKEEP_EVERY_S", 0.1)
    async with (
        lightroom(safelight_home) as (catalog, client),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        ids = ["FAKE-0001", "FAKE-0002", "FAKE-0003", "FAKE-0004"]
        mine = catalog.photos["FAKE-0004"]
        mine["label"] = "green"  # the user's own color
        await mcp.call_tool("straighten_photos", {"photo_ids": ids})
        done, to_redo, old, _ = (catalog.photos[i] for i in ids)
        assert done["label"] == "purple" and done["safelight_label"] == "purple"
        assert mine["label"] == "green" and mine["safelight_label"] is None
        old["safelight_label"] = None  # marked by a Safelight that kept no record of it

        await mcp.call_tool(
            "undo_straighten", {"photo_ids": ["FAKE-0002", "FAKE-0003", "FAKE-0004"]}
        )
        assert to_redo["label"] == "yellow" and old["label"] == "yellow"
        assert mine["label"] == "green"  # never changed

        await client.review(add=[{"id": "FAKE-0001", "reason": "check it"}])
        done["validated"] = True  # the user ticks Validated in Lightroom
        assert await _until(lambda: done["label"] is None)
        assert done["safelight_label"] is None
        review = (await client.review())["review"]
        assert "FAKE-0001" not in {e["id"] for e in review}
        assert {"FAKE-0002", "FAKE-0003"} <= {e["id"] for e in review}  # still to do by hand


async def test_safelight_collections_follow_the_settings(safelight_home):
    async with lightroom(safelight_home) as (catalog, client):
        assert await _until(lambda: catalog.safelight_set is not None)
        assert catalog.safelight_set == {
            "set": "Safelight",
            "to_check": "To check",
            "by_hand": "To straighten by hand",
            "validated": "Validated",
        }
        await client.settings({"collections": {"to_check": "À vérifier"}})
        assert await _until(lambda: catalog.safelight_set["to_check"] == "À vérifier")


async def test_no_upkeep_in_read_only_mode(safelight_home, monkeypatch):
    monkeypatch.setattr(hub_module, "UPKEEP_EVERY_S", 0.1)
    async with lightroom(safelight_home, read_only=True) as (catalog, _):
        catalog.photos["FAKE-0001"].update(validated=True, label="purple", safelight_label="purple")
        await asyncio.sleep(1.5)
        assert catalog.safelight_set is None
        assert catalog.photos["FAKE-0001"]["label"] == "purple"


async def test_undo_straighten_needs_a_threshold(safelight_home):
    async with (
        lightroom(safelight_home, threshold=0) as _,
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        refused = await mcp.call_tool("undo_straighten", {"photo_ids": ["FAKE-0001"]})
        assert refused.is_error and "threshold" in refused.content[0].text


async def test_straighten_leaves_photos_the_user_corrects(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", lambda *_, **__: _plan())
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        catalog.photos["FAKE-0001"]["geometry_by"] = "user"  # with sliders, no Upright
        catalog.photos["FAKE-0002"]["geometry_by"] = "by_hand"
        args = {"photo_ids": ["FAKE-0001", "FAKE-0002", "FAKE-0003"], "check_only": True}
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        reasons = {e["id"]: e.get("reason") for e in result["already_corrected_skipped"]}
        assert "corrected by the user" in reasons["FAKE-0001"]
        assert "to straighten by hand" in reasons["FAKE-0002"]
        assert [e["id"] for e in result["to_straighten"]] == ["FAKE-0003"]
        args = {"photo_ids": ["FAKE-0002"], "replace_existing_upright": True}
        await mcp.call_tool("straighten_photos", args)
        assert catalog.photos["FAKE-0002"]["settings"]["PerspectiveUpright"] == 5


async def test_undo_straighten_needs_the_settings_from_before(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        await mcp.call_tool("straighten_photos", {"photo_ids": ["FAKE-0001"]})
        path = straighten_store.store_dir(safelight_home) / "FAKE-0001.json"
        data = json.loads(path.read_text())
        del data["before"]  # straightened by an older Safelight
        path.write_text(json.dumps(data))
        before = dict(catalog.photos["FAKE-0001"]["settings"])
        args = {"photo_ids": ["FAKE-0001"]}
        result = (await mcp.call_tool("undo_straighten", args)).structured_content["result"]
        assert "History" in result["skipped"][0]["reason"]
        assert catalog.photos["FAKE-0001"]["settings"] == before
        assert catalog.photos["FAKE-0001"]["geometry_by"] == "safelight"


async def test_straighten_labels_only_what_it_changed(safelight_home, monkeypatch):
    plans = iter([_plan(), None, _plan()])
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", lambda *_, **__: next(plans))
    ids = ["FAKE-0001", "FAKE-0002", "FAKE-0003"]
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        catalog.photos["FAKE-0003"]["label"] = "red"  # the user's own label
        args = {"photo_ids": ids, "label": "purple"}
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        labels = [catalog.photos[i]["label"] for i in ids]
    assert labels == ["purple", None, "red"]
    assert [e["label"] for e in result["straightened"]] == ["purple", "kept red"]


async def test_straighten_puts_photos_back_after_an_error(safelight_home, monkeypatch):
    def broken(*_, **__):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_server, "plan_from_jpeg", broken)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        for photo in catalog.photos.values():
            photo["settings"].update(EARLIER)
        result = await mcp.call_tool("straighten_photos", {})
        selected = [catalog.photos[i] for i in catalog.selected]
    assert result.is_error
    for photo in selected:
        _assert_put_back(photo)


async def test_straighten_puts_photos_back_when_cancelled(safelight_home, monkeypatch):
    started = asyncio.Event()

    async def stuck(*_, **__):
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(mcp_server, "fetch_previews", stuck)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        for photo in catalog.photos.values():
            photo["settings"].update(EARLIER)
        call = asyncio.create_task(mcp.call_tool("straighten_photos", {}))
        await started.wait()
        selected = [catalog.photos[i] for i in catalog.selected]
        assert selected[0]["settings"]["PerspectiveVertical"] == 0
        call.cancel()
        for _ in range(200):
            if all(p["settings"]["PerspectiveVertical"] == 7 for p in selected):
                break
            await asyncio.sleep(0.02)
    for photo in selected:
        _assert_put_back(photo)


# -- choosing the guides --------------------------------------------------------------------

LINES = [
    (0.1, 0.1, 0.1, 0.9),  # 1: left edge of a panel
    (0.9, 0.1, 0.9, 0.9),  # 2: right edge
    (0.2, 0.2, 0.8, 0.2),  # 3: top of a print
    (0.2, 0.8, 0.8, 0.8),  # 4: bottom of the print
    (0.4, 0.1, 0.43, 0.9),  # 5: a shadow, leaning
]


def _found_plan(*_, **__):
    lines = [Line(segment, 0.5, 0.0) for segment in LINES]
    return plan_from_lines(lines, [0, 1], 6000, 4000, 2833.0)  # the automatic guides


async def test_straighten_lines_then_chosen_lines(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        await mcp.call_tool("straighten_photos", {"photo_ids": ["FAKE-0001"]})
        shown = await mcp.call_tool("straighten_lines", {"photo_ids": ["FAKE-0001"]})
        images = [c for c in shown.content if isinstance(c, types.ImageContent)]
        text = "\n".join(c.text for c in shown.content if isinstance(c, types.TextContent))
        assert len(images) == 1 and images[0].mime_type == "image/jpeg"
        assert "1. vertical" in text and "3. horizontal" in text
        assert text.count("[guide]") == 2

        photo = catalog.photos["FAKE-0001"]
        assert photo["label"] == "purple"  # the default color for changed photos
        args = {"chosen_lines": {"FAKE-0001": [1, 2, 3, 4]}, "label": "yellow"}
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        assert [e["lines"] for e in result["straightened"]] == [[1, 2, 3, 4]]
        assert result["straightened"][0]["model"] == "exact"
        assert result["straightened"][0]["label"] == "yellow"  # its own color: changed
        assert photo["settings"]["UprightFourSegmentsCount"] == 4

        # The new choice is remembered, and can be changed again.
        again = await mcp.call_tool("straighten_lines", {"photo_ids": ["FAKE-0001"]})
        text = "\n".join(c.text for c in again.content if isinstance(c, types.TextContent))
        assert text.count("[guide]") == 4
        args = {"chosen_lines": {"FAKE-0001": [1, 2]}}
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        assert result["summary"]["straightened"] == 1


async def test_chosen_lines_leave_doubtful_or_changed_photos_alone(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        ids = ["FAKE-0001", "FAKE-0002"]
        await mcp.call_tool("straighten_photos", {"photo_ids": ids})
        edited = catalog.photos["FAKE-0002"]
        edited["settings"]["UprightTransform_5"] = "1,0,0.01,0,1,0,0,0,1"  # moved by the user
        before = {i: dict(catalog.photos[i]["settings"]) for i in ids}
        args = {
            "chosen_lines": {
                "FAKE-0001": [1, 5],  # a shadow: disagrees with the automatic verticals
                "FAKE-0002": [1, 2, 3, 4],
                "FAKE-0003": [1, 2],  # never straightened
            },
            "label": "yellow",
        }
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        after = {i: dict(catalog.photos[i]["settings"]) for i in ids}
    assert after == before
    assert [e["id"] for e in result["left_to_user"]] == ["FAKE-0001"]
    assert result["left_to_user"][0]["automatic"] == [1, 2]
    assert result["left_to_user"][0]["disagreement_deg"] > 0.45
    assert [e["id"] for e in result["already_corrected_skipped"]] == ["FAKE-0002"]
    assert "kept no lines" in result["not_straightened"][0]["reason"]
    assert result["summary"]["straightened"] == 0
    assert catalog.photos["FAKE-0001"]["label"] != "yellow"


async def test_chosen_lines_refuse_lines_found_before_a_lens_change(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", _found_plan)
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        await mcp.call_tool("straighten_photos", {"photo_ids": ["FAKE-0001"]})
        photo = catalog.photos["FAKE-0001"]
        photo["settings"]["LensProfileEnable"] = 1  # the lines moved with the distortion
        before = dict(photo["settings"])
        args = {"chosen_lines": {"FAKE-0001": [1, 2, 3, 4]}}
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
    assert photo["settings"] == before
    assert "lens corrections" in result["not_straightened"][0]["reason"]


async def test_straighten_lines_needs_a_straightened_photo(safelight_home):
    async with lightroom(safelight_home), Client(build_server(HubClient(safelight_home))) as mcp:
        result = await mcp.call_tool("straighten_lines", {"photo_ids": ["FAKE-0001"]})
    assert result.is_error and "straighten_photos first" in result.content[0].text


async def test_straighten_asks_first_and_touches_nothing(safelight_home):
    async with (
        lightroom(safelight_home, threshold=1) as (catalog, _),
        Client(build_server(HubClient(safelight_home)), mode="legacy") as mcp,
    ):
        before = {i: dict(p["settings"]) for i, p in catalog.photos.items()}
        asked = (await mcp.call_tool("straighten_photos", {})).structured_content
        after = {i: dict(p["settings"]) for i, p in catalog.photos.items()}
    assert asked["state"] == "confirmation_required"
    assert after == before


async def test_straighten_refuses_a_zero_threshold(safelight_home):
    async with (
        lightroom(safelight_home, threshold=0) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        result = await mcp.call_tool("straighten_photos", {})
    assert result.is_error
    assert "PerspectiveUpright" not in catalog.photos["FAKE-0001"]["settings"]


# -- the user's preferences -----------------------------------------------------------------


async def test_straighten_follows_color_and_snapshot_preferences(safelight_home, monkeypatch):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", lambda *_, **__: _plan())
    async with (
        lightroom(safelight_home, change_label="none", snapshots=False) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        result = await mcp.call_tool("straighten_photos", {"photo_ids": ["FAKE-0001"]})
        photo = catalog.photos["FAKE-0001"]
        assert photo["label"] is None and photo["snapshots"] == []
        assert "no snapshot" in " ".join(result.structured_content["result"]["notes"])
        # Asking for a color still works when the default is none.
        args = {"photo_ids": ["FAKE-0002"], "label": "green"}
        await mcp.call_tool("straighten_photos", args)
        assert catalog.photos["FAKE-0002"]["label"] == "green"

    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        await mcp.call_tool("straighten_photos", {"photo_ids": ["FAKE-0003"]})
        photo = catalog.photos["FAKE-0003"]
        assert photo["label"] == "purple"
        assert [s["name"] for s in photo["snapshots"]] == [mcp_server.STRAIGHTEN_SNAPSHOT]


async def test_real_lens_names_are_shown_and_searchable(safelight_home):
    names = [{"lightroom": "DT 17mm F4 SAM", "real": "Canon TS-E 17mm f/4L"}]
    async with (
        lightroom(safelight_home, lens_names=names) as (_, _client),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        shown = await mcp.call_tool("get_photos", {"photo_ids": ["FAKE-0008", "FAKE-0001"]})
        photos = shown.structured_content["photos"]
        assert photos[0]["lens"] == "Canon TS-E 17mm f/4L"
        assert photos[0]["lens_in_lightroom"] == "DT 17mm F4 SAM"
        assert photos[1]["lens"] == "FE 24-70mm F4 ZA OSS" and "lens_in_lightroom" not in photos[1]
        for asked in ("Canon TS-E 17mm f/4L", "ts-e 17", "DT 17mm F4 SAM"):
            found = await mcp.call_tool("search_photos", {"filters": {"lens": asked}})
            assert [p["uuid"] for p in found.structured_content["photos"]] == ["FAKE-0008"], asked
        status = (await mcp.call_tool("safelight_status", {})).structured_content
        assert status["change_label"] == "purple" and status["snapshots"] is True


# -- recipes and guides -------------------------------------------------------------------


async def test_recipes_and_guides(safelight_home):
    async with Client(build_server(HubClient(safelight_home))) as mcp:
        prompts = {p.name: p for p in (await mcp.list_prompts()).prompts}
        assert set(prompts) == {"straighten-exhibition-folder", "sort-duplicates"}
        assert [a.name for a in prompts["sort-duplicates"].arguments] == ["folder"]
        recipe = await mcp.get_prompt("straighten-exhibition-folder", {"folder": "2026-11-19"})
        text = recipe.messages[0].content.text
        assert '"2026-11-19"' in text and '{"folder": "2026-11-19"}' in text
        assert "never choose shadows" in text and "{" + "folder}" not in text

        guides = {str(r.uri) for r in (await mcp.list_resources()).resources}
        assert guides == {"safelight://guides/guided-upright", "safelight://guides/shift-lenses"}
        read = await mcp.read_resource("safelight://guides/shift-lenses")
        assert "optical centre" in read.contents[0].text

        guide = await mcp.call_tool("safelight_guide", {"topic": "sort-duplicates"})
        text = guide.content[0].text
        assert "Sharpness alone" in text and "the folder the user names" in text


async def test_straighten_never_touches_validated_photos_and_records_its_work(
    safelight_home, monkeypatch
):
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", lambda *_, **__: _plan())
    async with (
        lightroom(safelight_home) as (catalog, _),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        catalog.photos["FAKE-0001"]["validated"] = True
        before = dict(catalog.photos["FAKE-0001"]["settings"])
        args = {"photo_ids": ["FAKE-0001", "FAKE-0002"]}
        result = (await mcp.call_tool("straighten_photos", args)).structured_content["result"]
        assert [e["id"] for e in result["validated_skipped"]] == ["FAKE-0001"]
        assert catalog.photos["FAKE-0001"]["settings"] == before
        assert catalog.photos["FAKE-0001"]["label"] is None
        assert catalog.photos["FAKE-0002"]["geometry_by"] == "safelight"
        assert catalog.photos["FAKE-0002"]["label"] == "purple"

        only = await mcp.call_tool("straighten_photos", {"photo_ids": ["FAKE-0001"]})
        assert only.is_error and "validated" in str(only.content)


async def test_photos_needing_the_users_eye_go_to_the_app(safelight_home, monkeypatch):
    warned = _plan()
    warned.warnings = ["the guides do not fit one view: maybe a wrong line, check it"]
    plans = iter([warned, _plan()])
    monkeypatch.setattr(mcp_server, "plan_from_jpeg", lambda *_, **__: next(plans))
    async with (
        lightroom(safelight_home) as (_catalog, client),
        Client(build_server(HubClient(safelight_home))) as mcp,
    ):
        await mcp.call_tool("straighten_photos", {"photo_ids": ["FAKE-0001", "FAKE-0002"]})
        review = (await client.review())["review"]
        assert [e["id"] for e in review] == ["FAKE-0001"]
        assert "wrong line" in review[0]["reason"] and review[0]["file"] == "DSC00001.ARW"
        await client.review(done=["FAKE-0001"])
        assert (await client.review())["review"] == []
