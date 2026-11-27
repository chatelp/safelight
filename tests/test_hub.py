import asyncio
import contextlib
import json
import urllib.error
import urllib.request

import pytest

from safelight.devtools.fake_plugin import FakePlugin
from safelight.hub import Hub, HubServer
from safelight.hub_client import HubClient
from safelight.policy import CommandPolicy
from safelight.protocol import TOKEN_HEADER, ErrorCode, JobState
from safelight.settings import Settings

pytestmark = pytest.mark.anyio


# -- queue, jobs and presence, no HTTP ----------------------------------------------------


async def _connected(hub: Hub) -> None:
    """Make the hub believe the plugin polled a moment ago."""
    await hub.poll({}, wait_s=0)


async def test_run_fails_fast_when_lightroom_is_not_connected():
    hub = Hub()
    result = await asyncio.wait_for(hub.run("system.ping"), 1)
    assert result["state"] == JobState.FAILED
    assert result["error"]["code"] == ErrorCode.LIGHTROOM_NOT_CONNECTED


async def test_round_trip_through_poll():
    hub = Hub()
    poll = asyncio.create_task(hub.poll({"plugin_version": "t"}, wait_s=2))
    await asyncio.sleep(0.05)
    call = asyncio.create_task(hub.run("system.ping", {"x": 1}))
    reply = await asyncio.wait_for(poll, 1)
    command = reply["commands"][0]
    assert command["command"] == "system.ping"
    assert command["params"] == {"x": 1}
    hub.deliver_result({"id": command["id"], "success": True, "result": {"pong": True}})
    result = await asyncio.wait_for(call, 1)
    assert result["state"] == JobState.SUCCEEDED
    assert result["result"] == {"pong": True}


async def test_undelivered_command_is_cancelled_and_reported_as_not_run():
    hub = Hub(delivery_timeout_s=0.2)
    await _connected(hub)
    result = await hub.run("system.ping", wait_s=5)
    assert result["state"] == JobState.CANCELLED
    assert result["error"]["code"] == ErrorCode.NOT_DELIVERED
    assert (await hub.poll({}, wait_s=0))["commands"] == []


async def test_slow_command_returns_running_job_then_result():
    hub = Hub()
    poll = asyncio.create_task(hub.poll({}, wait_s=2))
    await asyncio.sleep(0.05)
    first = await hub.run("system.slow", wait_s=0.2)
    assert first["state"] == JobState.RUNNING
    command = (await poll)["commands"][0]
    assert hub.report_progress({"id": command["id"], "done": 1, "total": 3}) == {
        "accepted": True,
        "cancel": False,
    }
    assert hub.get_job(first["job_id"]).response()["progress"] == {"done": 1, "total": 3}
    hub.deliver_result({"id": command["id"], "success": True, "result": "ok"})
    final = await hub.wait(hub.get_job(first["job_id"]), 1)
    assert final["state"] == JobState.SUCCEEDED
    assert final["result"] == "ok"


async def test_cancel_running_job_is_passed_to_plugin_at_next_progress():
    hub = Hub()
    poll = asyncio.create_task(hub.poll({}, wait_s=2))
    await asyncio.sleep(0.05)
    started = await hub.run("system.slow", wait_s=0.1)
    command = (await poll)["commands"][0]
    assert hub.cancel_job(started["job_id"])["cancel_requested"] is True
    assert hub.report_progress({"id": command["id"], "done": 2, "total": 9})["cancel"] is True
    hub.deliver_result(
        {"id": command["id"], "success": False, "error": {"code": "CANCELLED", "message": "x"}}
    )
    assert hub.get_job(started["job_id"]).state == JobState.CANCELLED


async def test_cancel_queued_job_removes_it_from_the_queue():
    hub = Hub()
    await _connected(hub)
    job = hub.submit("system.ping", {}, client="t")
    assert hub.cancel_job(job.id)["state"] == JobState.CANCELLED
    assert (await hub.poll({}, wait_s=0))["commands"] == []


async def test_running_job_fails_when_plugin_disappears():
    hub = Hub(connected_grace_s=0.1)
    poll = asyncio.create_task(hub.poll({}, wait_s=1))
    await asyncio.sleep(0.05)
    started = await hub.run("system.slow", wait_s=0.1)
    await poll  # delivered, then the plugin goes silent
    await asyncio.sleep(0.2)
    hub.maintenance()
    job = hub.get_job(started["job_id"])
    assert job.state == JobState.FAILED
    assert job.error["code"] == ErrorCode.LIGHTROOM_DISCONNECTED


# -- policy: read-only mode and confirmations ------------------------------------------------

