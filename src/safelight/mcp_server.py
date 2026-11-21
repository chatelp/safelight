"""MCP server exposed to AI clients (Claude Desktop, Claude Code, Cursor, Codex...).

The server always starts, whether or not Lightroom Classic is open. It forwards
work to the Safelight hub, starting the hub if needed, and every tool reports
Lightroom's state instead of failing at startup.
"""

from __future__ import annotations

import time
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .hub_client import HubClient, HubUnavailable
from .protocol import JobState

INSTRUCTIONS = """\
Safelight lets you work in the user's Adobe Lightroom Classic catalog.
Call safelight_status first: it never fails and tells you whether Lightroom is open
and connected.

Long operations return a job with state "running" and a job_id: follow them with
safelight_job and stop them with safelight_cancel_job.

Some changes need the user's approval (many photos at once, or changes that are hard
to undo): the tool then returns state "confirmation_required" with a summary and a
token. Show the summary to the user and ask. Only if they explicitly approve, call the
same tool again with the same arguments plus that confirmation token. Never confirm on
your own.

Safelight is not affiliated with Adobe.
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
    read_only: bool | None = Field(
        default=None, description="When true, Safelight refuses every change to the catalog."
    )


class JobResult(BaseModel):
    job_id: str | None = None
    command: str | None = None
    state: str = Field(
        description="queued, running, succeeded, failed, cancelled or confirmation_required"
    )
    progress: dict[str, Any] | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    duration_ms: int | None = None
    cancel_requested: bool | None = None


async def collect_status(client: HubClient, client_name: str | None = None) -> StatusResult:
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
    read_only = (status.get("settings") or {}).get("read_only")
    plugin = status.get("plugin") or {}
    if not plugin.get("connected"):
        return StatusResult(
            safelight_version=__version__,
            hub=hub,
            read_only=read_only,
            lightroom=LightroomStatus(
                available=False,
                detail="Lightroom Classic is not connected. Ask the user to open Lightroom "
                "Classic with the Safelight plugin enabled; it connects automatically.",
            ),
        )

    started = time.perf_counter()
    ping = await client.run("system.ping", wait_s=5.0, client=client_name)
    ok = ping.get("state") == JobState.SUCCEEDED
    round_trip = round((time.perf_counter() - started) * 1000, 1) if ok else None
    return StatusResult(
        safelight_version=__version__,
        hub=hub,
        read_only=read_only,
        lightroom=LightroomStatus(
            available=ok,
            detail="Lightroom Classic is connected."
            if ok
            else f"Lightroom Classic is connected but did not answer: {ping.get('error')}",
            lightroom_version=plugin.get("lightroom_version"),
            plugin_version=plugin.get("plugin_version"),
            catalog=plugin.get("catalog"),
            round_trip_ms=round_trip,
        ),
    )


def _client_name(ctx: Context | None) -> str:
    """The AI client's own name (for the activity log), as it introduced itself."""
    try:
        info = ctx.session.client_params.client_info  # type: ignore[union-attr]
        return str(info.name)[:80] if info and info.name else "mcp-client"
    except AttributeError:
        return "mcp-client"


def build_server(client: HubClient | None = None) -> MCPServer:
    hub_client = client or HubClient(client_name="mcp-client")
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
    async def safelight_status(ctx: Context) -> StatusResult:
        """Report whether the Safelight hub is running and Lightroom Classic is connected.

        This tool never fails. Call it before anything else, and again whenever another
        tool says Lightroom is unavailable.
        """
        return await collect_status(hub_client, _client_name(ctx))

    @server.tool(
        annotations=ToolAnnotations(
            title="Follow a Safelight job",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    async def safelight_job(
        job_id: str,
        wait_s: float = Field(
            default=0, ge=0, le=60, description="Seconds to wait for the job to finish."
        ),
    ) -> JobResult:
        """Get the state, progress and result of a job started by another Safelight tool."""
        return JobResult.model_validate(await hub_client.job(job_id, wait_s))

    @server.tool(
        annotations=ToolAnnotations(
            title="Cancel a Safelight job",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    async def safelight_cancel_job(job_id: str) -> JobResult:
        """Ask Lightroom to stop a running job.

        A job still waiting is cancelled at once. A running job stops at its next
        progress step; changes already made to earlier photos are kept.
        """
        return JobResult.model_validate(await hub_client.cancel(job_id))

    return server
