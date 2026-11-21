import asyncio
import contextlib
import json
import urllib.error
import urllib.request

import pytest

from safelight.hub import Hub, HubServer
from safelight.hub_client import HubClient
from safelight.protocol import TOKEN_HEADER, ErrorCode

from .fake_plugin import FakePlugin

pytestmark = pytest.mark.anyio


# -- queue and presence logic, no HTTP ---------------------------------------------------


async def test_call_fails_fast_when_lightroom_is_not_connected():
    hub = Hub()
    result = await asyncio.wait_for(hub.call("system.ping"), 1)
    assert result["error"]["code"] == ErrorCode.LIGHTROOM_NOT_CONNECTED


async def test_round_trip_through_poll():
    hub = Hub()
    poll = asyncio.create_task(hub.poll({"plugin_version": "t"}, wait_s=2))
    await asyncio.sleep(0.05)
    call = asyncio.create_task(hub.call("system.ping", {"x": 1}))
    reply = await asyncio.wait_for(poll, 1)
    assert [c["command"] for c in reply["commands"]] == ["system.ping"]
    command = reply["commands"][0]
    assert command["params"] == {"x": 1}
    hub.deliver_result({"id": command["id"], "success": True, "result": {"pong": True}})
    assert await asyncio.wait_for(call, 1) == {"success": True, "result": {"pong": True}}


async def test_undelivered_command_is_dropped_and_reported_as_not_run():
    hub = Hub()
    await hub.poll({}, wait_s=0)  # plugin seen recently, but not polling right now
    result = await hub.call("develop.apply", timeout_s=0.2)
    assert result["error"]["code"] == ErrorCode.NOT_DELIVERED
    # A later poll must not receive the expired command.
    reply = await hub.poll({}, wait_s=0)
    assert reply["commands"] == []


async def test_delivered_command_without_answer_reports_timeout():
    hub = Hub()
    poll = asyncio.create_task(hub.poll({}, wait_s=2))
    await asyncio.sleep(0.05)
    result = await hub.call("export.photos", timeout_s=0.3)
    await poll
    assert result["error"]["code"] == ErrorCode.TIMEOUT


async def test_in_flight_command_fails_when_plugin_disappears():
    hub = Hub(connected_grace_s=0.1)
    poll = asyncio.create_task(hub.poll({}, wait_s=1))
    await asyncio.sleep(0.05)
    call = asyncio.create_task(hub.call("slow.command", timeout_s=5))
    await poll  # the command is delivered, then the plugin goes silent
    await asyncio.sleep(0.2)
    hub.fail_in_flight_if_disconnected()
    result = await asyncio.wait_for(call, 1)
    assert result["error"]["code"] == ErrorCode.LIGHTROOM_DISCONNECTED


# -- over HTTP, with a fake plugin -------------------------------------------------------


async def _start_server(home):
    server = HubServer(home, port=0)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.port and (home / "hub.json").exists():
            return server, task
        await asyncio.sleep(0.02)
    raise AssertionError("hub did not start")


async def test_http_round_trip_with_fake_plugin(safelight_home):
    server, task = await _start_server(safelight_home)
    config = json.loads((safelight_home / "hub.json").read_text())
    assert config["port"] == server.port
    assert (safelight_home / "hub.json").stat().st_mode & 0o077 == 0  # owner only

    client = HubClient(safelight_home)
    assert (await client.call("system.ping"))["error"]["code"] == ErrorCode.LIGHTROOM_NOT_CONNECTED

    plugin = FakePlugin(server.port, server.token, {"echo": lambda params: params})
    plugin_task = asyncio.create_task(plugin.run())
    try:
        for _ in range(100):
            if (await client.status(start=False))["plugin"]["connected"]:
                break
            await asyncio.sleep(0.02)
        assert await client.call("echo", {"word": "é📷"}) == {
            "success": True,
            "result": {"word": "é📷"},
        }
        unknown = await client.call("nope")
        assert unknown["error"]["code"] == "UNKNOWN_COMMAND"
    finally:
        plugin.stop()
        await asyncio.wait_for(plugin_task, 5)
        server.stop()
        await asyncio.wait_for(task, 5)
    assert not (safelight_home / "hub.json").exists()


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
        assert (
            await asyncio.to_thread(
                _raw_request,
                server.port,
                {TOKEN_HEADER: server.token, "Origin": "https://evil.example"},
            )
            == 403
        )
        assert (
            await asyncio.to_thread(_raw_request, server.port, {TOKEN_HEADER: server.token}) == 200
        )
    finally:
        server.stop()
        await asyncio.wait_for(task, 5)


async def test_hub_stops_quickly_while_the_plugin_holds_a_poll(safelight_home):
    server, task = await _start_server(safelight_home)
    plugin = FakePlugin(server.port, server.token)
    plugin.wait_s = 20  # a real plugin holds each poll open for 20 s
    plugin_task = asyncio.create_task(plugin.run())
    await asyncio.sleep(0.3)
    started = asyncio.get_running_loop().time()
    server.stop()
    await asyncio.wait_for(task, 5)
    assert asyncio.get_running_loop().time() - started < 2
    plugin.stop()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(plugin_task, 5)
