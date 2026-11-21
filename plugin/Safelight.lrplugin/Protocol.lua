-- Constants shared with the hub. Keep in sync with src/safelight/protocol.py.
return {
    VERSION = 1,
    TOKEN_HEADER = "X-Safelight-Token",
    POLL_WAIT_S = 20,          -- how long the hub may hold a poll open
    POLL_HTTP_TIMEOUT_S = 35,  -- LrHttp timeout for the poll request (must exceed POLL_WAIT_S)
    RESULT_HTTP_TIMEOUT_S = 15,
    RETRY_MIN_S = 1,
    RETRY_MAX_S = 5,
}
