"""MCP server exposed to AI clients (Claude Desktop, Claude Code, Cursor, Codex...).

The server always starts, whether or not Lightroom Classic is open. It forwards work to
the Safelight hub, starting the hub if needed. Read-only tools are marked as such, so
clients can run them without asking; tools that change the catalog go through the hub's
policy (read-only mode, dry run, confirmation of large or destructive changes).
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .analysis import findings, groups, measure, summarize, top_of_stacks
from .contact_sheet import SheetPhoto, render
from .hub_client import HubClient, HubUnavailable
from .protocol import JobState
from .search import PhotoFilters, build_search, has_post_filters, matches_post_filters

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

Safelight is not affiliated with Adobe.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
CHANGES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

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
        if reply.get("state") == JobState.CONFIRMATION_REQUIRED and _supports_elicitation(ctx):
            reply = await self._ask_user(ctx, command, params, reply, name)
            if isinstance(reply, ActionResult):
                return reply
        return self._outcome(reply)

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

    def _outcome(self, reply: dict[str, Any]) -> ActionResult:
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
    if filters is None:
        listing = await runner.read(
            ctx, "photos.get", {**_targets(photo_ids, scope), "limit": limit, "offset": offset}
        )
        return listing.get("photos") or [], int(listing.get("total") or 0)
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

    # -- status and jobs ------------------------------------------------------------------

    @server.tool(annotations=READ_ONLY)
    async def safelight_status(ctx: Context) -> StatusResult:
        """Whether Lightroom Classic is connected, plus the active photo and selection size.

        Never fails. Call it first, and again whenever another tool says Lightroom is
        unavailable.
        """
        return await collect_status(hub_client, _client_name(ctx))

    @server.tool(annotations=READ_ONLY)
    async def safelight_job(
        job_id: str,
        wait_s: Annotated[float, Field(ge=0, le=60, description="Seconds to wait")] = 0,
    ) -> JobResult:
        """State, progress and result of a job that another tool reported as running."""
        return JobResult.model_validate(await hub_client.job(job_id, wait_s))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
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
        return await runner.read(ctx, "photos.get", params)

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
        known photo). Focal length, aperture and shutter speed are checked on the results,
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
        Closed eyes are not detected; check faces on a contact sheet.
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

    @server.tool(annotations=CHANGES)
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

    @server.tool(annotations=CHANGES)
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
        one. Create a snapshot before a big edit so the user can return to it."""
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

    @server.tool(annotations=CHANGES)
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
        confirmation_token: ConfirmationToken = None,
    ) -> ActionResult:
        """Set ratings, flags, color labels, titles, captions, IPTC fields or GPS location."""
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

    @server.tool(annotations=CHANGES)
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

    @server.tool(annotations=CHANGES)
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
        files are kept unless if_exists is overwrite."""
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