POLICIES = {
    "bulk": CommandPolicy(supports_dry_run=True),
    "remove": CommandPolicy(destructive=True),
}


async def _answer_next(hub: Hub, result):
    """Act as the plugin for one command."""
    reply = await hub.poll({}, wait_s=2)
    command = reply["commands"][0]
    hub.deliver_result({"id": command["id"], "success": True, "result": result})
    return command


async def test_read_only_mode_refuses_changes_but_allows_reads():
    hub = Hub(settings=Settings(read_only=True), policies=POLICIES)
    await _connected(hub)
    refused = await hub.run("bulk", {"count": 1})
    assert refused["error"]["code"] == ErrorCode.READ_ONLY_MODE
    reader = asyncio.create_task(_answer_next(hub, {"pong": True}))
    assert (await hub.run("system.ping"))["state"] == JobState.SUCCEEDED
    await reader


async def test_small_bulk_change_runs_after_a_dry_run():
    hub = Hub(settings=Settings(confirm_threshold=50), policies=POLICIES)
    await _connected(hub)

    async def plugin():
        dry = await _answer_next(hub, {"affected": 10})
        assert dry["params"]["dry_run"] is True
        real = await _answer_next(hub, {"edited": 10})
        assert "dry_run" not in real["params"]

    task = asyncio.create_task(plugin())
    result = await hub.run("bulk", {"count": 10})
    await task
    assert result["state"] == JobState.SUCCEEDED


async def test_large_bulk_change_needs_confirmation_with_matching_params():
    hub = Hub(settings=Settings(confirm_threshold=50), policies=POLICIES)
    await _connected(hub)
    dry = asyncio.create_task(_answer_next(hub, {"affected": 300, "summary": "Edit 300"}))
    gate = await hub.run("bulk", {"count": 300})
    await dry
    assert gate["state"] == JobState.CONFIRMATION_REQUIRED
    assert gate["confirmation"]["affected"] == 300

    # A token used with different parameters is refused (and used up).
    wrong = await hub.run("bulk", {"count": 301}, confirm=gate["confirmation"]["token"])
    assert wrong["error"]["code"] == ErrorCode.INVALID_CONFIRMATION

    dry = asyncio.create_task(_answer_next(hub, {"affected": 300}))
    token = (await hub.run("bulk", {"count": 300}))["confirmation"]["token"]
    await dry
    real = asyncio.create_task(_answer_next(hub, {"edited": 300}))
    confirmed = await hub.run("bulk", {"count": 300}, confirm=token)
    await real
    assert confirmed["state"] == JobState.SUCCEEDED
    # A token works once.
    again = await hub.run("bulk", {"count": 300}, confirm=token)
    assert again["error"]["code"] == ErrorCode.INVALID_CONFIRMATION


async def test_destructive_command_always_needs_confirmation():
    hub = Hub(settings=Settings(confirm_threshold=1000), policies=POLICIES)
    await _connected(hub)
    gate = await hub.run("remove", {"ids": [1]})
    assert gate["state"] == JobState.CONFIRMATION_REQUIRED
    assert "undone" in gate["confirmation"]["reason"]


async def test_activity_log_and_clients():
    hub = Hub(policies=POLICIES)
    await _connected(hub)
    reader = asyncio.create_task(_answer_next(hub, {"pong": True}))
    await hub.run("system.ping", client="Claude Desktop")
    await reader
    await hub.run("remove", {"ids": [1]}, client="Claude Code")
    events = hub.activity.since(0)["events"]
    assert [(e["kind"], e["client"]) for e in events] == [
        ("job", "Claude Desktop"),
        ("confirmation_required", "Claude Code"),
    ]
    assert {c["name"] for c in hub.status()["clients"]} == {"Claude Desktop", "Claude Code"}
    assert hub.activity.since(events[0]["seq"])["events"] == events[1:]


