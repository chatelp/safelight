-- Finds the Safelight hub (from hub.json) and sends it JSON requests with LrHttp.
-- Every function here must run inside LrFunctionContext.postAsyncTaskWithContext.
local LrFileUtils = import "LrFileUtils"
local LrHttp = import "LrHttp"
local LrPathUtils = import "LrPathUtils"

local JSON = require "JSON"
local Protocol = require "Protocol"

local HubConnection = {}

local configPath = LrPathUtils.child(
    LrPathUtils.child(
        LrPathUtils.child(LrPathUtils.getStandardFilePath("home"), "Library"),
        "Application Support"
    ),
    "Safelight"
)
configPath = LrPathUtils.child(configPath, "hub.json")

function HubConnection.configPath()
    return configPath
end

-- Returns { port = number, token = string } or nil when the hub is not running.
function HubConnection.readConfig()
    if not LrFileUtils.exists(configPath) then
        return nil
    end
    local file = io.open(configPath, "r")
    if not file then
        return nil
    end
    local text = file:read("*a")
    file:close()
    local ok, config = pcall(JSON.decode, text)
    if not ok or type(config) ~= "table" or not config.port or not config.token then
        return nil
    end
    return { port = tonumber(config.port), token = tostring(config.token) }
end

-- POSTs `payload` (a Lua table) to `path`. Returns (decodedBody, httpStatus) on an HTTP answer,
-- or (nil, nil, errorMessage) when the hub could not be reached.
function HubConnection.post(config, path, payload, timeout)
    local okEncode, body = pcall(JSON.encode, payload)
    if not okEncode then
        return nil, nil, "could not encode request: " .. tostring(body)
    end
    local headers = {
        { field = Protocol.TOKEN_HEADER, value = config.token },
        { field = "Content-Type", value = "application/json" },
    }
    local url = "http://127.0.0.1:" .. tostring(config.port) .. path
    local responseBody, responseHeaders = LrHttp.post(url, body, headers, "POST", timeout)
    local status = responseHeaders and responseHeaders.status
    if not status then
        local reason = "no answer"
        if responseHeaders and responseHeaders.error then
            reason = tostring(responseHeaders.error.name or responseHeaders.error.errorCode or reason)
        end
        return nil, nil, reason
    end
    local decoded = nil
    if responseBody and responseBody ~= "" then
        local okDecode, value = pcall(JSON.decode, responseBody)
        if okDecode then
            decoded = value
        end
    end
    return decoded, status
end

return HubConnection
