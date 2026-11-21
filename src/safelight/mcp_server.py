"""MCP server exposed to AI clients (Claude Desktop, Claude Code, Cursor, Codex...).

The server must always start, whether or not Lightroom Classic is open. Every tool
reports Lightroom's state instead of failing at startup. In this foundation stage
the hub is not implemented yet, so the server only answers `safelight_status`.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__

INSTRUCTIONS = """\
Safelight lets you work in the user's Adobe Lightroom Classic catalog.
Call safelight_status first: it never fails and tells you whether Lightroom is open
and connected. Safelight is not affiliated with Adobe.
"""


class ComponentStatus(BaseModel):
    available: bool = Field(description="True when this component is reachable.")
    detail: str = Field(description="What the AI client should tell the user about it.")


class StatusResult(BaseModel):
    safelight_version: str
    hub: ComponentStatus
    lightroom: ComponentStatus


def build_server() -> MCPServer:
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
    def safelight_status() -> StatusResult:
        """Report whether the Safelight hub is running and Lightroom Classic is connected.

        This tool never fails. Call it before anything else, and again whenever another
        tool says Lightroom is unavailable.
        """
        return StatusResult(
            safelight_version=__version__,
            hub=ComponentStatus(
                available=False,
                detail="The Safelight hub is not implemented yet in this development build.",
            ),
            lightroom=ComponentStatus(
                available=False,
                detail="Lightroom Classic connection is not implemented yet.",
            ),
        )

    return server
