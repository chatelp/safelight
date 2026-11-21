"""Constants shared by the hub, the MCP relay and the Lightroom plugin.

Keep in sync with plugin/Safelight.lrplugin/Protocol.lua.
"""

from __future__ import annotations

# Bumped whenever the hub <-> plugin message format changes incompatibly.
PROTOCOL_VERSION = 1

DEFAULT_PORT = 58763
TOKEN_HEADER = "X-Safelight-Token"

# The hub holds a plugin poll open for at most this long before answering "no command".
MAX_POLL_WAIT_S = 25.0
# The plugin counts as connected if it polled within this many seconds after a poll ended.
CONNECTED_GRACE_S = 10.0

DEFAULT_COMMAND_TIMEOUT_S = 30.0
MAX_COMMAND_TIMEOUT_S = 600.0


class ErrorCode:
    LIGHTROOM_NOT_CONNECTED = "LIGHTROOM_NOT_CONNECTED"
    LIGHTROOM_DISCONNECTED = "LIGHTROOM_DISCONNECTED"
    NOT_DELIVERED = "NOT_DELIVERED"  # timed out before Lightroom picked it up: it did not run
    TIMEOUT = "TIMEOUT"  # Lightroom picked it up but did not answer in time: it may have run
    COMMAND_FAILED = "COMMAND_FAILED"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    HUB_UNAVAILABLE = "HUB_UNAVAILABLE"
    BAD_REQUEST = "BAD_REQUEST"
