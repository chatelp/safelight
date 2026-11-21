"""The Safelight hub: a local daemon between AI clients and the Lightroom plugin.

AI clients (through `safelight mcp` relays) submit commands with POST /v1/rpc. Each
command becomes a job with an id, a state, optional progress and a result. The
Lightroom plugin long-polls POST /v1/plugin/poll to receive jobs, reports progress to
POST /v1/plugin/progress and posts each answer to POST /v1/plugin/result.

Before running a command that changes the catalog, the hub applies the user's policy:
read-only mode, a dry run to count the affected photos, and a confirmation step for
bulk or destructive changes. Everything listens on 127.0.0.1 only and requires the
per-install token stored in hub.json.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import __version__
from .paths import hub_config_path
from .policy import CommandPolicy, policy_for
from .protocol import (
    ACTIVITY_LOG_SIZE,
    CLIENT_HEADER,
    CLIENT_SEEN_WINDOW_S,
    CONFIRMATION_TTL_S,
    CONNECTED_GRACE_S,
    DEFAULT_WAIT_S,
    DELIVERY_TIMEOUT_S,
    JOB_RETENTION_S,
    MAX_POLL_WAIT_S,
    MAX_RETAINED_JOBS,
    MAX_WAIT_S,
    PROTOCOL_VERSION,
    TOKEN_HEADER,
    ErrorCode,
    JobState,
)
from .settings import Settings

LOGGER = logging.getLogger("safelight.hub")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


def _failed(code: str, message: str) -> dict[str, Any]:
    """Response for a request refused before any job was created."""
    return {"job_id": None, "state": JobState.FAILED, "error": _error(code, message)}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _params_digest(command: str, params: dict[str, Any]) -> str:
    canonical = json.dumps([command, params], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class Job:
    id: str
    command: str
    params: dict[str, Any]
    client: str
    internal: bool = False  # dry runs started by the hub itself
    state: str = JobState.QUEUED
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    progress: dict[str, Any] | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def finished(self) -> bool:
        return self.state in JobState.TERMINAL

    def response(self) -> dict[str, Any]:
        out: dict[str, Any] = {"job_id": self.id, "command": self.command, "state": self.state}
        if self.progress is not None:
            out["progress"] = self.progress
        if self.state == JobState.SUCCEEDED:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        if self.cancel_requested and not self.finished:
            out["cancel_requested"] = True
        if self.started_at is not None:
            end = self.finished_at or time.monotonic()
            out["duration_ms"] = round((end - self.started_at) * 1000)
        return out


@dataclass
class PendingConfirmation:
    command: str
    digest: str
    expires_at: float


@dataclass
class PluginInfo:
    plugin_version: str | None = None
    lightroom_version: str | None = None
    catalog: str | None = None
    session_id: str | None = None


class ActivityLog:
    """The last few hundred things that happened, for the menu bar app."""

    def __init__(self, size: int = ACTIVITY_LOG_SIZE) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=size)
        self._seq = 0

    def add(self, **event: Any) -> None:
        self._seq += 1
        self._events.append({"seq": self._seq, "time": _now_iso(), **event})

    def since(self, after: int = 0) -> dict[str, Any]:
        return {
            "last_seq": self._seq,
            "events": [event for event in self._events if event["seq"] > after],
        }


class Hub:
    """Jobs, policy and plugin presence. Transport-agnostic, so it is easy to test."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        settings_path: Path | None = None,
        policies: dict[str, CommandPolicy] | None = None,
        connected_grace_s: float = CONNECTED_GRACE_S,
        delivery_timeout_s: float = DELIVERY_TIMEOUT_S,
    ) -> None:
        self.started_at = time.monotonic()
        self.settings_path = settings_path
        self.settings = settings or (Settings.load(settings_path) if settings_path else Settings())
        self.policies = policies
        self.connected_grace_s = connected_grace_s
        self.delivery_timeout_s = delivery_timeout_s
        self.plugin = PluginInfo()
        self.activity = ActivityLog()
        self.shutting_down = False
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._queue: deque[str] = deque()
        self._waiters: set[asyncio.Future[None]] = set()
        self._confirmations: dict[str, PendingConfirmation] = {}
        self._clients: dict[str, float] = {}
        self._active_polls = 0
        self._last_poll_end: float | None = None

    # -- presence and status ------------------------------------------------------------

    @property
    def plugin_connected(self) -> bool:
        if self._active_polls > 0:
            return True
        if self._last_poll_end is None:
            return False
        return time.monotonic() - self._last_poll_end <= self.connected_grace_s

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        last_seen = None
        if self._active_polls > 0:
            last_seen = 0.0
        elif self._last_poll_end is not None:
            last_seen = round(now - self._last_poll_end, 1)
        return {
            "hub": {
                "version": __version__,
                "protocol": PROTOCOL_VERSION,
                "pid": os.getpid(),
                "uptime_s": round(now - self.started_at, 1),
                "shutting_down": self.shutting_down,
            },
            "plugin": {
                "connected": self.plugin_connected,
                "last_seen_s_ago": last_seen,
                "plugin_version": self.plugin.plugin_version,
                "lightroom_version": self.plugin.lightroom_version,
                "catalog": self.plugin.catalog,
            },
            "jobs": {
                "queued": sum(1 for j in self._jobs.values() if j.state == JobState.QUEUED),
                "running": sum(1 for j in self._jobs.values() if j.state == JobState.RUNNING),
            },
            "clients": [
                {"name": name, "last_seen_s_ago": round(now - seen, 1)}
                for name, seen in sorted(self._clients.items(), key=lambda item: -item[1])
                if now - seen <= CLIENT_SEEN_WINDOW_S
            ],
            "settings": self.settings.as_dict(),
        }

    # -- settings -------------------------------------------------------------------------

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        self.settings.update(changes)
        if self.settings_path:
            self.settings.save(self.settings_path)
        self.activity.add(kind="settings", client="local", detail=self.settings.as_dict())
        return self.settings.as_dict()

    # -- running commands -----------------------------------------------------------------

    async def run(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        client: str = "unknown",
        wait_s: float | None = None,
        confirm: str | None = None,
    ) -> dict[str, Any]:
        """Run a command for a client, applying the policy. Never retries.

        Returns the job's response: finished (succeeded / failed / cancelled), still
        running after wait_s (poll it with get_job), or confirmation_required.
        """
        params = dict(params or {})
        self._clients[client] = time.monotonic()
        refusal = self._refusal(command)
        if refusal is not None:
            self._log_refusal(client, command, refusal)
            return refusal

        policy = policy_for(command, self.policies)
        if confirm is not None:
            if not self._consume_confirmation(confirm, command, params):
                response = _failed(
                    ErrorCode.INVALID_CONFIRMATION,
                    "This confirmation is unknown, expired, already used, or was issued for "
                    "different parameters. Run the command again without it to get a new one.",
                )
                self._log_refusal(client, command, response)
                return response
        elif policy.mutating and (policy.destructive or policy.supports_dry_run):
            gate = await self._confirmation_gate(command, params, policy, client)
            if gate is not None:
                return gate

        job = self.submit(command, params, client=client)
        return await self.wait(job, wait_s)

    def _refusal(self, command: str) -> dict[str, Any] | None:
        if self.shutting_down:
            return _failed(ErrorCode.HUB_UNAVAILABLE, "The Safelight hub is shutting down.")
        if self.settings.read_only and policy_for(command, self.policies).mutating:
            return _failed(
                ErrorCode.READ_ONLY_MODE,
                "Safelight is in read-only mode, so it cannot change the catalog. The user "
                "can turn read-only mode off in the Safelight settings.",
            )
        if not self.plugin_connected:
            return _failed(
                ErrorCode.LIGHTROOM_NOT_CONNECTED,
                "Lightroom Classic is not connected to Safelight. Open Lightroom Classic "
                "(with the Safelight plugin enabled) and try again.",
            )
        return None

    async def _confirmation_gate(
        self, command: str, params: dict[str, Any], policy: CommandPolicy, client: str
    ) -> dict[str, Any] | None:
        affected: int | None = None
        summary: str | None = None
        if policy.supports_dry_run:
            dry = self.submit(command, {**params, "dry_run": True}, client=client, internal=True)
            outcome = await self.wait(dry, DEFAULT_WAIT_S)
            if outcome["state"] != JobState.SUCCEEDED:
                return outcome
            plan = outcome.get("result") or {}
            affected = plan.get("affected") if isinstance(plan, dict) else None
            summary = plan.get("summary") if isinstance(plan, dict) else None

        threshold = self.settings.confirm_threshold
        if policy.destructive:
            reason = "This change cannot be undone easily."
        elif affected is not None and affected > threshold:
            reason = f"This change affects {affected} photos, more than the limit of {threshold}."
        else:
            return None

        token = secrets.token_urlsafe(12)
        self._confirmations[token] = PendingConfirmation(
            command=command,
            digest=_params_digest(command, params),
            expires_at=time.monotonic() + CONFIRMATION_TTL_S,
        )
        self.activity.add(
            kind="confirmation_required",
            client=client,
            command=command,
            affected=affected,
            detail=reason,
        )
        return {
            "job_id": None,
            "state": JobState.CONFIRMATION_REQUIRED,
            "confirmation": {
                "token": token,
                "command": command,
                "affected": affected,
                "summary": summary,
                "reason": reason,
                "expires_in_s": int(CONFIRMATION_TTL_S),
                "instructions": "Show this to the user and ask for approval. Only if they "
                "approve, run the same command with the same parameters and this token.",
            },
        }

    def _consume_confirmation(self, token: str, command: str, params: dict[str, Any]) -> bool:
        pending = self._confirmations.pop(token, None)
        if pending is None or pending.expires_at < time.monotonic():
            return False
        return pending.command == command and pending.digest == _params_digest(command, params)

    def _log_refusal(self, client: str, command: str, response: dict[str, Any]) -> None:
        self.activity.add(
            kind="job",
            client=client,
            command=command,
            state=response["state"],
            error=(response.get("error") or {}).get("code"),
        )

    # -- jobs -----------------------------------------------------------------------------

    def submit(
        self, command: str, params: dict[str, Any], *, client: str, internal: bool = False
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex, command=command, params=params, client=client, internal=internal
        )
        self._jobs[job.id] = job
        self._queue.append(job.id)
        self._wake_pollers()
        return job

    async def wait(self, job: Job, wait_s: float | None) -> dict[str, Any]:
        wait = DEFAULT_WAIT_S if wait_s is None else max(0.0, min(wait_s, MAX_WAIT_S))
        deadline = time.monotonic() + wait
        while not job.finished:
            remaining = deadline - time.monotonic()
            if job.state == JobState.QUEUED:
                undelivered_at = job.created_at + self.delivery_timeout_s
                remaining = min(remaining, undelivered_at - time.monotonic())
            if remaining <= 0:
                self._expire_undelivered(job)
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(job.done.wait(), remaining)
        return job.response()

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            return _failed(ErrorCode.UNKNOWN_JOB, f"No job with id {job_id}.")
        if job.state == JobState.QUEUED:
            with contextlib.suppress(ValueError):
                self._queue.remove(job.id)
            self._finish(job, JobState.CANCELLED, error=_error(ErrorCode.CANCELLED, "Cancelled."))
        elif job.state == JobState.RUNNING:
            # Lightroom learns about it at its next progress report; commands without
            # progress reports run to completion.
            job.cancel_requested = True
        return job.response()

    def recent_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        jobs = [j for j in reversed(self._jobs.values()) if not j.internal]
        return [dict(j.response(), client=j.client) for j in jobs[:limit]]

    def _expire_undelivered(self, job: Job) -> None:
        if job.state != JobState.QUEUED:
            return
        if time.monotonic() - job.created_at < self.delivery_timeout_s:
            return
        with contextlib.suppress(ValueError):
            self._queue.remove(job.id)
        self._finish(
            job,
            JobState.CANCELLED,
            error=_error(
                ErrorCode.NOT_DELIVERED,
                f"Lightroom did not pick up '{job.command}' in time; it was not run.",
            ),
        )

    def _finish(
        self, job: Job, state: str, *, result: Any = None, error: dict[str, Any] | None = None
    ) -> None:
        if job.finished:
            return
        job.state = state
        job.result = result
        job.error = error
        job.finished_at = time.monotonic()
        job.done.set()
        if not job.internal:
            duration = None
            if job.started_at is not None:
                duration = round((job.finished_at - job.started_at) * 1000)
            self.activity.add(
                kind="job",
                job_id=job.id,
                client=job.client,
                command=job.command,
                state=state,
                duration_ms=duration,
                error=(error or {}).get("code"),
            )

    # -- plugin side ----------------------------------------------------------------------

    async def poll(self, info: dict[str, Any], wait_s: float) -> dict[str, Any]:
        """Hand queued jobs to the plugin, waiting up to wait_s for one to arrive."""
        self._update_plugin_info(info)
        self._active_polls += 1
        try:
            deadline = time.monotonic() + max(0.0, min(wait_s, MAX_POLL_WAIT_S))
            while True:
                commands = self._take_queued()
                remaining = deadline - time.monotonic()
                if commands or remaining <= 0 or self.shutting_down:
                    return {"session_id": self.plugin.session_id, "commands": commands}
                waiter = asyncio.get_running_loop().create_future()
                self._waiters.add(waiter)
                try:
                    await asyncio.wait_for(waiter, remaining)
                except TimeoutError:
                    pass
                finally:
                    self._waiters.discard(waiter)
        finally:
            self._active_polls -= 1
            self._last_poll_end = time.monotonic()

    def report_progress(self, message: dict[str, Any]) -> dict[str, Any]:
        job = self._jobs.get(str(message.get("id")))
        if job is None or job.finished:
            return {"accepted": False, "cancel": True}
        job.progress = {key: message[key] for key in ("done", "total", "message") if key in message}
        return {"accepted": True, "cancel": job.cancel_requested}

    def deliver_result(self, message: dict[str, Any]) -> bool:
        job = self._jobs.get(str(message.get("id")))
        if job is None or job.finished:
            return False
        if message.get("success"):
            self._finish(job, JobState.SUCCEEDED, result=message.get("result"))
        else:
            raw = message.get("error") or {}
            code = str(raw.get("code") or ErrorCode.COMMAND_FAILED)
            state = JobState.CANCELLED if code == ErrorCode.CANCELLED else JobState.FAILED
            self._finish(
                job,
                state,
                error=_error(code, str(raw.get("message") or "Lightroom reported an error")),
            )
        return True

    def shutdown(self) -> None:
        """Release held plugin polls at once so the server can stop without waiting for them."""
        self.shutting_down = True
        self._wake_pollers()

    # -- housekeeping ---------------------------------------------------------------------

    def maintenance(self) -> None:
        """Called every second or so: timeouts, disconnections, pruning."""
        now = time.monotonic()
        for job in list(self._jobs.values()):
            if job.state == JobState.QUEUED:
                self._expire_undelivered(job)
            elif job.state == JobState.RUNNING and not self.plugin_connected:
                self._finish(
                    job,
                    JobState.FAILED,
                    error=_error(
                        ErrorCode.LIGHTROOM_DISCONNECTED,
                        f"Lightroom disconnected while running '{job.command}'. It may or may "
                        "not have completed.",
                    ),
                )
        while len(self._jobs) > MAX_RETAINED_JOBS:
            oldest = next(iter(self._jobs.values()))
            if not oldest.finished:
                break
            self._jobs.popitem(last=False)
        for job_id, job in list(self._jobs.items()):
            if job.finished and job.finished_at and now - job.finished_at > JOB_RETENTION_S:
                del self._jobs[job_id]
        for token, pending in list(self._confirmations.items()):
            if pending.expires_at < now:
                del self._confirmations[token]

    # -- internals ------------------------------------------------------------------------

    def _update_plugin_info(self, info: dict[str, Any]) -> None:
        session_id = info.get("session_id") or self.plugin.session_id or uuid.uuid4().hex
        self.plugin = PluginInfo(
            plugin_version=info.get("plugin_version"),
            lightroom_version=info.get("lightroom_version"),
            catalog=info.get("catalog"),
            session_id=session_id,
        )

    def _take_queued(self) -> list[dict[str, Any]]:
        out = []
        while self._queue:
            job = self._jobs.get(self._queue.popleft())
            if job is None or job.state != JobState.QUEUED:
                continue
            job.state = JobState.RUNNING
            job.started_at = time.monotonic()
            out.append({"id": job.id, "command": job.command, "params": job.params})
        return out

    def _wake_pollers(self) -> None:
        for waiter in list(self._waiters):
            if not waiter.done():
                waiter.set_result(None)


