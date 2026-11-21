import pytest
from mcp import Client

from safelight import __version__
from safelight.mcp_server import build_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_server_starts_and_lists_status_tool():
    async with Client(build_server()) as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        assert "safelight_status" in names
        status_tool = next(tool for tool in tools.tools if tool.name == "safelight_status")
        assert status_tool.annotations is not None
        assert status_tool.annotations.read_only_hint is True


@pytest.mark.anyio
async def test_status_never_fails_without_lightroom():
    async with Client(build_server()) as client:
        result = await client.call_tool("safelight_status", {})
        assert not result.is_error
        status = result.structured_content
        assert status["safelight_version"] == __version__
        assert status["lightroom"]["available"] is False


@pytest.mark.anyio
async def test_stdio_entry_point_starts_without_lightroom():
    """What AI clients actually launch: `safelight mcp` must start even if Lightroom is closed."""
    import sys

    from mcp import StdioServerParameters

    params = StdioServerParameters(command=sys.executable, args=["-m", "safelight.cli", "mcp"])
    async with Client(params) as client:
        result = await client.call_tool("safelight_status", {})
        assert not result.is_error
        assert result.structured_content["lightroom"]["available"] is False
