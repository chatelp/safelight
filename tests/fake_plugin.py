"""A stand-in for the Lightroom plugin: polls the hub over HTTP and answers commands."""

from __future__ import annotations

import asyncio
import json
import urllib.request
from collections.abc import Callable
from typing import Any

from safelight.protocol import PROTOCOL_VERSION, TOKEN_HEADER

Handler = Callable[[dict[str, Any]], Any]


class FakePlugin:
    def __init__(self, port: int, token: str, handlers: dict[str, Handler] | None = None) -> None:
        self.port = port
        self.token = token
        self.handlers: dict[str, Handler] = {"system.ping": lambda _: {"pong": True}}
        self.handlers.update(handlers or {})
        self.received: list[str] = []
        self.wait_s = 0.5
        self._stopped = False

    def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={TOKEN_HEADER: self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())

    async def run(self) -> None:
        info = {
            "protocol": PROTOCOL_VERSION,
            "plugin_version": "fake",
            "lightroom_version": "16.0.1",
            "catalog": "/tmp/fake.lrcat",
        }
        while not self._stopped:
            info["wait_s"] = self.wait_s
            reply = await asyncio.to_thread(self._post, "/v1/plugin/poll", info, 30.0)
            for command in reply.get("commands", []):
                self.received.append(command["command"])
                handler = self.handlers.get(command["command"])
                if handler is None:
                    message = {
                        "id": command["id"],
                        "success": False,
                        "error": {"code": "UNKNOWN_COMMAND", "message": command["command"]},
                    }
                else:
                    message = {
                        "id": command["id"],
                        "success": True,
                        "result": handler(command["params"]),
                    }
                await asyncio.to_thread(self._post, "/v1/plugin/result", message, 5.0)

    def stop(self) -> None:
        self._stopped = True
