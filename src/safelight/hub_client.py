"""Client used by the MCP relay and the CLI to talk to the hub, starting it if needed."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import hub_config_path, hub_log_path, safelight_home
from .protocol import TOKEN_HEADER, ErrorCode


class HubUnavailable(RuntimeError):
    """The hub is not running and could not be started."""


@dataclass(frozen=True)
class HubConfig:
    port: int
    token: str
    pid: int | None = None

    @classmethod
    def read(cls, path: Path) -> HubConfig | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(port=int(data["port"]), token=str(data["token"]), pid=data.get("pid"))
        except (OSError, ValueError, KeyError, TypeError):
            return None


class HubClient:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or safelight_home()

    # -- low level -------------------------------------------------------------------

    def _request(
        self, config: HubConfig, method: str, path: str, body: Any = None, timeout: float = 5.0
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{config.port}{path}",
            data=data,
            method=method,
            headers={TOKEN_HEADER: config.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read())
            except ValueError:
                raise HubUnavailable(f"hub answered HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            raise HubUnavailable(str(exc)) from exc

    def _config(self) -> HubConfig | None:
        return HubConfig.read(hub_config_path(self.home))

    def _probe(self) -> tuple[str, dict[str, Any] | None]:
        """("ok", status) when a hub answers, ("busy", None) when one is listening but not
        ready (for example shutting down), ("down", None) when none is running."""
        config = self._config()
        if config is None:
            return "down", None
        try:
            status = self._request(config, "GET", "/v1/status", timeout=2.0)
        except HubUnavailable as exc:
            cause = exc.__cause__
            reason = cause.reason if isinstance(cause, urllib.error.URLError) else cause
            refused = isinstance(reason, ConnectionRefusedError)
            return ("down" if refused else "busy"), None
        hub = status.get("hub") if isinstance(status, dict) else None
        if not isinstance(hub, dict) or hub.get("shutting_down"):
            return "busy", None
        return "ok", status

    def _spawn_hub(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        log = open(hub_log_path(self.home), "ab")  # noqa: SIM115 - handed to the child process
        env = dict(os.environ, SAFELIGHT_HOME=str(self.home))
        subprocess.Popen(
            [sys.executable, "-m", "safelight.cli", "hub"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        log.close()

    def _ensure_running_sync(self, start: bool, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        spawned = False
        while True:
            state, status = self._probe()
            if state == "ok" and status is not None:
                return status
            if state == "down" and not start:
                raise HubUnavailable("the Safelight hub is not running")
            if state == "down" and not spawned:
                self._spawn_hub()
                spawned = True
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        raise HubUnavailable(
            f"the Safelight hub did not become ready; see {hub_log_path(self.home)} for details"
        )

    # -- public async API ---------------------------------------------------------------

    async def status(self, *, start: bool = True, timeout: float = 8.0) -> dict[str, Any]:
        """Hub status, starting the hub first if needed. Raises HubUnavailable."""
        return await asyncio.to_thread(self._ensure_running_sync, start, timeout)

    async def call(
        self, command: str, params: dict[str, Any] | None = None, timeout_s: float = 30.0
    ) -> dict[str, Any]:
        """Run a Lightroom command through the hub. Returns {success, result|error}."""
        try:
            await self.status()
            config = self._config()
            if config is None:
                raise HubUnavailable("hub.json disappeared")
            return await asyncio.to_thread(
                self._request,
                config,
                "POST",
                "/v1/rpc",
                {"command": command, "params": params or {}, "timeout_s": timeout_s},
                timeout_s + 5.0,
            )
        except HubUnavailable as exc:
            return {
                "success": False,
                "error": {"code": ErrorCode.HUB_UNAVAILABLE, "message": str(exc)},
            }
