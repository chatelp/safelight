"""Client used by the MCP relay and the CLI to talk to the hub, starting it if needed."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import hub_config_path, hub_log_path, safelight_home
from .protocol import CLIENT_HEADER, DEFAULT_WAIT_S, TOKEN_HEADER, ErrorCode, JobState


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


def unavailable(message: str) -> dict[str, Any]:
    return {
        "job_id": None,
        "state": JobState.FAILED,
        "error": {"code": ErrorCode.HUB_UNAVAILABLE, "message": message},
    }


class HubClient:
    def __init__(self, home: Path | None = None, client_name: str = "cli") -> None:
        self.home = home or safelight_home()
        self.client_name = client_name

    # -- low level -------------------------------------------------------------------

    def _request(
        self,
        config: HubConfig,
        method: str,
        path: str,
        body: Any = None,
        timeout: float = 5.0,
        client: str | None = None,
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{config.port}{path}",
            data=data,
            method=method,
            headers={
                TOKEN_HEADER: config.token,
                CLIENT_HEADER: client or self.client_name,
                "Content-Type": "application/json",
            },
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

    async def _call_hub(
        self,
        method: str,
        path: str,
        body: Any = None,
        timeout: float = 5.0,
        client: str | None = None,
    ) -> dict[str, Any]:
        """Make sure the hub runs, then send one request. Raises HubUnavailable."""
        await self.status()
        config = self._config()
        if config is None:
            raise HubUnavailable("hub.json disappeared")
        return await asyncio.to_thread(self._request, config, method, path, body, timeout, client)

    # -- public async API ---------------------------------------------------------------

    async def status(self, *, start: bool = True, timeout: float = 8.0) -> dict[str, Any]:
        """Hub status, starting the hub first if needed. Raises HubUnavailable."""
        return await asyncio.to_thread(self._ensure_running_sync, start, timeout)

    async def run(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        wait_s: float = DEFAULT_WAIT_S,
        confirm: str | None = None,
        client: str | None = None,
    ) -> dict[str, Any]:
        """Run a Lightroom command through the hub.

        Returns the job response: `state` is succeeded, failed, cancelled, running (poll
        with `job`) or confirmation_required. Never raises for hub problems.
        """
        body: dict[str, Any] = {"command": command, "params": params or {}, "wait_s": wait_s}
        if confirm:
            body["confirm"] = confirm
        try:
            return await self._call_hub("POST", "/v1/rpc", body, wait_s + 40.0, client)
        except HubUnavailable as exc:
            return unavailable(str(exc))

    async def job(self, job_id: str, wait_s: float = 0.0) -> dict[str, Any]:
        query = urllib.parse.urlencode({"wait_s": wait_s})
        path = f"/v1/jobs/{urllib.parse.quote(job_id)}?{query}"
        try:
            return await self._call_hub("GET", path, timeout=wait_s + 10.0)
        except HubUnavailable as exc:
            return unavailable(str(exc))

    async def cancel(self, job_id: str) -> dict[str, Any]:
        try:
            return await self._call_hub("POST", f"/v1/jobs/{urllib.parse.quote(job_id)}/cancel")
        except HubUnavailable as exc:
            return unavailable(str(exc))

    async def jobs(self) -> dict[str, Any]:
        return await self._call_hub("GET", "/v1/jobs")

    async def activity(self, after: int = 0) -> dict[str, Any]:
        return await self._call_hub("GET", f"/v1/activity?after={int(after)}")

    async def settings(self, changes: dict[str, Any] | None = None) -> dict[str, Any]:
        if changes:
            return await self._call_hub("PUT", "/v1/settings", changes)
        return await self._call_hub("GET", "/v1/settings")
