"""MCP server exposed to AI clients (Claude Desktop, Claude Code, Cursor, Codex...).

The server always starts, whether or not Lightroom Classic is open. It forwards
work to the Safelight hub, starting the hub if needed, and every tool reports
Lightroom's state instead of failing at startup.
"""

from __future__ import annotations

import time

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .hub_client import HubClient, HubUnavailable

INSTRUCTIONS = """\
Safelight lets you work in the user's Adobe Lightroom Classic catalog.
Call safelight_status first: it never fails and tells you whether Lightroom is open
and connected. Safelight is not affiliated with Adobe.
"""


class ComponentStatus(BaseModel):
    available: bool = Field(description="True when this component is reachable.")
    detail: str = Field(description="What the AI client should tell the user about it.")


class LightroomStatus(ComponentStatus):
    lightroom_version: str | None = None
    plugin_version: str | None = None
    catalog: str | None = None
    round_trip_ms: float | None = Field(
        default=None, description="Time for a ping to reach Lightroom and come back."
    )


class StatusResult(BaseModel):
    safelight_version: str
    hub: ComponentStatus
    lightroom: LightroomStatus


async def collect_status(client: HubClient) -> StatusResult:
    try:
        status = await client.status()
    except HubUnavailable as exc:
        return StatusResult(
            safelight_version=__version__,
            hub=ComponentStatus(available=False, detail=f"The Safelight hub is unavailable: {exc}"),
            lightroom=LightroomStatus(
                available=False, detail="Unknown, because the hub is unavailable."
            ),
        )

    hub = ComponentStatus(
        available=True,
        detail=f"Safelight hub {status['hub']['version']} is running.",
    )
    plugin = status.get("plugin") or {}
    if not plugin.get("connected"):
        return StatusResult(
            safelight_version=__version__,
            hub=hub,
            lightroom=LightroomStatus(
                available=False,
                detail="Lightroom Classic is not connected. Ask the user to open Lightroom "
                "Classic with the Safelight plugin enabled; it connects automatically.",
            ),
        )

    started = time.perf_counter()
    ping = await client.call("system.ping", timeout_s=5.0)
    round_trip = round((time.perf_counter() - started) * 1000, 1) if ping.get("success") else None
    return StatusResult(
        safelight_version=__version__,
        hub=hub,
        lightroom=LightroomStatus(
            available=bool(ping.get("success")),
            detail="Lightroom Classic is connected."
            if ping.get("success")
            else f"Lightroom Classic is connected but did not answer: {ping.get('error')}",
            lightroom_version=plugin.get("lightroom_version"),
            plugin_version=plugin.get("plugin_version"),
            catalog=plugin.get("catalog"),
            round_trip_ms=round_trip,
        ),
    )


def build_server(client: HubClient | None = None) -> MCPServer:
    hub_client = client or HubClient()
    server = MCPServer(
        name="safelight",
        title="Safelight for Lightroom Classic",
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    @server.tool(
        annotations=ToolAnnotations(
            title="Safelight status",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    async def safelight_status() -> StatusResult:
        """Report whether the Safelight hub is running and Lightroom Classic is connected.

        This tool never fails. Call it before anything else, and again whenever another
        tool says Lightroom is unavailable.
        """
        return await collect_status(hub_client)

    return server