def test_settings_validation_and_persistence(tmp_path):
    path = tmp_path / "settings.json"
    hub = Hub(settings_path=path)
    lens = [{"lightroom": "DT 17mm F4 SAM", "real": "Canon TS-E 17mm f/4L"}]
    changes = {
        "confirm_threshold": 20,
        "read_only": True,
        "change_label": "none",
        "snapshots": False,
        "lens_names": lens,
        "by_hand_label": "red",
        "validated_label": "green",
    }
    saved = hub.update_settings({**changes, "collections": {"to_check": "À vérifier"}})
    assert {key: saved[key] for key in changes} == changes
    assert saved["collections"]["to_check"] == "À vérifier"
    assert saved["collections"]["set"] == "Safelight"  # a partial change keeps the others
    loaded = Settings.load(path)
    assert loaded.confirm_threshold == 20 and loaded.lens_names == lens
    assert loaded.real_lens("DT 17mm F4 SAM") == "Canon TS-E 17mm f/4L"
    assert loaded.lightroom_lens("TS-E 17") == "DT 17mm F4 SAM"
    assert loaded.lightroom_lens("FE 24-70mm F4 ZA OSS") == "FE 24-70mm F4 ZA OSS"
    # A Lightroom name that is also part of another lens's real name is left alone.
    tricky = Settings(lens_names=[{"lightroom": "X", "real": "Canon TS-E 17mm"}, *lens])
    assert tricky.lightroom_lens("TS-E 17") == "TS-E 17"  # matches two real names: unchanged
    clash = Settings(lens_names=[{"lightroom": "17mm", "real": "Some 17mm lens"}])
    assert clash.lightroom_lens("17mm") == "17mm"
    for bad in (
        {"confirm_threshold": -1},
        {"read_only": "yes"},
        {"unknown": 1},
        {"change_label": "orange"},
        {"by_hand_label": "pink"},
        {"collections": {"set": ""}},
        {"collections": {"other": "X"}},
        {"collections": {"to_check": "x" * 61}},
        {"snapshots": 1},
        {"lens_names": [{"lightroom": "A"}]},
        {"lens_names": [{"lightroom": "A", "real": "B"}, {"lightroom": "A", "real": "C"}]},
        {"lens_names": [{"lightroom": "", "real": "B"}]},
        {"read_only": False, "change_label": "orange"},  # nothing changes
    ):
        with pytest.raises(ValueError):
            hub.update_settings(bad)
    assert hub.settings.read_only is True


def test_settings_defaults_and_old_files(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"confirm_threshold": 7, "change_label": "orange"}))
    settings = Settings.load(path)  # an old or hand-edited file: bad values are skipped
    assert settings.confirm_threshold == 7
    assert settings.change_label == "purple" and settings.snapshots is True


# -- over HTTP, with the fake plugin ----------------------------------------------------------


async def _start_server(home, hub=None):
    server = HubServer(home, port=0, hub=hub)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.port and (home / "hub.json").exists():
            return server, task
        await asyncio.sleep(0.02)
    raise AssertionError("hub did not start")


async def _wait_connected(client: HubClient) -> None:
    for _ in range(200):
        if (await client.status(start=False))["plugin"]["connected"]:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("fake plugin did not connect")


@contextlib.asynccontextmanager
async def running_hub(home, hub=None):
    server, task = await _start_server(home, hub)
    plugin = FakePlugin(server.port, server.token)
    plugin_task = asyncio.create_task(plugin.run())
    client = HubClient(home, client_name="tests")
    try:
        await _wait_connected(client)
        yield server, client
    finally:
        plugin.stop()
        server.stop()
        await asyncio.wait_for(task, 5)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(plugin_task, 5)


async def test_http_round_trip_with_fake_plugin(safelight_home):
    async with running_hub(safelight_home) as (_, client):
        result = await client.run("echo", {"word": "é📷"})
        assert result["state"] == JobState.SUCCEEDED
        assert result["result"] == {"word": "é📷"}
        unknown = await client.run("system.nothing")
        assert unknown["state"] == JobState.FAILED
        assert unknown["error"]["code"] == ErrorCode.UNKNOWN_COMMAND
    assert not (safelight_home / "hub.json").exists()


async def test_http_long_job_progress_and_cancel(safelight_home):
    hub = Hub(settings=Settings(confirm_threshold=1000))
    async with running_hub(safelight_home, hub) as (_, client):
        started = await client.run("fake.bulk_edit", {"count": 40, "delay_s": 0.05}, wait_s=0.3)
        assert started["state"] == JobState.RUNNING
        job_id = started["job_id"]
        progress = (await client.job(job_id))["progress"]
        assert progress["total"] == 40
        assert 0 < progress["done"] < 40
        await client.cancel(job_id)
        final = await client.job(job_id, wait_s=5)
        assert final["state"] == JobState.CANCELLED
        assert final["error"]["code"] == ErrorCode.CANCELLED


async def test_http_confirmation_flow(safelight_home):
    hub = Hub(settings=Settings(confirm_threshold=5))
    async with running_hub(safelight_home, hub) as (_, client):
        gate = await client.run("fake.bulk_edit", {"count": 12})
        assert gate["state"] == JobState.CONFIRMATION_REQUIRED
        assert gate["confirmation"]["affected"] == 12
        done = await client.run(
            "fake.bulk_edit", {"count": 12}, confirm=gate["confirmation"]["token"]
        )
        assert done["state"] == JobState.SUCCEEDED
        assert done["result"] == {"edited": 12}
        events = (await client.activity())["events"]
        assert [e["kind"] for e in events][-2:] == ["confirmation_required", "job"]
        assert events[-1]["client"] == "tests"


