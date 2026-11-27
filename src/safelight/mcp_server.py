"""MCP server exposed to AI clients (Claude Desktop, Claude Code, Cursor, Codex...).

The server always starts, whether or not Lightroom Classic is open. It forwards work to
the Safelight hub, starting the hub if needed. Read-only tools are marked as such, so
clients can run them without asking; tools that change the catalog go through the hub's
policy (read-only mode, dry run, confirmation of large or destructive changes).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import time
from typing import Annotated, Any, Literal

import anyio
import numpy as np
from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__, knowledge, straighten_store
from .analysis import findings, groups, measure, summarize, top_of_stacks
from .contact_sheet import SheetPhoto, render
from .hub_client import HubClient, HubUnavailable
from .protocol import JobState
from .search import PhotoFilters, build_search, has_post_filters, matches_post_filters
from .settings import Settings
from .upright import (
    DISAGREE_DEG,
    RESET_SETTINGS,
    Plan,
    disagreement_deg,
    geometry_settings,
    plan_from_jpeg,
    plan_from_lines,
)

STRAIGHTEN_SNAPSHOT = "Before Safelight straighten"
# How long a change waits for the user's answer in the Safelight app before the AI
# client asks instead.
APP_APPROVAL_WAIT_S = 120.0

INSTRUCTIONS = """\
Safelight lets you work in the user's Adobe Lightroom Classic catalog.

Start with safelight_status: it never fails, says whether Lightroom is connected, and
gives the active photo and the selection size.

Photos are identified by id (the `uuid` field of every photo description). Tools that
work on photos take `photo_ids`, or act on the photos selected in Lightroom (scope
"selected", the default) or on the active photo (scope "active").

Look before you change: use preview_photo to see a photo with its current edits and
contact_sheet to see many at once (each cell is numbered; the legend maps numbers to
ids). Develop values are the ones shown in Lightroom (Exposure in EV, most sliders
-100..100); edit_develop clamps out-of-range values and says so. Prefer relative changes
("relative": true) when the user asks for "a bit more" or "less".

Changes that touch many photos, or are hard to undo, need the user's approval. Depending
on the AI client, Safelight asks the user directly, or the tool returns state
"confirmation_required" with a summary and a token: show the summary to the user and
only if they approve, call the same tool again with the same arguments plus
`confirmation_token`. Never confirm on your own.

Long operations may return state "running" with a job_id: follow them with safelight_job
and stop them with safelight_cancel_job.

The user's stable choices live in Safelight's settings and are applied by Safelight
itself: the color label for photos it changes, snapshots, real lens names. Before
straightening an exhibition or sorting duplicates, read the method with safelight_guide
(also offered as prompts); background guides cover Lightroom's Guided Upright and shift
lenses.

Safelight is not affiliated with Adobe.
"""

# All four MCP hints, always explicit: some clients and directories require them, and
# tools that read the source only see literal values. Destructive means information can be
# lost that Lightroom's History panel does not keep (files, captions, keywords,
# snapshots...) or that all edits are reset. No tool reaches outside this Mac.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
CHANGES = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
CHANGES_ONCE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)
DESTRUCTIVE_ONCE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

PhotoIds = Annotated[
    list[str] | None,
    Field(description="Photo ids to act on. Leave empty to use the scope instead."),
]
Scope = Annotated[
    Literal["selected", "active"],
    Field(description="Without photo_ids: the photos selected in Lightroom, or the active one."),
]
ConfirmationToken = Annotated[
    str | None,
    Field(description="Token from a confirmation_required answer, once the user approved."),
]
MaskAdjustments = Annotated[
    dict[str, float],
    Field(
        description="Exposure (EV), Contrast, Highlights, Shadows, Whites, Blacks, Texture, "
        "Clarity, Dehaze, Temperature, Tint, Hue, Saturation, Sharpness, Noise, Moire, "
        "Defringe, Grain (-100..100), Amount (0..1)"
    ),
]


# -- results ------------------------------------------------------------------------------


class ComponentStatus(BaseModel):
    available: bool = Field(description="True when this component is reachable.")
    detail: str = Field(description="What the AI client should tell the user about it.")


class LightroomStatus(ComponentStatus):
    lightroom_version: str | None = None
    plugin_version: str | None = None
    catalog: str | None = None
    module: str | None = Field(default=None, description="Lightroom module shown now")
    active_photo_id: str | None = None
    selected_count: int | None = None
    round_trip_ms: float | None = Field(
        default=None, description="Time for a request to reach Lightroom and come back."
    )


class StatusResult(BaseModel):
    safelight_version: str
    hub: ComponentStatus
    lightroom: LightroomStatus
    read_only: bool | None = Field(
        default=None, description="When true, Safelight refuses every change to the catalog."
    )
    confirm_threshold: int | None = Field(
        default=None, description="Changes touching more photos than this need approval."
    )
    change_label: str | None = Field(
        default=None,
        description="Color label the user wants on photos Safelight changes (none: no label).",
    )
    snapshots: bool | None = Field(
        default=None,
        description="Whether the user wants a snapshot before Safelight changes a geometry.",
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


class ActionResult(BaseModel):
    state: Literal["succeeded", "running", "cancelled", "confirmation_required", "declined"]
    result: Any = None
    job_id: str | None = None
    progress: dict[str, Any] | None = None
    confirmation: dict[str, Any] | None = Field(
        default=None,
        description="When state is confirmation_required: summary, reason and token.",
    )
    message: str | None = None


class ApprovalForm(BaseModel):
    approve: bool = Field(description="Run this change in Lightroom Classic")


# -- helpers ------------------------------------------------------------------------------


def _client_name(ctx: Context | None) -> str:
    """The AI client's own name (for the activity log), as it introduced itself."""
    try:
        info = ctx.session.client_params.client_info  # type: ignore[union-attr]
        return str(info.name)[:80] if info and info.name else "mcp-client"
    except AttributeError:
        return "mcp-client"


def _supports_elicitation(ctx: Context | None) -> bool:
    try:
        capabilities = ctx.client_capabilities  # type: ignore[union-attr]
    except (AttributeError, LookupError, RuntimeError):
        return False
    return bool(capabilities and getattr(capabilities, "elicitation", None))


def _targets(photo_ids: list[str] | None, scope: str) -> dict[str, Any]:
    if photo_ids:
        return {"uuids": list(photo_ids)}
    return {"target": "active"} if scope == "active" else {}


def _single(photo_id: str | None) -> dict[str, Any]:
    return {"uuids": [photo_id]} if photo_id else {"target": "active"}


