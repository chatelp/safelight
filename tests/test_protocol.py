"""The Lua plugin and the Python hub must agree on the protocol constants."""

import re
from pathlib import Path

from safelight.protocol import MAX_POLL_WAIT_S, PROTOCOL_VERSION, TOKEN_HEADER

PROTOCOL_LUA = (
    Path(__file__).resolve().parents[1] / "plugin" / "Safelight.lrplugin" / "Protocol.lua"
)


def _lua_value(name: str) -> str:
    match = re.search(rf"\b{name}\s*=\s*([^,\n]+)", PROTOCOL_LUA.read_text(encoding="utf-8"))
    assert match, f"{name} missing from Protocol.lua"
    return match.group(1).strip().strip('"')


def test_protocol_version_matches():
    assert int(_lua_value("VERSION")) == PROTOCOL_VERSION


def test_token_header_matches():
    assert _lua_value("TOKEN_HEADER") == TOKEN_HEADER


def test_poll_timeouts_are_consistent():
    wait = float(_lua_value("POLL_WAIT_S"))
    assert wait <= MAX_POLL_WAIT_S
    assert float(_lua_value("POLL_HTTP_TIMEOUT_S")) > wait
