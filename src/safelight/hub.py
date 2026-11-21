"""The Safelight hub: a local daemon between AI clients and the Lightroom plugin.

AI clients (through `safelight mcp` relays) send commands with POST /v1/rpc.
The Lightroom plugin long-polls POST /v1/plugin/poll to receive them and posts
each answer to POST /v1/plugin/result. Everything listens on 127.0.0.1 only and
requires the per-install token stored in hub.json.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import __version__
from .paths import hub_config_path
from .protocol import (
    CONNECTED_GRACE_S,
    DEFAULT_COMMAND_TIMEOUT_S,
    MAX_COMMAND_TIMEOUT_S,
    MAX_POLL_WAIT_S,
    PROTOCOL_VERSION,
    TOKEN_HEADER,
    ErrorCode,
)

LOGGER = logging.getLogger("safelight.hub")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}


@dataclass
class PendingCommand:
    id: str
    command: str
    params: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]
    delivered: bool = False
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class PluginInfo:
    plugin_version: str | None = None
    lightroom_version: str | None = None
    catalog: str | None = None
    session_id: str | None = None


class Hub:
    """Command queue and plugin presence tracking. Transport-agnostic, so it is easy to test."""

    def __init__(self, *, connected_grace_s: float = CONNECTED_GRACE_S) -> None:
        self.started_at = time.monotonic()
        self.connected_grace_s = connected_grace_s
        self.plugin = PluginInfo()
        self._queue: deque[str] = deque()
        self._pending: dict[str, PendingCommand] = {}
        self._waiters: set[asyncio.Future[None]] = set()
        self._active_polls = 0
        self._last_poll_end: float | None = None
        self.commands_handled = 0
        self.shutting_down = False

    # -- presence -----------------------------------------------------------------

    @property
    def plugin_connected(self) -> bool:
        if self._active_polls > 0:
            return True
        if self._last_poll_end is None:
            return False
        return time.monotonic() - self._last_poll_end <= self.connected_grace_s

    def status(self) -> dict[str, Any]:
        last_seen = None
        if self._active_polls > 0:
            last_seen = 0.0
        elif self._last_poll_end is not None:
            last_seen = round(time.monotonic() - self._last_poll_end, 1)
        return {
            "hub": {
                "version": __version__,
                "protocol": PROTOCOL_VERSION,
                "pid": os.getpid(),
                "uptime_s": round(time.monotonic() - self.started_at, 1),
                "commands_handled": self.commands_handled,
                "shutting_down": self.shutting_down,
            },
            "plugin": {
                "connected": self.plugin_connected,
                "last_seen_s_ago": last_seen,
                "plugin_version": self.plugin.plugin_version,
                "lightroom_version": self.plugin.lightroom_version,
                "catalog": self.plugin.catalog,
            },
            "queue": {
                "waiting": len(self._queue),
                "in_progress": sum(1 for p in self._pending.values() if p.delivered),
            },
        }

    # -- client side ----------------------------------------------------------------

    async def call(
        self, command: str, params: dict[str, Any] | None = None, timeout_s: float | None = None
    ) -> dict[str, Any]:
        """Queue a command for Lightroom and wait for its answer. Never retries."""
        if self.shutting_down:
            return _error(ErrorCode.HUB_UNAVAILABLE, "The Safelight hub is shutting down.")
        if not self.plugin_connected:
            return _error(
                ErrorCode.LIGHTROOM_NOT_CONNECTED,
                "Lightroom Classic is not connected to Safelight. Open Lightroom Classic "
                "(with the Safelight plugin enabled) and try again.",
            )
        timeout = min(timeout_s or DEFAULT_COMMAND_TIMEOUT_S, MAX_COMMAND_TIMEOUT_S)
        loop = asyncio.get_running_loop()
        pending = PendingCommand(
            id=uuid.uuid4().hex, command=command, params=params or {}, future=loop.create_future()
        )
        self._pending[pending.id] = pending
        self._queue.append(pending.id)
        self._wake_pollers()
        try:
            return await asyncio.wait_for(asyncio.shield(pending.future), timeout)
        except TimeoutError:
            if pending.delivered:
                return _error(
                    ErrorCode.TIMEOUT,
                    f"Lightroom did not answer '{command}' within {timeout:.0f}s. It may still "
                    "be running: check Lightroom before trying again.",
                )
            with contextlib.suppress(ValueError):
                self._queue.remove(pending.id)
            return _error(
                ErrorCode.NOT_DELIVERED,
                f"Lightroom did not pick up '{command}' within {timeout:.0f}s; it was not run.",
            )
        finally:
            self._pending.pop(pending.id, None)
            self.commands_handled += 1

    # -- plugin side ----------------------------------------------------------------

    async def poll(self, info: dict[str, Any], wait_s: float) -> dict[str, Any]:
        """Hand queued commands to the plugin, waiting up to wait_s for one to arrive."""
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

    def shutdown(self) -> None:
        """Release held plugin polls at once so the server can stop without waiting for them."""
        self.shutting_down = True
        self._wake_pollers()

    def deliver_result(self, message: dict[str, Any]) -> bool:
        pending = self._pending.get(str(message.get("id")))
        if pending is None or pending.future.done():
            return False
        if message.get("success"):
            pending.future.set_result({"success": True, "result": message.get("result")})
        else:
            error = message.get("error") or {}
            pending.future.set_result(
                _error(
                    str(error.get("code") or ErrorCode.COMMAND_FAILED),
                    str(error.get("message") or "Lightroom reported an error"),
                )
            )
        return True

    def fail_in_flight_if_disconnected(self) -> None:
        if self.plugin_connected:
            return
        for pending in list(self._pending.values()):
            if pending.delivered and not pending.future.done():
                pending.future.set_result(
                    _error(
                        ErrorCode.LIGHTROOM_DISCONNECTED,
                        f"Lightroom disconnected while running '{pending.command}'. It may or "
                        "may not have completed.",
                    )
                )

    # -- internals ------------------------------------------------------------------

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
            pending = self._pending.get(self._queue.popleft())
            if pending is None or pending.future.done():
                continue
            pending.delivered = True
            out.append({"id": pending.id, "command": pending.command, "params": pending.params})
        return out

    def _wake_pollers(self) -> None:
        for waiter in list(self._waiters):
            if not waiter.done():
                waiter.set_result(None)


# -- HTTP layer -------------------------------------------------------------------------

Handler = Callable[[Request], Awaitable[Response]]


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
                    _error(ErrorCode.BAD_REQUEST, "browser requests are refused"), 403
                )
            host = (request.headers.get("host") or "").split(":")[0]
            if host not in {"127.0.0.1", "localhost"}:
                return JSONResponse(_error(ErrorCode.BAD_REQUEST, "unexpected Host"), 403)
            if not secrets.compare_digest(request.headers.get(TOKEN_HEADER, ""), token):
                return JSONResponse(_error(ErrorCode.BAD_REQUEST, "missing or wrong token"), 401)
            return await handler(request)

        return wrapped

    async def read_json(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    async def status(_: Request) -> Response:
        return JSONResponse(hub.status())

    async def rpc(request: Request) -> Response:
        body = await read_json(request)
        command = body.get("command")
        if not isinstance(command, str) or not command:
            return JSONResponse(_error(ErrorCode.BAD_REQUEST, "command is required"), 400)
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        timeout = body.get("timeout_s")
        result = await hub.call(
            command, params, float(timeout) if isinstance(timeout, int | float) else None
        )
        return JSONResponse(result)

    async def plugin_poll(request: Request) -> Response:
        body = await read_json(request)
        protocol = body.get("protocol")
        if protocol != PROTOCOL_VERSION:
            return JSONResponse(
                _error(
                    ErrorCode.BAD_REQUEST,
                    f"protocol mismatch: plugin speaks {protocol}, hub speaks {PROTOCOL_VERSION}",
                ),
                409,
            )
        wait = body.get("wait_s")
        return JSONResponse(await hub.poll(body, float(wait) if wait else MAX_POLL_WAIT_S))

    async def plugin_result(request: Request) -> Response:
        accepted = hub.deliver_result(await read_json(request))
        return JSONResponse({"accepted": accepted})

    return Starlette(
        routes=[
            Route("/v1/status", guard(status), methods=["GET"]),
            Route("/v1/rpc", guard(rpc), methods=["POST"]),
            Route("/v1/plugin/poll", guard(plugin_poll), methods=["POST"]),
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

    def __init__(self, home: Path, port: int) -> None:
        self.home = home
        self.requested_port = port
        self.token = secrets.token_urlsafe(32)
        self.hub = Hub()
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
            await asyncio.sleep(2)
            self.hub.fail_in_flight_if_disconnected()

    def _remove_config(self) -> None:
        path = hub_config_path(self.home)
        with contextlib.suppress(OSError, json.JSONDecodeError):
            if json.loads(path.read_text()).get("pid") == os.getpid():
                path.unlink()


def run_hub(home: Path, port: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(HubServer(home, port).serve())
