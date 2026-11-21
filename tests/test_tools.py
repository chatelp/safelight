"""The MCP tools, end to end: MCP client -> relay -> hub -> fake plugin with a fake catalog."""

import asyncio
import contextlib

import mcp.types as types
import pytest
from mcp import Client

from safelight.devtools.fake_catalog import FakeCatalog
from safelight.devtools.fake_plugin import FakePlugin
from safelight.hub import Hub, HubServer
from safelight.hub_client import HubClient
from safelight.mcp_server import build_server
from safelight.settings import Settings

pytestmark = pytest.mark.anyio


@contextlib.asynccontextmanager
async def lightroom(home, threshold=50):
    hub = Hub(settings=Settings(confirm_threshold=threshold))
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
    "safelight_status",
    "safelight_job",
    "safelight_cancel_job",
    "get_photos",
    "search_photos",
    "preview_photo",
    "contact_sheet",
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
}


async def test_tool_list_and_read_only_annotations(safelight_home):
    async with Client(build_server(HubClient(safelight_home))) as mcp:
        tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    read_only = {n for n, t in tools.items() if t.annotations and t.annotations.read_only_hint}
    assert read_only == {
        "safelight_status",
        "safelight_job",
        "get_photos",
        "search_photos",
        "preview_photo",
        "contact_sheet",
        "get_develop_settings",
        "list_catalog_items",
    }
    assert tools["change_mask"].annotations.destructive_hint is True
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