class Runner:
    """Runs commands through the hub and turns hub answers into tool results."""

    def __init__(self, client: HubClient) -> None:
        self.client = client

    async def preferences(self) -> Settings:
        """The user's settings, from the hub (defaults if it cannot answer)."""
        settings = Settings()
        with contextlib.suppress(HubUnavailable):
            data = await self.client.settings()
            settings.update(data if isinstance(data, dict) else {}, strict=False)
        return settings

    async def read(self, ctx: Context | None, command: str, params: dict[str, Any]) -> Any:
        reply = await self.client.run(command, params, wait_s=60, client=_client_name(ctx))
        if reply.get("state") != JobState.SUCCEEDED:
            self._raise(reply)
        return reply.get("result")

    async def change(
        self,
        ctx: Context | None,
        command: str,
        params: dict[str, Any],
        confirmation_token: str | None = None,
    ) -> ActionResult:
        name = _client_name(ctx)
        reply = await self.client.run(
            command, params, wait_s=30, confirm=confirmation_token, client=name
        )
        if reply.get("state") == JobState.CONFIRMATION_REQUIRED:
            # The Safelight app first, when it is open; then the AI client.
            answer = await self._ask_app(ctx, reply)
            token = (reply.get("confirmation") or {}).get("token")
            if answer == "approved":
                reply = await self.client.run(
                    command, params, wait_s=30, confirm=token, client=name
                )
            elif answer == "declined":
                return ActionResult(
                    state="declined", message="The user declined this change in the Safelight app."
                )
        if reply.get("state") == JobState.CONFIRMATION_REQUIRED and _supports_elicitation(ctx):
            reply = await self._ask_user(ctx, command, params, reply, name)
            if isinstance(reply, ActionResult):
                return reply
        return self.outcome(reply)

    async def _ask_app(self, ctx: Context | None, reply: dict[str, Any]) -> str | None:
        """Waits for the user's answer in the Safelight app: approved, declined, or None
        when the app is closed or nobody answered in time (then the AI client asks)."""
        approval_id = (reply.get("confirmation") or {}).get("approval_id")
        if not approval_id:
            return None
        try:
            status = await self.client.status(start=False)
            if not (status.get("app") or {}).get("connected"):
                return None
        except HubUnavailable:
            return None
        try:
            deadline = time.monotonic() + APP_APPROVAL_WAIT_S
            while (left := deadline - time.monotonic()) > 0:
                if ctx is not None:
                    with contextlib.suppress(Exception):  # keeps the client's request alive
                        await ctx.report_progress(
                            APP_APPROVAL_WAIT_S - left,
                            APP_APPROVAL_WAIT_S,
                            "Waiting for the user's approval in the Safelight app",
                        )
                answer = await self.client.approval(approval_id, wait_s=min(10.0, left))
                state = answer.get("state")
                if state in ("approved", "declined"):
                    return state
                if state != "pending":
                    return None
        except HubUnavailable:
            return None
        except BaseException:
            # The AI client cancelled: take the request out of the app, then stop.
            with anyio.CancelScope(shield=True), contextlib.suppress(HubUnavailable):
                await self.client.decide(approval_id, "withdrawn")
            raise
        # No answer in time: take the request out of the app; the AI client asks instead.
        # An answer given at the very last moment still counts.
        try:
            last = await self.client.decide(approval_id, "withdrawn")
        except HubUnavailable:
            return None
        state = last.get("state")
        return state if state in ("approved", "declined") else None

    async def _ask_user(
        self, ctx: Context | None, command: str, params: dict[str, Any], reply: dict, name: str
    ) -> dict[str, Any] | ActionResult:
        confirmation = reply.get("confirmation") or {}
        message = " ".join(
            part
            for part in (confirmation.get("summary"), confirmation.get("reason"), "Continue?")
            if part
        )
        try:
            answer = await ctx.elicit(message, ApprovalForm)  # type: ignore[union-attr]
        except Exception:  # the client advertised elicitation but could not ask: fall back
            return reply
        if answer.action == "accept" and answer.data is not None and answer.data.approve:
            return await self.client.run(
                command, params, wait_s=30, confirm=confirmation.get("token"), client=name
            )
        return ActionResult(state="declined", message="The user did not approve this change.")

    async def settle(self, reply: dict[str, Any]) -> dict[str, Any]:
        """Waits until a job that was still running is over."""
        while reply.get("state") in (JobState.RUNNING, JobState.QUEUED) and reply.get("job_id"):
            reply = await self.client.job(reply["job_id"], wait_s=30)
        return reply

    def outcome(self, reply: dict[str, Any]) -> ActionResult:
        state = reply.get("state")
        if state == JobState.SUCCEEDED:
            return ActionResult(state="succeeded", result=reply.get("result"))
        if state in (JobState.RUNNING, JobState.QUEUED):
            return ActionResult(
                state="running",
                job_id=reply.get("job_id"),
                progress=reply.get("progress"),
                message="Still running in Lightroom. Follow it with safelight_job.",
            )
        if state == JobState.CONFIRMATION_REQUIRED:
            confirmation = reply.get("confirmation") or {}
            return ActionResult(
                state="confirmation_required",
                confirmation={
                    key: confirmation.get(key)
                    for key in ("summary", "reason", "affected", "token", "expires_in_s")
                },
                message="Show the summary to the user. Only if they approve, call this tool "
                "again with the same arguments and confirmation_token.",
            )
        if state == JobState.CANCELLED:
            error = reply.get("error") or {}
            return ActionResult(state="cancelled", message=error.get("message"))
        self._raise(reply)
        raise AssertionError("unreachable")

    @staticmethod
    def _raise(reply: dict[str, Any]) -> None:
        error = reply.get("error") or {}
        raise ToolError(
            f"{error.get('message', 'Lightroom reported an error')} [{error.get('code')}]"
        )


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
    settings = status.get("settings") or {}
    plugin = status.get("plugin") or {}
    common = {
        "safelight_version": __version__,
        "hub": hub,
        "read_only": settings.get("read_only"),
        "confirm_threshold": settings.get("confirm_threshold"),
        "change_label": settings.get("change_label"),
        "snapshots": settings.get("snapshots"),
    }
    if not plugin.get("connected"):
        return StatusResult(
            **common,
            lightroom=LightroomStatus(
                available=False,
                detail="Lightroom Classic is not connected. Ask the user to open Lightroom "
                "Classic with the Safelight plugin enabled; it connects automatically.",
            ),
        )

    started = time.perf_counter()
    reply = await client.run("catalog.info", wait_s=5.0, client=client_name)
    ok = reply.get("state") == JobState.SUCCEEDED
    if not ok:  # older plugins only answer system.ping
        reply = await client.run("system.ping", wait_s=5.0, client=client_name)
        ok = reply.get("state") == JobState.SUCCEEDED
    info = reply.get("result") if ok and isinstance(reply.get("result"), dict) else {}
    round_trip = round((time.perf_counter() - started) * 1000, 1) if ok else None
    return StatusResult(
        **common,
        lightroom=LightroomStatus(
            available=ok,
            detail="Lightroom Classic is connected."
            if ok
            else f"Lightroom Classic is connected but did not answer: {reply.get('error')}",
            lightroom_version=plugin.get("lightroom_version"),
            plugin_version=plugin.get("plugin_version"),
            catalog=plugin.get("catalog"),
            module=info.get("module"),
            active_photo_id=info.get("active_photo"),
            selected_count=info.get("selected_count"),
            round_trip_ms=round_trip,
        ),
    )


MAX_SCANNED = 5000  # photos scanned when filtering results on fields Lightroom can't search


