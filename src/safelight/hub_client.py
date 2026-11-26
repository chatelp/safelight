"""Client used by the MCP relay and the CLI to talk to the hub, starting it if needed.

When no hub runs, the client opens the Safelight app, which runs the hub (the app is the
control post: settings, activity, approvals). If the app is not installed, or does not
bring a hub up within `APP_WAIT_S`, the client starts the hub on its own.
"""

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

from .paths import default_home, hub_config_path, hub_log_path, safelight_home
from .protocol import CLIENT_HEADER, DEFAULT_WAIT_S, TOKEN_HEADER, ErrorCode, JobState

APP_BUNDLE_ID = "io.github.chatelp.safelight"
APP_WAIT_S = 6.0  # an app launched cold takes about 2 s to bring its hub up
START_TIMEOUT_S = 12.0


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

    def _app_allowed(self) -> bool:
        """Only for the user's real home: the app knows no other (tests use their own)."""
        return (
            sys.platform == "darwin"
            and os.environ.get("SAFELIGHT_OPEN_APP", "1") != "0"
            and self.home == default_home()
        )

    def _open_app(self) -> bool:
        """Opens the Safelight app in the background. False if it is not installed."""
        if not self._app_allowed():
            return False
        try:
            done = subprocess.run(
                ["/usr/bin/open", "-g", "-b", APP_BUNDLE_ID],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.returncode == 0

    def _spawn_hub(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        log = open(hub_log_path(self.home), "ab")  # noqa: SIM115 - handed to the child process
        env = dict(os.environ, SAFELIGHT_HOME=str(self.home))
        subprocess.Popen(
            [sys.executable, "-m", "safelight.cli", "hub", "--host", "relay"],
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
        app_tried = spawned = False
        spawn_at: float | None = None  # when to stop waiting for the app
        while True:
            state, status = self._probe()
            if state == "ok" and status is not None:
                return status
            if state == "down" and not start:
                raise HubUnavailable("the Safelight hub is not running")
            if state == "down" and not app_tried:
                app_tried = True
                if self._open_app():
                    spawn_at = time.monotonic() + APP_WAIT_S
            if (
                state == "down"
                and not spawned
                and (spawn_at is None or time.monotonic() >= spawn_at)
            ):
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

    async def status(
        self, *, start: bool = True, timeout: float = START_TIMEOUT_S
    ) -> dict[str, Any]:
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

    async def approval(self, approval_id: str, wait_s: float = 0.0) -> dict[str, Any]:
        """The app's answer to a confirmation: pending, approved, declined, withdrawn."""
        query = urllib.parse.urlencode({"wait_s": wait_s})
        path = f"/v1/approvals/{urllib.parse.quote(approval_id)}?{query}"
        return await self._call_hub("GET", path, timeout=wait_s + 10.0)

    async def decide(self, approval_id: str, decision: str) -> dict[str, Any]:
        path = f"/v1/approvals/{urllib.parse.quote(approval_id)}"
        return await self._call_hub("POST", path, {"decision": decision})

    async def review(
        self, add: list[dict[str, Any]] | None = None, done: list[str] | None = None
    ) -> dict[str, Any]:
        """Photos to check in the app: add some, mark some done, or list them."""
        if add or done:
            return await self._call_hub(
                "POST", "/v1/review", {"add": add or [], "done": done or []}
            )
        return await self._call_hub("GET", "/v1/review")

    async def settings(self, changes: dict[str, Any] | None = None) -> dict[str, Any]:
        if changes:
            return await self._call_hub("PUT", "/v1/settings", changes)
        return await self._call_hub("GET", "/v1/settings")
