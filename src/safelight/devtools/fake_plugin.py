"""A stand-in for the Lightroom plugin.

It speaks the same protocol as plugin/Safelight.lrplugin: long-polls the hub, runs each
command in its own task, reports progress, honors cancellation and dry runs. Used by the
test suite, and by `safelight fake-plugin` to develop clients (such as the menu bar app)
without Lightroom.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from ..protocol import PROTOCOL_VERSION, TOKEN_HEADER, ErrorCode

LOGGER = logging.getLogger("safelight.fake_plugin")

Progress = Callable[[int, int, str | None], Awaitable[bool]]
"""Report progress: (done, total, message) -> True when the hub asks to cancel."""
Handler = Callable[[dict[str, Any], Progress], Awaitable[Any]]


class Cancelled(Exception):
    """Raised by a handler that stops because the hub asked it to."""


class CommandError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def _ping(_: dict[str, Any], __: Progress) -> Any:
    return {"pong": True, "plugin": "fake"}


async def _echo(params: dict[str, Any], _: Progress) -> Any:
    return params


async def _bulk_edit(params: dict[str, Any], progress: Progress) -> Any:
    """Pretend to edit `count` photos, `delay_s` seconds each."""
    count = int(params.get("count", 10))
    if params.get("dry_run"):
        return {"affected": count, "summary": f"Edit {count} photos"}
    delay = float(params.get("delay_s", 0.0))
    for done in range(count):
        if await progress(done, count, f"Editing photo {done + 1} of {count}"):
            raise Cancelled(f"Stopped after {done} of {count} photos")
        await asyncio.sleep(delay)
    await progress(count, count, "Done")
    return {"edited": count}


async def _remove_photos(params: dict[str, Any], _: Progress) -> Any:
    count = int(params.get("count", 1))
    if params.get("dry_run"):
        return {"affected": count, "summary": f"Remove {count} photos from the catalog"}
    return {"removed": count}


DEFAULT_HANDLERS: dict[str, Handler] = {
    "system.ping": _ping,
    "echo": _echo,
    "fake.bulk_edit": _bulk_edit,
    "fake.remove_photos": _remove_photos,
}


class FakePlugin:
    def __init__(
        self,
        port: int,
        token: str,
        handlers: dict[str, Handler] | None = None,
        *,
        wait_s: float = 0.5,
    ) -> None:
        self.port = port
        self.token = token
        self.handlers: dict[str, Handler] = dict(DEFAULT_HANDLERS)
        self.handlers.update(handlers or {})
        self.wait_s = wait_s
        self.received: list[str] = []
        self._stopped = False
        self._tasks: set[asyncio.Task[None]] = set()

    def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={TOKEN_HEADER: self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())

    async def run(self) -> None:
        info: dict[str, Any] = {
            "protocol": PROTOCOL_VERSION,
            "plugin_version": "fake",
            "lightroom_version": "16.0.1 (fake)",
            "catalog": "/tmp/Fake Catalog.lrcat",
        }
        while not self._stopped:
            info["wait_s"] = self.wait_s
            try:
                reply = await asyncio.to_thread(self._post, "/v1/plugin/poll", info, 30.0)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                LOGGER.debug("poll failed: %s", exc)
                await asyncio.sleep(1)
                continue
            for command in reply.get("commands", []):
                task = asyncio.create_task(self._execute(command))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _execute(self, command: dict[str, Any]) -> None:
        name = command["command"]
        self.received.append(name)

        async def progress(done: int, total: int, message: str | None = None) -> bool:
            body = {"id": command["id"], "done": done, "total": total}
            if message:
                body["message"] = message
            reply = await asyncio.to_thread(self._post, "/v1/plugin/progress", body, 5.0)
            return bool(reply.get("cancel"))

        handler = self.handlers.get(name)
        message: dict[str, Any] = {"id": command["id"]}
        if handler is None:
            message.update(
                success=False,
                error={"code": ErrorCode.UNKNOWN_COMMAND, "message": f"Unknown command: {name}"},
            )
        else:
            try:
                message.update(success=True, result=await handler(command["params"], progress))
            except Cancelled as exc:
                message.update(
                    success=False, error={"code": ErrorCode.CANCELLED, "message": str(exc)}
                )
            except CommandError as exc:
                message.update(success=False, error={"code": exc.code, "message": str(exc)})
            except Exception as exc:  # report every handler failure to the hub
                message.update(
                    success=False,
                    error={"code": ErrorCode.COMMAND_FAILED, "message": repr(exc)},
                )
        try:
            await asyncio.to_thread(self._post, "/v1/plugin/result", message, 5.0)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            LOGGER.warning("could not deliver result of %s: %s", name, exc)

    def stop(self) -> None:
        self._stopped = True


async def run_against_local_hub() -> None:
    """`safelight fake-plugin`: connect to the hub described by hub.json, like Lightroom."""
    from ..hub_client import HubClient, HubConfig
    from ..paths import hub_config_path

    client = HubClient()
    await client.status()
    config = HubConfig.read(hub_config_path(client.home))
    if config is None:
        raise SystemExit("hub.json not found")
    from .fake_catalog import FakeCatalog

    print(f"Fake Lightroom plugin connected to the hub on port {config.port}. Ctrl-C to stop.")
    await FakePlugin(config.port, config.token, FakeCatalog().handlers(), wait_s=20).run()
