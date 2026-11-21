import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from safelight import __version__
from safelight.hub_client import HubClient
from safelight.mcp_server import build_server

pytestmark = pytest.mark.anyio


async def test_server_lists_status_tool_as_read_only(safelight_home):
    async with Client(build_server(HubClient(safelight_home))) as client:
        tools = (await client.list_tools()).tools
        status_tool = next(tool for tool in tools if tool.name == "safelight_status")
        assert status_tool.annotations is not None
        assert status_tool.annotations.read_only_hint is True


async def test_status_starts_the_hub_and_reports_lightroom_missing(safelight_home):
    async with Client(build_server(HubClient(safelight_home))) as client:
        result = await client.call_tool("safelight_status", {})
        assert not result.is_error
        status = result.structured_content
        assert status["safelight_version"] == __version__
        assert status["hub"]["available"] is True, status["hub"]["detail"]
        assert status["lightroom"]["available"] is False
    assert (safelight_home / "hub.json").exists()


async def test_stdio_entry_point_starts_without_lightroom(safelight_home):
    """What AI clients actually launch: `safelight mcp` must start even if Lightroom is closed."""
    # The MCP stdio client passes only a minimal environment by default: hand over ours so the
    # hub the relay starts uses the test's isolated home and a free port.
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "safelight.cli", "mcp"], env=dict(os.environ)
    )
    async with Client(params) as client:
        result = await client.call_tool("safelight_status", {})
        assert not result.is_error
        assert result.structured_content["lightroom"]["available"] is False