async def list_photos(
    runner: Runner,
    ctx: Context | None,
    *,
    photo_ids: list[str] | None = None,
    scope: str = "selected",
    filters: PhotoFilters | None = None,
    limit: int = 50,
    offset: int = 0,
    sort: str = "captureTime",
    ascending: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Photos from ids, the selection or a search; returns (page, total)."""
    preferences = await runner.preferences()
    if filters is None:
        listing = await runner.read(
            ctx, "photos.get", {**_targets(photo_ids, scope), "limit": limit, "offset": offset}
        )
        photos = real_lens_names(listing.get("photos") or [], preferences)
        return photos, int(listing.get("total") or 0)
    if filters.lens:
        filters = filters.model_copy(update={"lens": preferences.lightroom_lens(filters.lens)})
    page, total = await _search_photos(runner, ctx, filters, limit, offset, sort, ascending)
    return real_lens_names(page, preferences), total


def real_lens_names(photos: list[dict[str, Any]], preferences: Settings) -> list[dict[str, Any]]:
    """Shows the real name of lenses Lightroom names wrongly (the user's settings)."""
    for photo in photos:
        real = preferences.real_lens(photo.get("lens"))
        if real:
            photo["lens_in_lightroom"] = photo["lens"]
            photo["lens"] = real
    return photos


async def _search_photos(
    runner: Runner,
    ctx: Context | None,
    filters: PhotoFilters,
    limit: int,
    offset: int,
    sort: str,
    ascending: bool,
) -> tuple[list[dict[str, Any]], int]:
    search = build_search(filters)
    if search is None and not has_post_filters(filters):
        raise ToolError("Give at least one filter.")
    if search is None:
        raise ToolError("Add at least one filter Lightroom can search (for example a date).")
    base = {"search": search, "sort": sort, "ascending": ascending}
    if not has_post_filters(filters):
        listing = await runner.read(
            ctx, "photos.search", {**base, "limit": limit, "offset": offset}
        )
        return listing.get("photos") or [], int(listing.get("total") or 0)
    matched: list[dict[str, Any]] = []
    scanned, page_start = 0, 0
    while scanned < MAX_SCANNED:
        page = await runner.read(ctx, "photos.search", {**base, "limit": 500, "offset": page_start})
        photos = page.get("photos") or []
        matched.extend(p for p in photos if matches_post_filters(p, filters))
        scanned += len(photos)
        page_start += len(photos)
        if len(photos) < 500 or page_start >= int(page.get("total") or 0):
            break
    return matched[offset : offset + limit], len(matched)


async def fetch_previews(
    client: HubClient,
    ctx: Context | None,
    photos: list[dict[str, Any]],
    size: int,
    progress: Any = None,
) -> list[bytes | None]:
    """Lightroom previews of many photos, six at a time."""
    semaphore = asyncio.Semaphore(6)
    done = 0

    async def fetch(photo: dict[str, Any]) -> bytes | None:
        nonlocal done
        async with semaphore:
            reply = await client.run(
                "photos.preview",
                {"uuids": [photo["uuid"]], "size": size},
                wait_s=30,
                client=_client_name(ctx),
            )
        done += 1
        if progress is not None:
            await progress(done, len(photos))
        if reply.get("state") != JobState.SUCCEEDED:
            return None
        return base64.b64decode(reply["result"]["data"])

    return list(await asyncio.gather(*(fetch(photo) for photo in photos)))


# -- server -------------------------------------------------------------------------------


def build_server(client: HubClient | None = None) -> MCPServer:
    hub_client = client or HubClient(client_name="mcp-client")
    runner = Runner(hub_client)
    server = MCPServer(
        name="safelight",
        title="Safelight for Lightroom Classic",
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    knowledge.register(server)

    # -- status and jobs ------------------------------------------------------------------

    @server.tool(annotations=READ_ONLY)
    async def safelight_status(ctx: Context) -> StatusResult:
        """Whether Lightroom Classic is connected, plus the active photo and selection size.

        Never fails. Call it first, and again whenever another tool says Lightroom is
        unavailable.
        """
        return await collect_status(hub_client, _client_name(ctx))

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def safelight_guide(
        topic: Annotated[
            Literal[
                "straighten-exhibition-folder", "sort-duplicates", "guided-upright", "shift-lenses"
            ],
            Field(
                description="A recipe (straighten-exhibition-folder, sort-duplicates) or a guide"
            ),
        ],
        folder: Annotated[str | None, Field(description="For a recipe: the folder")] = None,
    ) -> str:
        """Safelight's method for a common job (recipe), or background on a topic (guide).
        Read the recipe before straightening an exhibition folder or sorting duplicates,
        and follow it."""
        return knowledge.ENTRIES[topic].text(folder)

    @server.tool(annotations=READ_ONLY)
    async def safelight_job(
        job_id: str,
        wait_s: Annotated[float, Field(ge=0, le=60, description="Seconds to wait")] = 0,
    ) -> JobResult:
        """State, progress and result of a job that another tool reported as running."""
        return JobResult.model_validate(await hub_client.job(job_id, wait_s))

    @server.tool(annotations=CHANGES_ONCE)
    async def safelight_cancel_job(job_id: str) -> JobResult:
        """Stop a running job. It stops at its next step; work already done is kept."""
        return JobResult.model_validate(await hub_client.cancel(job_id))

    # -- photos ---------------------------------------------------------------------------

    @server.tool(annotations=READ_ONLY)
    async def get_photos(
        ctx: Context,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Describe photos: file, format, capture date, rating, flag, label, camera, lens,
        exposure, GPS, title, caption, keywords. Defaults to the photos selected in Lightroom.
        """
        params = {**_targets(photo_ids, scope), "limit": limit, "offset": offset}
        listing = await runner.read(ctx, "photos.get", params)
        real_lens_names(listing.get("photos") or [], await runner.preferences())
        return listing

    @server.tool(annotations=READ_ONLY)
    async def search_photos(
        ctx: Context,
        filters: PhotoFilters,
        sort: Literal["captureTime", "fileName", "extension"] = "captureTime",
        newest_first: bool = True,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Find photos in the whole catalog. Photos must match every filter given.

        Use exact Lightroom names for camera and lens (look them up with get_photos on a
        known photo); for a lens the user renamed in Safelight's settings, its real name
        works too. Focal length, aperture and shutter speed are checked on the results,
        so combine them with a filter Lightroom can search (dates, camera, rating...).
        Returns `total` and one page of photo descriptions.
        """
        photos, total = await list_photos(
            runner,
            ctx,
            filters=filters,
            limit=limit,
            offset=offset,
            sort=sort,
            ascending=not newest_first,
        )
        return {"total": total, "offset": offset, "limit": limit, "photos": photos}

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def preview_photo(
        ctx: Context,
        photo_id: Annotated[str | None, Field(description="Defaults to the active photo")] = None,
        size: Annotated[int, Field(ge=128, le=2048, description="Long edge, pixels")] = 1024,
    ) -> list[Any]:
        """Look at one photo as Lightroom renders it now, with all its edits."""
        result = await runner.read(ctx, "photos.preview", {**_single(photo_id), "size": size})
        return [
            Image(data=base64.b64decode(result["data"]), format="jpeg"),
            f"Photo {result['uuid']} rendered by Lightroom ({result['bytes']} bytes).",
        ]

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def contact_sheet(
        ctx: Context,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        filters: Annotated[
            PhotoFilters | None, Field(description="Search the catalog instead of using ids")
        ] = None,
        max_photos: Annotated[int, Field(ge=1, le=48)] = 24,
        columns: Annotated[int, Field(ge=1, le=8)] = 6,
        cell_px: Annotated[int, Field(ge=128, le=512)] = 256,
    ) -> list[Any]:
        """See many photos at once in one numbered grid, to compare, cull or pick the best.

        Each cell shows the number, star rating, flag and color label. The text answer maps
        each number to the photo id, so you can act on the photos you choose.
        """
        photos, total = await list_photos(
            runner, ctx, photo_ids=photo_ids, scope=scope, filters=filters, limit=max_photos
        )
        if not photos:
            raise ToolError("No photos to show.")
        previews = await fetch_previews(hub_client, ctx, photos, cell_px if cell_px > 256 else 256)
        cells = [
            SheetPhoto(
                number=index + 1,
                jpeg=jpeg,
                caption=photo.get("file_name") or "",
                rating=int(photo.get("rating") or 0),
                pick=int(photo.get("pick") or 0),
                label=photo.get("label"),
            )
            for index, (photo, jpeg) in enumerate(zip(photos, previews, strict=True))
        ]
        legend = "\n".join(
            f"{cell.number}. {photo['uuid']}  {photo.get('file_name', '')}"
            for cell, photo in zip(cells, photos, strict=True)
        )
        header = f"{len(photos)} of {total} photos. Number -> photo id:"
        return [
            Image(data=render(cells, columns=columns, cell=cell_px), format="jpeg"),
            f"{header}\n{legend}",
        ]

    @server.tool(annotations=READ_ONLY)
    async def analyze_photos(
        ctx: Context,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        filters: Annotated[
            PhotoFilters | None, Field(description="Search the catalog instead of using ids")
        ] = None,
        checks: Annotated[
            list[Literal["bursts", "sharpness", "exposure", "horizon"]],
            Field(description="What to look for"),
        ] = ["bursts", "sharpness", "exposure", "horizon"],  # noqa: B006 - copied by pydantic
        burst_gap_s: Annotated[
            float, Field(ge=0.1, le=30, description="Max seconds between frames of a burst")
        ] = 2.0,
        max_photos: Annotated[int, Field(ge=2, le=1000)] = 300,
        include_stacked_frames: Annotated[
            bool,
            Field(
                description="Also analyze frames stacked under another photo (HDR brackets, "
                "panorama sources). Off by default: they are kept for the merge."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Help cull a shoot: group bursts and near-duplicates and pick the best frame of
        each, and point out photos that look blurred, badly exposed or tilted.

        Works on Lightroom's previews (with the current edits). The measures are
        heuristics: before rejecting or deleting anything, look at the doubtful photos
        with contact_sheet and ask the user. Nothing is changed in the catalog. To act,
        use set_metadata (flags, ratings, labels) with the photo ids returned here.
        Closed eyes are not detected; check faces on a contact sheet. Sharpness alone does
        not choose the best frame: look for lens flare, passers-by and reflections too
        (method: safelight_guide "sort-duplicates"). Groups marked choose_by_eye have
        frames about as sharp as each other: look at them (faces, gestures, light,
        framing) and let the user choose; best_photo_id is then only the sharpest.
        """
        photos, total = await list_photos(
            runner, ctx, photo_ids=photo_ids, scope=scope, filters=filters, limit=max_photos
        )
        skipped_stacked = 0
        if not include_stacked_frames:
            photos, skipped_stacked = top_of_stacks(photos)
        if len(photos) < 1:
            raise ToolError("No photos to analyze.")

        async def report(done: int, count: int) -> None:
            await ctx.report_progress(done, count, f"Rendered {done} of {count} previews")

        previews = await fetch_previews(hub_client, ctx, photos, 512, report)
        wanted = set(checks)
        with_tilt = "horizon" in wanted

        def compute() -> dict[str, Any]:
            stats = {
                photo["uuid"]: measure(jpeg, with_tilt=with_tilt)
                for photo, jpeg in zip(photos, previews, strict=True)
                if jpeg
            }
            reports = findings(photos, stats, wanted)
            grouped = groups(photos, stats, burst_gap_s) if "bursts" in wanted else []
            return {
                "summary": {
                    **summarize(reports, grouped),
                    "photos_found": total,
                    "stacked_frames_skipped": skipped_stacked,
                    "previews_missing": sum(1 for jpeg in previews if not jpeg),
                },
                "groups": grouped,
                "photos_with_issues": [r.as_dict() for r in reports if r.issues],
                "notes": [
                    "focus is relative: compare photos of the same shoot",
                    "tilt_deg > 0 means turned counterclockwise; edit_develop CropAngle "
                    "with the opposite value levels it",
                ],
            }

        return await asyncio.to_thread(compute)

    @server.tool(annotations=CHANGES)
    async def straighten_photos(
        ctx: Context,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        filters: Annotated[
            PhotoFilters | None, Field(description="Search the catalog instead of using ids")
        ] = None,
        guides: Annotated[
            Literal["verticals_and_frontal", "verticals"],
            Field(
                description="verticals_and_frontal: vertical lines, plus the horizontal "
                "edges of things facing the camera (prints, frames), like a hand-drawn "
                "Guided Upright with four lines. verticals: vertical lines only."
            ),
        ] = "verticals_and_frontal",
        chosen_lines: Annotated[
            dict[str, list[int]] | None,
            Field(
                description="Guides you chose yourself after looking at straighten_lines: "
                "photo id -> 2 to 4 line numbers. Only for photos Safelight straightened "
                "before. Replaces photo_ids, scope and filters."
            ),
        ] = None,
        label: Annotated[
            Literal["red", "yellow", "green", "blue", "purple", "none"] | None,
            Field(
                description="Color label for the photos this changes, so the user can find "
                "and check them (none: no label). Defaults to the user's choice in "
                "Safelight's settings. A photo that already has another label keeps it."
            ),
        ] = None,
        replace_existing_upright: Annotated[
            bool,
            Field(
                description="Also redo photos that already have an Upright correction, by "
                "hand or by an earlier run. Only when the user asks for exactly that."
            ),
        ] = False,
        max_photos: Annotated[int, Field(ge=1, le=500)] = 200,
        check_only: Annotated[
            bool,
            Field(
                description="Change nothing: only say which photos would be straightened "
                "and which would be skipped, and why. Use it to tell the user the right "
                "count before asking them."
            ),
        ] = False,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Straighten verticals (and frontal horizontals) of architecture and exhibition
        photos with Lightroom's Guided Upright, each photo with its own correction.

        For each photo: a snapshot "Before Safelight straighten" keeps the current settings
        (unless the user turned snapshots off), earlier transform corrections and the crop
        are reset, Safelight finds the straight lines on a fresh preview, and writes up to
        four Upright guides with the matching correction. The correction is a rotation of
        the camera, so prints and walls keep their proportions; with two vertical and two
        horizontal guides it is made exact, like Lightroom's own. Corners left empty by the
        correction are not cropped. Rotated photos are skipped, and so are photos that
        already have an Upright correction: it may be the user's own work. A photo that
        cannot be straightened gets its earlier transform and crop back.

        The lines found automatically are sometimes wrong (shadows, lines inside prints,
        ceiling rails). To choose the guides yourself, look at the numbered lines with
        straighten_lines, then call this tool again with chosen_lines. When your verticals
        disagree with the automatic ones, the photo is not changed and is listed in
        left_to_user: tell the user, who can draw the guides in Lightroom (Transform tool).
        Check the result with contact_sheet, and look closer at photos with warnings.
        Which photos would be skipped is only known by reading their settings: call it
        first with check_only to tell the user how many photos it will change.
        """
        store_home = hub_client.home
        if chosen_lines is not None:
            if not chosen_lines:
                raise ToolError("chosen_lines is empty.")
            photos, found = await list_photos(
                runner, ctx, photo_ids=list(chosen_lines), limit=max_photos
            )
        else:
            photos, found = await list_photos(
                runner, ctx, photo_ids=photo_ids, scope=scope, filters=filters, limit=max_photos
            )
        listed = len(photos)
        photos, skipped_stacked = top_of_stacks(photos)
        if not photos:
            raise ToolError("No photos to straighten.")
        status = await hub_client.status()
        preferences = Settings()
        preferences.update(status.get("settings") or {}, strict=False)
        if label is None:
            label = preferences.change_label  # type: ignore[assignment]
        if label == "none":
            label = None
        if (status.get("settings") or {}).get("confirm_threshold") == 0:
            # Each photo gets its own correction, so each would need its own approval.
            raise ToolError("straighten_photos needs a confirmation threshold of at least 1.")
        rotated: list[dict[str, Any]] = []
        already: list[dict[str, Any]] = []
        left_to_user: list[dict[str, Any]] = []
        not_done: list[dict[str, Any]] = []
        upright_photos = []
        before: dict[str, dict[str, Any]] = {}  # geometry to put back if it goes wrong
        lens: dict[str, dict[str, Any]] = {}  # settings the lines found depend on
        chosen_plans: dict[str, Plan] = {}
        validated: list[dict[str, Any]] = []  # the user approved their edits: never touched
        for photo in photos:
            entry = {"id": photo["uuid"], "file": photo.get("file_name")}
            if photo.get("validated"):
                validated.append(entry)
                continue
            current = await runner.read(ctx, "develop.get", {"uuids": [photo["uuid"]]})
            settings = current.get("settings") or {}
            if chosen_lines is not None:
                refusal = _choose(photo["uuid"], chosen_lines[photo["uuid"]], settings)
                if isinstance(refusal, Plan):
                    chosen_plans[photo["uuid"]] = refusal
                    upright_photos.append(photo)
                    before[photo["uuid"]] = geometry_settings(settings)
                elif refusal.get("left_to_user"):
                    left_to_user.append({**entry, **refusal["left_to_user"]})
                elif refusal.get("changed"):
                    already.append({**entry, "reason": refusal["changed"]})
                else:
                    not_done.append({**entry, "reason": refusal["reason"], "put_back": False})
            elif settings.get("PerspectiveUpright") and not replace_existing_upright:
                already.append(entry)
            elif settings.get("orientation") in (None, "", "AB"):
                upright_photos.append(photo)
                before[photo["uuid"]] = geometry_settings(settings)
                lens[photo["uuid"]] = settings
            else:
                rotated.append(entry)

        async def note_review(done: list[dict[str, Any]]) -> None:
            """Puts the photos that need the user's eye in the app's list of photos to
            check (it stays there until the user marks them done)."""
            items = [
                {"id": e["id"], "file": e.get("file"), "reason": "; ".join(e["warnings"])}
                for e in done
                if e.get("warnings")
            ]
            items += [
                {
                    "id": e["id"],
                    "file": e.get("file"),
                    "reason": "Safelight did not change it: the lines chosen disagree with the "
                    "automatic ones. Draw the guides in Lightroom (Transform, Guided).",
                }
                for e in left_to_user
            ]
            items += [
                {
                    "id": e["id"],
                    "file": e.get("file"),
                    "reason": f"Not straightened, and its earlier correction could not be put "
                    f"back: {e['reason']}",
                }
                for e in not_done
                if e["id"] in before and e.get("put_back") is False  # lost its geometry
            ]
            if items:
                with contextlib.suppress(HubUnavailable):
                    await hub_client.review(add=items)

        def report(done: list[dict[str, Any]]) -> ActionResult:
            to_review = [entry["id"] for entry in done if entry.get("warnings")]
            notes = [
                f"each photo has a snapshot '{STRAIGHTEN_SNAPSHOT}' with its settings from before"
                if preferences.snapshots
                else "no snapshot (the user turned them off): Lightroom's History has the "
                "settings from before",
                "the crop was reset and corners left empty are not cropped",
                "pitch > 0: the camera was pointing down; roll > 0: turned clockwise",
            ]
            if chosen_lines is None and done:
                notes.append(
                    "to choose the guides yourself: straighten_lines shows the numbered "
                    "lines, then call straighten_photos with chosen_lines"
                )
            return ActionResult(
                state="succeeded",
                result={
                    "summary": {
                        "straightened": len(done),
                        "not_straightened": len(not_done),
                        "to_review": len(to_review),
                        "left_to_user": len(left_to_user),
                        "rotated_skipped": len(rotated),
                        "already_corrected_skipped": len(already),
                        "validated_skipped": len(validated),
                        "stacked_frames_skipped": skipped_stacked,
                    },
                    "straightened": done,
                    "not_straightened": not_done,
                    "left_to_user": left_to_user,
                    "rotated_skipped": rotated,
                    "already_corrected_skipped": already,
                    "validated_skipped": validated,
                    "notes": notes,
                },
            )

        if check_only:
            notes = [
                "nothing was changed (check_only)",
                "a few photos may still not be straightened: no straight line found, or "
                "Lightroom refusing; the real run says which",
            ]
            if found > listed:
                notes.append(
                    f"only the first {listed} of {found} photos were looked at (max_photos): "
                    "the real run with the same max_photos stops there too"
                )
            if (status.get("settings") or {}).get("read_only"):
                notes.append(
                    "Safelight is read-only: nothing can be straightened until the user turns "
                    "read-only off"
                )
            return ActionResult(
                state="succeeded",
                result={
                    "summary": {
                        "to_straighten": len(upright_photos),
                        "left_to_user": len(left_to_user),
                        "not_possible": len(not_done),
                        "rotated_skipped": len(rotated),
                        "already_corrected_skipped": len(already),
                        "validated_skipped": len(validated),
                        "stacked_frames_skipped": skipped_stacked,
                        "photos_found": found,
                    },
                    "to_straighten": [
                        {"id": p["uuid"], "file": p.get("file_name")} for p in upright_photos
                    ],
                    "left_to_user": left_to_user,
                    "not_possible": [
                        {k: v for k, v in e.items() if k != "put_back"} for e in not_done
                    ],
                    "rotated_skipped": rotated,
                    "already_corrected_skipped": already,
                    "validated_skipped": validated,
                    "notes": notes,
                },
            )
        if not upright_photos:
            if chosen_lines is not None:
                await note_review([])
                return report([])
            raise ToolError(
                f"Nothing to straighten: {len(already)} photos already have an Upright "
                f"correction, {len(validated)} were validated by the user (never changed) and "
                f"{len(rotated)} are rotated, which is not supported yet."
            )
        history = "Safelight: Straighten" + (" (chosen lines)" if chosen_lines else "")

        async def write(photo: dict[str, Any], fields: dict[str, Any]) -> bool:
            reply = await runner.settle(
                await hub_client.run(
                    "metadata.set",
                    {"uuids": [photo["uuid"]], "fields": fields},
                    wait_s=30,
                    client=_client_name(ctx),
                )
            )
            failed = (reply.get("result") or {}).get("failures")
            return reply.get("state") == JobState.SUCCEEDED and not failed

        async def mark(photo: dict[str, Any]) -> dict[str, str]:
            """Gives a straightened photo the user's color and records in the catalog that
            Safelight straightened it; says what its label is now, and what went wrong."""
            out: dict[str, str] = {}
            if label:
                if photo.get("label") not in (None, label):
                    out["label"] = f"kept {photo['label']}"
                else:
                    out["label"] = str(label) if await write(photo, {"label": label}) else "failed"
            if not await write(photo, {"geometry_by": "safelight"}):
                out["geometry_field"] = "failed"
            return out

        async def apply(photo_id: str, settings: dict[str, Any], name: str) -> str | None:
            """Write settings on one photo; returns what went wrong, or None."""
            reply = await runner.settle(
                await hub_client.run(
                    "develop.apply",
                    {"uuids": [photo_id], "settings": settings, "history_name": name},
                    wait_s=30,
                    client=_client_name(ctx),
                )
            )
            if reply.get("state") != JobState.SUCCEEDED:
                return str((reply.get("error") or {}).get("message") or reply.get("state"))
            failures = (reply.get("result") or {}).get("failures") or []
            return str(failures[0].get("error")) if failures else None

        async def put_back(photo_id: str) -> bool:
            settings = before[photo_id]
            return await apply(photo_id, settings, f"{history} (put back)") is None

        def keep(photo_id: str, plan: Plan, jpeg: bytes | None) -> None:
            """Remember the lines and the correction, for straighten_lines and chosen_lines."""
            if not plan.lines:
                return
            with contextlib.suppress(OSError, ValueError, KeyError):
                straighten_store.save(photo_id, plan, lens.get(photo_id, {}), jpeg, store_home)

        ids = [photo["uuid"] for photo in upright_photos]
        # From the reset on, photos have lost their geometry: whatever happens (no line
        # found, an error, the AI client cancelling), those not straightened get it back.
        pending = set(ids)
        if (status.get("settings") or {}).get("read_only"):
            pending.clear()  # the hub refuses the reset: nothing to put back
        try:
            reset = await runner.change(
                ctx,
                "develop.apply",
                {
                    "uuids": ids,
                    "settings": RESET_SETTINGS,
                    "history_name": history,
                    **({"snapshot": STRAIGHTEN_SNAPSHOT} if preferences.snapshots else {}),
                },
                confirmation_token,
            )
            if reset.state in ("confirmation_required", "declined"):
                pending.clear()
                return reset
            if reset.state == "running" and reset.job_id:
                reply = await runner.settle({"state": "running", "job_id": reset.job_id})
                reset = runner.outcome(reply)
            if reset.state != "succeeded":
                return reset
            reset_failures = {
                failure.get("uuid"): failure.get("error")
                for failure in (reset.result or {}).get("failures") or []
            }

            async def progress(done: int, count: int) -> None:
                await ctx.report_progress(done, 2 * count, f"Rendered {done} of {count} previews")

            previews: list[bytes | None]
            if chosen_lines is None:
                previews = await fetch_previews(hub_client, ctx, upright_photos, 2048, progress)
                frontal = guides == "verticals_and_frontal"

                def compute() -> list[Plan | None]:
                    return [
                        plan_from_jpeg(jpeg, photo, frontal_horizontals=frontal) if jpeg else None
                        for photo, jpeg in zip(upright_photos, previews, strict=True)
                    ]

                plans = await asyncio.to_thread(compute)
            else:
                previews = [None] * len(upright_photos)
                plans = [chosen_plans[photo_id] for photo_id in ids]
            done: list[dict[str, Any]] = []
            for index, (photo, jpeg, plan) in enumerate(
                zip(upright_photos, previews, plans, strict=True)
            ):
                await ctx.report_progress(
                    len(upright_photos) + index, 2 * len(upright_photos), "Straightening"
                )
                photo_id = photo["uuid"]
                entry = {"id": photo_id, "file": photo.get("file_name")}
                if photo_id in reset_failures:
                    reason = f"Lightroom refused it: {reset_failures[photo_id]}"
                elif plan is None:
                    reason = "no preview" if not jpeg else "no usable straight line found"
                else:
                    error = await apply(photo_id, plan.settings(), history)
                    if error is None:
                        pending.discard(photo_id)
                        await asyncio.to_thread(keep, photo_id, plan, jpeg)
                        chosen = (
                            {"lines": [i + 1 for i in plan.guide_lines]} if chosen_lines else {}
                        )
                        labelled = await mark(photo)
                        done.append({**entry, **plan.as_dict(), **chosen, **labelled})
                        continue
                    reason = f"Lightroom refused it: {error}"
                restored = await put_back(photo_id)
                pending.discard(photo_id)
                not_done.append({**entry, "reason": reason, "put_back": restored})
        finally:
            if pending:
                with anyio.CancelScope(shield=True):
                    failed_in_a_row = 0
                    for photo_id in [i for i in ids if i in pending]:
                        failed_in_a_row = 0 if await put_back(photo_id) else failed_in_a_row + 1
                        if failed_in_a_row == 2:
                            break  # Lightroom is gone or busy: the snapshots remain
        await note_review(done)
        return report(done)

    def _choose(photo_id: str, numbers: list[int], settings: dict[str, Any]) -> Plan | dict:
        """The plan for the lines chosen on one photo, or why it is not made."""
        saved = straighten_store.load(photo_id, hub_client.home)
        if saved is None:
            return {"reason": "Safelight kept no lines for this photo: straighten it first"}
        if not saved.is_current(settings):
            return {"changed": "its correction changed since Safelight wrote it"}
        if saved.lens_changed(settings):
            return {
                "reason": "its lens corrections or orientation changed since the lines were "
                "found: straighten it again (replace_existing_upright)"
            }
        try:
            plan = plan_from_lines(
                saved.lines, [n - 1 for n in numbers], saved.width, saved.height, saved.focal_px
            )
        except ValueError as exc:
            return {"reason": f"lines {numbers}: {exc}"}
        verticals = [plan.lines[i].segment for i in plan.guide_lines if plan.lines[i].vertical]
        auto = np.array(saved.auto_matrix).reshape(3, 3)
        gap = disagreement_deg(plan.matrix, verticals, auto, saved.auto_verticals)
        if gap > DISAGREE_DEG:
            return {
                "left_to_user": {
                    "chosen": numbers,
                    "automatic": [i + 1 for i in saved.auto_guide_lines],
                    "disagreement_deg": round(gap, 2),
                    "reason": "the chosen verticals and the automatic ones disagree: only the "
                    "user can tell which are right",
                }
            }
        return plan

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def straighten_lines(
        ctx: Context,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        max_photos: Annotated[int, Field(ge=1, le=8)] = 4,
    ) -> list[Any]:
        """See the straight lines Safelight found on photos it straightened, numbered, to
        choose their guides yourself.

        Each photo is shown as it was analysed, without any correction: verticals in blue,
        horizontals in orange, the guides in use thicker in magenta. Choose by eye: the
        long edges of things flat on the wall (frames, panels, prints) and of walls, pillars
        and doors. Never shadows, lines inside a printed photo, ceiling rails or lights that
        are not on the wall, cables, or short pieces. Take two verticals far apart, one on
        each side, and when the view faces the wall, two horizontals: the top and bottom
        edges of works hung on it. Then call straighten_photos with chosen_lines. For a
        view along a wall (receding), or when unsure of the verticals, leave the photo to
        the user.
        """
        photos, _ = await list_photos(
            runner, ctx, photo_ids=photo_ids, scope=scope, limit=max_photos
        )
        out: list[Any] = []
        missing = []
        for photo in photos:
            saved = await asyncio.to_thread(straighten_store.load, photo["uuid"], hub_client.home)
            if saved is None:
                missing.append(photo.get("file_name") or photo["uuid"])
                continue
            image = await asyncio.to_thread(straighten_store.draw, saved)
            rows = [
                f"{line['number']}. {line['kind']}, {line['length_pct']}% long, at "
                f"{'x' if line['kind'] == 'vertical' else 'y'}={line['at_pct']}%, "
                f"{line['angle_deg']} deg" + (" [guide]" if line.get("guide") else "")
                for line in straighten_store.describe(saved)
            ]
            out.append(Image(data=image, format="jpeg"))
            out.append(f"{photo['uuid']}  {photo.get('file_name', '')}\n" + "\n".join(rows))
        if not out:
            raise ToolError(
                "Safelight kept no lines for these photos: straighten them with "
                "straighten_photos first."
            )
        if missing:
            out.append("No lines kept for: " + ", ".join(missing))
        return out

    # -- develop --------------------------------------------------------------------------

    @server.tool(annotations=READ_ONLY)
    async def get_develop_settings(
        ctx: Context,
        photo_id: Annotated[str | None, Field(description="Defaults to the active photo")] = None,
        keys: Annotated[
            list[str] | None,
            Field(description="Only these settings, e.g. Exposure, Temperature. Default: all"),
        ] = None,
    ) -> dict[str, Any]:
        """A photo's develop settings, its masks (with their adjustments) and snapshots."""
        target = _single(photo_id)
        params = {**target, **({"keys": keys} if keys else {})}
        settings, masks, snapshots = await asyncio.gather(
            runner.read(ctx, "develop.get", params),
            runner.read(ctx, "masks.list", target),
            runner.read(ctx, "snapshots.list", target),
        )
        return {
            **settings,
            "masks": masks.get("masks", []),
            "snapshots": snapshots.get("snapshots", []),
        }

    @server.tool(annotations=CHANGES)
    async def edit_develop(
        ctx: Context,
        settings: Annotated[
            dict[str, float | int | str | bool],
            Field(
                description="Develop settings as named in Lightroom: Exposure (EV), Contrast, "
                "Highlights, Shadows, Whites, Blacks, Texture, Clarity, Dehaze, Vibrance, "
                "Saturation, Temperature (K on raw), Tint, HueAdjustmentRed..., "
                "SaturationAdjustmentBlue..., LuminanceAdjustmentGreen..., "
                "ColorGradeShadowHue/Sat..., Sharpness, LuminanceSmoothing, "
                "PostCropVignetteAmount, GrainAmount, WhiteBalance ('As Shot', 'Auto'), ..."
            ),
        ],
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        relative: Annotated[
            bool, Field(description="Add the numbers to the current values instead")
        ] = False,
        history_name: Annotated[str | None, Field(description="Name in the History panel")] = None,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Change develop settings on one or many photos. Values outside a slider's range
        are limited to it, and the answer says so in `notes`."""
        params: dict[str, Any] = {
            **_targets(photo_ids, scope),
            "settings": settings,
            "relative": relative,
        }
        if history_name:
            params["history_name"] = history_name
        return await runner.change(ctx, "develop.apply", params, confirmation_token)

    @server.tool(annotations=CHANGES_ONCE)
    async def copy_develop_settings(
        ctx: Context,
        from_photo_id: str,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        keys: Annotated[
            list[str] | None,
            Field(description="Only these settings. Default: all but crop, masks and red eye"),
        ] = None,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Copy develop settings from one photo to others (like Sync Settings)."""
        params: dict[str, Any] = {**_targets(photo_ids, scope), "from": from_photo_id}
        if keys:
            params["keys"] = keys
        return await runner.change(ctx, "develop.copy", params, confirmation_token)

    @server.tool(annotations=DESTRUCTIVE)
    async def auto_develop(
        ctx: Context,
        action: Annotated[
            Literal["auto_tone", "auto_white_balance", "reset"],
            Field(description="reset clears all develop settings (it stays in History)"),
        ],
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Lightroom's Auto Tone or Auto White Balance, or reset all develop settings.

        Auto Tone and reset open each photo in the Develop module, one at a time.
        """
        command = {
            "auto_tone": "develop.auto_tone",
            "auto_white_balance": "develop.auto_white_balance",
            "reset": "develop.reset",
        }[action]
        return await runner.change(ctx, command, _targets(photo_ids, scope), confirmation_token)

    @server.tool(annotations=CHANGES)
    async def apply_preset(
        ctx: Context,
        preset_id: Annotated[str, Field(description="Preset uuid from list_catalog_items")],
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        amount: Annotated[
            int | None, Field(ge=0, le=200, description="Percent, 100 = full")
        ] = None,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Apply one of the user's develop presets."""
        params: dict[str, Any] = {**_targets(photo_ids, scope), "preset_uuid": preset_id}
        if amount is not None:
            params["amount"] = amount
        return await runner.change(ctx, "develop.apply_preset", params, confirmation_token)

    @server.tool(annotations=DESTRUCTIVE)
    async def snapshot(
        ctx: Context,
        action: Literal["create", "apply", "delete"],
        name: Annotated[str, Field(description="Snapshot name")],
        photo_ids: PhotoIds = None,
        scope: Scope = "active",
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Save the current develop settings as a named snapshot, go back to one, or delete
        one. Create a snapshot before a big edit so the user can return to it. apply and
        delete work on one photo at a time."""
        if action != "create" and (
            len(photo_ids or []) > 1 or (not photo_ids and scope != "active")
        ):
            raise ToolError(f"{action} works on one photo at a time: give one photo id.")
        command = {
            "create": "snapshots.create",
            "apply": "snapshots.apply",
            "delete": "snapshots.delete",
        }[action]
        params = {**_targets(photo_ids, scope), "name": name}
        if action == "create":
            params["update_existing"] = True
        return await runner.change(ctx, command, params, confirmation_token)

    # -- masks ----------------------------------------------------------------------------

    @server.tool(annotations=CHANGES)
    async def create_mask(
        ctx: Context,
        kind: Literal["subject", "sky", "background", "people"],
        photo_id: Annotated[str | None, Field(description="Defaults to the active photo")] = None,
        adjustments: MaskAdjustments | None = None,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Let Lightroom's AI select the subject, sky, background or people, and optionally
        adjust that area right away. Opens the photo in the Develop module."""
        target = _single(photo_id)
        created = await runner.change(
            ctx, "masks.create", {**target, "kind": kind}, confirmation_token
        )
        if created.state != "succeeded" or not adjustments:
            return created
        if (created.result or {}).get("validated"):
            created.message = "The user validated this photo: Safelight does not change it."
            return created
        mask_id = (created.result or {}).get("mask_id")
        if not mask_id:
            created.message = "The mask was created but Lightroom did not report its id."
            return created
        adjusted = await runner.change(
            ctx, "masks.adjust", {**target, "mask_id": mask_id, "adjustments": adjustments}
        )
        adjusted.result = {"mask_id": mask_id, **(adjusted.result or {})}
        return adjusted

    @server.tool(annotations=CHANGES)
    async def edit_mask(
        ctx: Context,
        mask: Annotated[str, Field(description="Mask id or name, from get_develop_settings")],
        adjustments: MaskAdjustments,
        photo_id: Annotated[str | None, Field(description="Defaults to the active photo")] = None,
        relative: bool = False,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Change the local adjustments of an existing mask (works without opening Develop)."""
        params = {
            **_single(photo_id),
            "mask_id": mask,
            "adjustments": adjustments,
            "relative": relative,
        }
        return await runner.change(ctx, "masks.adjust", params, confirmation_token)

    @server.tool(annotations=DESTRUCTIVE)
    async def change_mask(
        ctx: Context,
        action: Literal["invert", "delete"],
        mask: Annotated[str, Field(description="Mask id from get_develop_settings")],
        photo_id: Annotated[str | None, Field(description="Defaults to the active photo")] = None,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Invert or delete a mask. Opens the photo in the Develop module."""
        command = "masks.invert" if action == "invert" else "masks.delete"
        return await runner.change(
            ctx, command, {**_single(photo_id), "mask_id": mask}, confirmation_token
        )

    # -- metadata, keywords, organization ---------------------------------------------------

    @server.tool(annotations=DESTRUCTIVE_ONCE)
    async def set_metadata(
        ctx: Context,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        rating: Annotated[int | None, Field(ge=0, le=5)] = None,
        pick: Literal["picked", "unflagged", "rejected"] | None = None,
        label: Literal["red", "yellow", "green", "blue", "purple", "none"] | None = None,
        title: str | None = None,
        caption: str | None = None,
        headline: str | None = None,
        alt_text: Annotated[str | None, Field(description="Accessibility description")] = None,
        copyright: str | None = None,
        creator: str | None = None,
        location: str | None = None,
        city: str | None = None,
        state: str | None = None,
        country: str | None = None,
        event: str | None = None,
        gps: Annotated[
            dict[str, float] | None,
            Field(description='{"latitude": 48.85, "longitude": 2.35}'),
        ] = None,
        remove_gps: bool = False,
        validated: Annotated[
            bool | None,
            Field(
                description="Safelight's Validated field: true when the user approves the "
                "photo's edits, so Safelight never changes them. Only when the user says so."
            ),
        ] = None,
        reject_reason: Annotated[
            str | None,
            Field(
                description="Why the photo is rejected (duplicate, passer-by, flare...), kept "
                'in the catalog; "" clears it'
            ),
        ] = None,
        geometry_by: Annotated[
            Literal["safelight", "user", "none"] | None,
            Field(description="Who last corrected the perspective (Safelight's field)"),
        ] = None,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Set ratings, flags, color labels, titles, captions, IPTC fields or GPS location,
        and Safelight's own fields: validated, reject reason, who corrected the geometry."""
        fields: dict[str, Any] = {
            key: value
            for key, value in {
                "rating": rating,
                "label": label,
                "title": title,
                "caption": caption,
                "headline": headline,
                "alt_text": alt_text,
                "copyright": copyright,
                "creator": creator,
                "location": location,
                "city": city,
                "state": state,
                "country": country,
                "event": event,
                "gps": gps,
                "validated": validated,
                "reject_reason": reject_reason,
                "geometry_by": geometry_by,
            }.items()
            if value is not None
        }
        if pick is not None:
            fields["pick"] = {"picked": 1, "unflagged": 0, "rejected": -1}[pick]
        if remove_gps:
            fields["gps"] = False
        if not fields:
            raise ToolError("Give at least one field to set.")
        params = {**_targets(photo_ids, scope), "fields": fields}
        return await runner.change(ctx, "metadata.set", params, confirmation_token)

    @server.tool(annotations=DESTRUCTIVE_ONCE)
    async def edit_keywords(
        ctx: Context,
        add: Annotated[
            list[str] | None,
            Field(description='Keywords to add; use "Parent > Child" for hierarchies'),
        ] = None,
        remove: Annotated[
            list[str] | None,
            Field(description="Keywords to remove (a name matches it anywhere in the tree)"),
        ] = None,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Add keywords (created if needed) or remove keywords from photos."""
        if not add and not remove:
            raise ToolError("Give keywords to add or to remove.")
        targets = _targets(photo_ids, scope)
        results: dict[str, Any] = {}
        outcome = None
        if add:
            outcome = await runner.change(
                ctx, "keywords.add", {**targets, "keywords": add}, confirmation_token
            )
            if outcome.state != "succeeded" or not remove:
                if outcome.state == "running":
                    outcome.message = (
                        f"{outcome.message} Nothing was removed yet: once it is done, call "
                        "edit_keywords again with only `remove`."
                    )
                return outcome
            results["added"] = outcome.result
        if remove:
            outcome = await runner.change(
                ctx,
                "keywords.remove",
                {**targets, "keywords": remove},
                None if add else confirmation_token,
            )
            if add and outcome.state == "succeeded":
                outcome.result = {**results, "removed": outcome.result}
            elif add and outcome.state == "confirmation_required":
                # The token is for the removal alone, so it cannot go with `add` again.
                outcome.result = results
                outcome.message = (
                    "The keywords were added. Removing needs its own approval: show the summary "
                    "to the user. Only if they approve, call edit_keywords again without `add`, "
                    "with the same `remove` and photos, and confirmation_token."
                )
        return outcome  # type: ignore[return-value]

    @server.tool(annotations=READ_ONLY)
    async def list_catalog_items(
        ctx: Context,
        kind: Literal["collections", "keywords", "presets"],
        query: Annotated[
            str | None, Field(description="Only items whose name contains this")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> dict[str, Any]:
        """List collections (with ids), keywords (as paths) or develop presets (with ids)."""
        if kind == "keywords":
            params: dict[str, Any] = {"limit": limit}
            if query:
                params["query"] = query
            return await runner.read(ctx, "keywords.list", params)
        command = "collections.list" if kind == "collections" else "presets.list"
        result = await runner.read(ctx, command, {})
        items = result.get("collections" if kind == "collections" else "presets") or []
        if query:
            needle = query.lower()
            items = [
                item
                for item in items
                if needle in str(item.get("name", "")).lower()
                or needle in str(item.get("folder", "")).lower()
            ]
        return {"total": len(items), "items": items[:limit]}

    @server.tool(annotations=DESTRUCTIVE)
    async def manage_collection(
        ctx: Context,
        action: Literal["create", "add_photos", "remove_photos", "delete", "list_photos"],
        collection_id: Annotated[int | None, Field(description="From list_catalog_items")] = None,
        name: Annotated[str | None, Field(description="For create")] = None,
        kind: Literal["collection", "collection_set", "smart_collection"] = "collection",
        parent_id: Annotated[int | None, Field(description="Collection set to create in")] = None,
        smart_filters: Annotated[
            PhotoFilters | None, Field(description="Rules of a smart collection")
        ] = None,
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Create collections (also smart collections and sets), add or remove photos, list a
        collection's photos, or delete a collection (its photos stay in the catalog)."""
        if action == "create":
            if not name:
                raise ToolError("name is required to create a collection.")
            params: dict[str, Any] = {"name": name, "kind": kind}
            if parent_id is not None:
                params["parent_id"] = parent_id
            if kind == "smart_collection":
                search = build_search(smart_filters) if smart_filters else None
                if search is None:
                    raise ToolError("A smart collection needs smart_filters.")
                params["search"] = search
            return await runner.change(ctx, "collections.create", params, confirmation_token)
        if collection_id is None:
            raise ToolError("collection_id is required for this action.")
        if action == "list_photos":
            result = await runner.read(
                ctx, "collections.photos", {"collection_id": collection_id, "limit": 200}
            )
            return ActionResult(state="succeeded", result=result)
        if action == "delete":
            return await runner.change(
                ctx, "collections.delete", {"collection_id": collection_id}, confirmation_token
            )
        command = "collections.add" if action == "add_photos" else "collections.remove_photos"
        params = {**_targets(photo_ids, scope), "collection_id": collection_id}
        return await runner.change(ctx, command, params, confirmation_token)

    @server.tool(annotations=CHANGES)
    async def organize_photos(
        ctx: Context,
        action: Literal["rotate_left", "rotate_right", "create_virtual_copies"],
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        copy_name: Annotated[str | None, Field(description="Name of the virtual copies")] = None,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Rotate photos, or create one virtual copy of each (to try another edit)."""
        targets = _targets(photo_ids, scope)
        if action == "create_virtual_copies":
            params = {**targets, **({"name": copy_name} if copy_name else {})}
            return await runner.change(
                ctx, "photos.create_virtual_copies", params, confirmation_token
            )
        direction = "left" if action == "rotate_left" else "right"
        return await runner.change(
            ctx, "photos.rotate", {**targets, "direction": direction}, confirmation_token
        )

    @server.tool(annotations=DESTRUCTIVE)
    async def export_photos(
        ctx: Context,
        destination: Annotated[str, Field(description="Absolute folder path")],
        photo_ids: PhotoIds = None,
        scope: Scope = "selected",
        format: Literal["JPEG", "TIFF", "PNG", "DNG", "ORIGINAL"] = "JPEG",
        quality: Annotated[int, Field(ge=1, le=100)] = 85,
        long_edge_px: Annotated[
            int | None, Field(ge=64, description="Resize; default full size")
        ] = None,
        color_space: Literal["sRGB", "AdobeRGB", "DisplayP3", "ProPhotoRGB"] = "sRGB",
        if_exists: Literal["rename", "overwrite", "skip"] = "rename",
        sharpen: Literal["screen", "matte", "glossy"] | None = None,
        strip_metadata: bool = False,
        remove_location: bool = False,
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Export photos with their edits to files. Returns the path of each file. Existing
        files are kept unless if_exists is overwrite, which always needs the user's
        approval (files replaced on disk cannot be got back)."""
        params: dict[str, Any] = {
            **_targets(photo_ids, scope),
            "destination": destination,
            "format": format,
            "quality": quality,
            "color_space": color_space,
            "if_exists": if_exists,
            "strip_metadata": strip_metadata,
            "remove_location": remove_location,
        }
        if long_edge_px:
            params["long_edge_px"] = long_edge_px
        if sharpen:
            params["sharpen"] = sharpen
        return await runner.change(ctx, "export.photos", params, confirmation_token)

    return server