# -- HTTP layer -------------------------------------------------------------------------

Handler = Callable[[Request], Awaitable[Response]]


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def build_app(
    hub: Hub,
    token: str,
    lifespan: Callable[[Starlette], contextlib.AbstractAsyncContextManager[None]] | None = None,
) -> Starlette:
    def guard(handler: Handler) -> Handler:
        async def wrapped(request: Request) -> Response:
            # Browsers always send Origin on cross-site requests: no web page may talk to the hub.
            if request.headers.get("origin"):
                return JSONResponse(
                    _failed(ErrorCode.BAD_REQUEST, "browser requests are refused"), 403
                )
            host = (request.headers.get("host") or "").split(":")[0]
            if host not in {"127.0.0.1", "localhost"}:
                return JSONResponse(_failed(ErrorCode.BAD_REQUEST, "unexpected Host"), 403)
            if not secrets.compare_digest(request.headers.get(TOKEN_HEADER, ""), token):
                return JSONResponse(_failed(ErrorCode.BAD_REQUEST, "missing or wrong token"), 401)
            return await handler(request)

        return wrapped

    async def read_json(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    def client_of(request: Request) -> str:
        return (request.headers.get(CLIENT_HEADER) or "unknown")[:80]

    async def status(_: Request) -> Response:
        return JSONResponse(hub.status())

    async def rpc(request: Request) -> Response:
        body = await read_json(request)
        command = body.get("command")
        if not isinstance(command, str) or not command:
            return JSONResponse(_failed(ErrorCode.BAD_REQUEST, "command is required"), 400)
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        confirm = body.get("confirm") if isinstance(body.get("confirm"), str) else None
        result = await hub.run(
            command,
            params,
            client=client_of(request),
            wait_s=_to_float(body.get("wait_s")),
            confirm=confirm,
        )
        return JSONResponse(result)

    async def get_job(request: Request) -> Response:
        job = hub.get_job(request.path_params["job_id"])
        if job is None:
            return JSONResponse(_failed(ErrorCode.UNKNOWN_JOB, "No such job."), 404)
        wait = _to_float(request.query_params.get("wait_s")) or 0.0
        return JSONResponse(await hub.wait(job, wait))

    async def cancel_job(request: Request) -> Response:
        return JSONResponse(hub.cancel_job(request.path_params["job_id"]))

    async def list_jobs(_: Request) -> Response:
        return JSONResponse({"jobs": hub.recent_jobs()})

    async def activity(request: Request) -> Response:
        after = int(_to_float(request.query_params.get("after")) or 0)
        return JSONResponse(hub.activity.since(after))

    async def settings(request: Request) -> Response:
        if request.method == "PUT":
            try:
                return JSONResponse(hub.update_settings(await read_json(request)))
            except ValueError as exc:
                return JSONResponse(_failed(ErrorCode.BAD_REQUEST, str(exc)), 400)
        return JSONResponse(hub.settings.as_dict())

    async def plugin_poll(request: Request) -> Response:
        body = await read_json(request)
        protocol = body.get("protocol")
        if protocol != PROTOCOL_VERSION:
            return JSONResponse(
                _failed(
                    ErrorCode.BAD_REQUEST,
                    f"protocol mismatch: plugin speaks {protocol}, hub speaks {PROTOCOL_VERSION}",
                ),
                409,
            )
        wait = _to_float(body.get("wait_s"))
        return JSONResponse(await hub.poll(body, MAX_POLL_WAIT_S if wait is None else wait))

    async def plugin_progress(request: Request) -> Response:
        return JSONResponse(hub.report_progress(await read_json(request)))

    async def plugin_result(request: Request) -> Response:
        accepted = hub.deliver_result(await read_json(request))
        return JSONResponse({"accepted": accepted})

    return Starlette(
        routes=[
            Route("/v1/status", guard(status), methods=["GET"]),
            Route("/v1/rpc", guard(rpc), methods=["POST"]),
            Route("/v1/jobs", guard(list_jobs), methods=["GET"]),
            Route("/v1/jobs/{job_id}", guard(get_job), methods=["GET"]),
            Route("/v1/jobs/{job_id}/cancel", guard(cancel_job), methods=["POST"]),
            Route("/v1/activity", guard(activity), methods=["GET"]),
            Route("/v1/settings", guard(settings), methods=["GET", "PUT"]),
            Route("/v1/plugin/poll", guard(plugin_poll), methods=["POST"]),
            Route("/v1/plugin/progress", guard(plugin_progress), methods=["POST"]),
            Route("/v1/plugin/result", guard(plugin_result), methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def write_hub_config(path: Path, port: int, token: str) -> None:
    """Write hub.json readable by the current user only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    payload = {
        "port": port,
        "token": token,
        "pid": os.getpid(),
        "version": __version__,
        "protocol": PROTOCOL_VERSION,
        "started_at": time.time(),
    }
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, path)


class _UvicornServer(uvicorn.Server):
    """uvicorn server that tells the hub to release long polls as soon as shutdown starts."""

    def __init__(self, config: uvicorn.Config, on_exit: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_exit = on_exit

    def handle_exit(self, sig: int, frame: Any) -> None:
        self._on_exit()
        super().handle_exit(sig, frame)


class HubServer:
    """Runs the hub over HTTP on 127.0.0.1 and publishes hub.json once listening."""

    def __init__(self, home: Path, port: int, hub: Hub | None = None) -> None:
        self.home = home
        self.requested_port = port
        self.token = secrets.token_urlsafe(32)
        self.hub = hub or Hub(settings_path=home / "settings.json")
        self.port: int | None = None
        config = uvicorn.Config(
            build_app(self.hub, self.token, lifespan=self._lifespan),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
            timeout_graceful_shutdown=3,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = _UvicornServer(config, on_exit=self._begin_shutdown)

    def _begin_shutdown(self) -> None:
        # May run inside a signal handler: hand the work to the event loop thread-safely.
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.hub.shutdown)

    @contextlib.asynccontextmanager
    async def _lifespan(self, _: Starlette) -> AsyncIterator[None]:
        # Runs inside uvicorn's shutdown, so hub.json is removed even on SIGTERM / Ctrl-C
        # (uvicorn re-raises those signals once it has shut down).
        watchdog = asyncio.create_task(self._watch())
        try:
            yield
        finally:
            watchdog.cancel()
            self._remove_config()

    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._server.serve()

    def stop(self) -> None:
        self._begin_shutdown()
        self._server.should_exit = True

    async def _watch(self) -> None:
        while not self._server.started:
            await asyncio.sleep(0.02)
        sockets = [s for server in self._server.servers for s in server.sockets]
        self.port = sockets[0].getsockname()[1]
        write_hub_config(hub_config_path(self.home), self.port, self.token)
        LOGGER.info("Safelight hub %s listening on 127.0.0.1:%s", __version__, self.port)
        while True:
            await asyncio.sleep(1)
            self.hub.maintenance()

    def _remove_config(self) -> None:
        path = hub_config_path(self.home)
        with contextlib.suppress(OSError, json.JSONDecodeError):
            if json.loads(path.read_text()).get("pid") == os.getpid():
                path.unlink()


def run_hub(home: Path, port: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(HubServer(home, port).serve())