async def test_http_settings(safelight_home):
    async with running_hub(safelight_home) as (_, client):
        assert (await client.settings({"read_only": True}))["read_only"] is True
        refused = await client.run("fake.bulk_edit", {"count": 1})
        assert refused["error"]["code"] == ErrorCode.READ_ONLY_MODE
    assert json.loads((safelight_home / "settings.json").read_text())["read_only"] is True


def _raw_request(port, headers):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/status", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


async def test_http_rejects_missing_token_and_browser_requests(safelight_home):
    server, task = await _start_server(safelight_home)
    try:
        assert await asyncio.to_thread(_raw_request, server.port, {}) == 401
        browser = {TOKEN_HEADER: server.token, "Origin": "https://evil.example"}
        assert await asyncio.to_thread(_raw_request, server.port, browser) == 403
        good = {TOKEN_HEADER: server.token}
        assert await asyncio.to_thread(_raw_request, server.port, good) == 200
    finally:
        server.stop()
        await asyncio.wait_for(task, 5)


async def test_hub_stops_quickly_while_the_plugin_holds_a_poll(safelight_home):
    server, task = await _start_server(safelight_home)
    plugin = FakePlugin(server.port, server.token, wait_s=20)
    plugin_task = asyncio.create_task(plugin.run())
    await asyncio.sleep(0.3)
    started = asyncio.get_running_loop().time()
    server.stop()
    await asyncio.wait_for(task, 5)
    assert asyncio.get_running_loop().time() - started < 2
    plugin.stop()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(plugin_task, 5)


# -- who starts the hub: the app first, the relay alone as a fallback ---------------------


class _FakeStart(HubClient):
    """HubClient with a simulated hub and app; `events` records who was asked to start it."""

    def __init__(self, home, app_installed, app_brings_hub_after=None):
        super().__init__(home)
        self.app_installed = app_installed
        self.app_brings_hub_after = app_brings_hub_after
        self.events = []
        self.probes = 0
        self.hub_up_at = None

    def _probe(self):
        self.probes += 1
        if self.hub_up_at is not None and self.probes >= self.hub_up_at:
            return "ok", {"hub": {"host": self.events[-1]}}
        return "down", None

    def _open_app(self):
        self.events.append("app")
        if self.app_installed and self.app_brings_hub_after is not None:
            self.hub_up_at = self.probes + self.app_brings_hub_after
        return self.app_installed

    def _spawn_hub(self):
        self.events.append("relay")
        self.hub_up_at = self.probes + 2


def test_relay_opens_the_app_and_waits_for_its_hub(tmp_path):
    client = _FakeStart(tmp_path, app_installed=True, app_brings_hub_after=5)
    status = client._ensure_running_sync(start=True, timeout=5)
    assert client.events == ["app"]
    assert status["hub"]["host"] == "app"


def test_relay_starts_the_hub_alone_without_the_app(tmp_path):
    client = _FakeStart(tmp_path, app_installed=False)
    status = client._ensure_running_sync(start=True, timeout=5)
    assert client.events == ["app", "relay"]
    assert status["hub"]["host"] == "relay"


def test_relay_starts_the_hub_alone_when_the_app_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr("safelight.hub_client.APP_WAIT_S", 0.3)
    client = _FakeStart(tmp_path, app_installed=True)  # opens, but no hub comes
    status = client._ensure_running_sync(start=True, timeout=5)
    assert client.events == ["app", "relay"]
    assert status["hub"]["host"] == "relay"


def test_app_is_opened_only_for_the_real_home(tmp_path, monkeypatch):
    monkeypatch.setattr("safelight.hub_client.sys.platform", "darwin")
    monkeypatch.setattr("safelight.hub_client.default_home", lambda: tmp_path)
    assert HubClient(tmp_path)._app_allowed()
    assert not HubClient(tmp_path / "other")._app_allowed()
    monkeypatch.setenv("SAFELIGHT_OPEN_APP", "0")
    assert not HubClient(tmp_path)._app_allowed()


async def test_status_says_who_started_the_hub(safelight_home):
    server = HubServer(safelight_home, port=0, host="app")
    task = asyncio.create_task(server.serve())
    try:
        client = HubClient(safelight_home)
        for _ in range(200):
            if (safelight_home / "hub.json").exists():
                break
            await asyncio.sleep(0.02)
        assert (await client.status(start=False))["hub"]["host"] == "app"
    finally:
        server.stop()
        await asyncio.wait_for(task, 5)


