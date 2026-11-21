"""Constants shared by the hub, the MCP relay and the Lightroom plugin.

Keep in sync with plugin/Safelight.lrplugin/Protocol.lua.
"""

from __future__ import annotations

# Bumped whenever the hub <-> plugin message format changes incompatibly.
PROTOCOL_VERSION = 1

DEFAULT_PORT = 58763
TOKEN_HEADER = "X-Safelight-Token"
CLIENT_HEADER = "X-Safelight-Client"

# The hub holds a plugin poll open for at most this long before answering "no command".
MAX_POLL_WAIT_S = 25.0
# The plugin counts as connected if it polled within this many seconds after a poll ended.
CONNECTED_GRACE_S = 10.0

# How long an RPC waits for a result before answering "still running" with a job id.
DEFAULT_WAIT_S = 30.0
MAX_WAIT_S = 120.0
# A queued command not picked up by Lightroom within this time is cancelled (it did not run).
DELIVERY_TIMEOUT_S = 10.0
# Finished jobs are kept this long so clients can still read their result.
JOB_RETENTION_S = 3600.0
MAX_RETAINED_JOBS = 500
CONFIRMATION_TTL_S = 600.0
ACTIVITY_LOG_SIZE = 500
CLIENT_SEEN_WINDOW_S = 900.0


class JobState:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONFIRMATION_REQUIRED = "confirmation_required"

    TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})


class ErrorCode:
    LIGHTROOM_NOT_CONNECTED = "LIGHTROOM_NOT_CONNECTED"
    LIGHTROOM_DISCONNECTED = "LIGHTROOM_DISCONNECTED"
    NOT_DELIVERED = "NOT_DELIVERED"  # timed out before Lightroom picked it up: it did not run
    COMMAND_FAILED = "COMMAND_FAILED"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    HUB_UNAVAILABLE = "HUB_UNAVAILABLE"
    BAD_REQUEST = "BAD_REQUEST"
    READ_ONLY_MODE = "READ_ONLY_MODE"
    INVALID_CONFIRMATION = "INVALID_CONFIRMATION"
    CANCELLED = "CANCELLED"
    UNKNOWN_JOB = "UNKNOWN_JOB"