def test_hub_stops_when_its_app_is_gone(safelight_home):
    """A hub started by the app with --parent-pid stops if its parent changes (app crash)."""
    import subprocess
    import sys

    # A parent pid that is not ours: the hub sees a different parent and stops by itself.
    process = subprocess.Popen(
        [sys.executable, "-m", "safelight.cli", "hub", "--host", "app", "--parent-pid", "1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
    assert not (safelight_home / "hub.json").exists()


# -- approvals in the app -----------------------------------------------------------------


async def test_the_app_approves_or_declines_a_confirmation():
    hub = Hub(settings=Settings(), policies=POLICIES)
    await _connected(hub)
    asked = await hub.run("remove", {"count": 3}, client="claude-ai")
    confirmation = asked["confirmation"]
    approval_id = confirmation["approval_id"]
    assert hub.status()["approvals"] == []  # nobody waits for the app's answer yet

    waiting = asyncio.create_task(hub.wait_approval(approval_id, 5))
    await asyncio.sleep(0.05)
    (shown,) = hub.status()["approvals"]
    assert shown["id"] == approval_id and shown["client"] == "claude-ai"
    assert "token" not in shown  # the app answers by id; only the relay holds the token
    assert hub.decide(approval_id, "approved") == {"state": "approved"}
    assert (await asyncio.wait_for(waiting, 1)) == {"state": "approved"}
    assert hub.status()["approvals"] == []
    assert hub.activity.since(0)["events"][-1]["state"] == "approved"

    # Declined: the token is no longer accepted.
    asked = await hub.run("remove", {"count": 3}, client="claude-ai")
    hub.decide(asked["confirmation"]["approval_id"], "declined")
    refused = await hub.run("remove", {"count": 3}, confirm=asked["confirmation"]["token"])
    assert refused["error"]["code"] == ErrorCode.INVALID_CONFIRMATION

    # Withdrawn (asked in the AI client instead): hidden from the app, token still good.
    asked = await hub.run("remove", {"count": 3}, client="claude-ai")
    assert hub.decide(asked["confirmation"]["approval_id"], "withdrawn") == {"state": "withdrawn"}
    assert hub.status()["approvals"] == []
    hub.decide(asked["confirmation"]["approval_id"], "approved")  # too late: stays withdrawn
    assert await hub.wait_approval(asked["confirmation"]["approval_id"], 0) == {
        "state": "withdrawn"
    }
    assert hub.decide("nope", "approved") == {"state": "unknown"}


async def test_the_app_is_seen_through_its_status_polls(safelight_home):
    from safelight.protocol import APP_CLIENT

    async with running_hub(safelight_home) as (_, client):
        assert (await client.status(start=False))["app"]["connected"] is False
        app = HubClient(safelight_home, client_name=APP_CLIENT)
        await app.status(start=False)
        assert (await client.status(start=False))["app"]["connected"] is True


# -- photos to check ----------------------------------------------------------------------


def test_photos_to_check_are_kept_newest_first_and_saved(tmp_path):
    path = tmp_path / "review.json"
    hub = Hub(review_path=path)
    hub.add_review([{"id": "A", "file": "a.dng", "reason": "tilted"}], "claude-ai")
    hub.add_review([{"id": "B", "reason": "check"}, {"id": "A", "reason": "again"}, {}], "x")
    assert [(e["id"], e["reason"]) for e in hub.status()["review"]] == [
        ("B", "check"),
        ("A", "again"),
    ]
    assert Hub(review_path=path).review == hub.review  # survives a restart
    assert hub.review_done(["A", "nope"]) == 1
    assert [e["id"] for e in Hub(review_path=path).review] == ["B"]
    assert hub.status()["review_count"] == 1


def test_a_damaged_review_file_does_not_break_the_list(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps(
            [
                {"id": ["x"]},
                {"id": 3, "reason": "number id"},
                {"id": "A", "file": {"bad": 1}},
                {"id": "A", "reason": "second A"},
                "junk",
                {"id": "B", "reason": "ok", "client": "claude-ai"},
            ]
        )
    )
    hub = Hub(review_path=path)
    assert [(e["id"], e["file"], e["reason"]) for e in hub.review] == [
        ("A", None, ""),
        ("B", None, "ok"),
    ]
    hub.add_review([{"id": "C", "reason": "1"}, {"id": "C", "reason": "2"}], "x")
    assert [(e["id"], e["reason"]) for e in hub.review][:2] == [("C", "2"), ("A", "")]
    assert hub.review_done([["x"], "A"]) == 1
