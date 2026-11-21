-- Keeps Lightroom connected to the Safelight hub: long-polls for commands, runs each one in
-- its own task and posts the answer back. Survives the hub starting, stopping or restarting.
local LrApplication = import "LrApplication"
local LrFunctionContext = import "LrFunctionContext"
local LrTasks = import "LrTasks"

local CommandRouter = require "CommandRouter"
local HubConnection = require "HubConnection"
local Logger = require "Logger"
local Protocol = require "Protocol"
local Version = require "Version"

local Transport = {}

-- Kept in _G so that a loop started before a plug-in reload can see that it is outdated.
_G.SafelightTransportState = _G.SafelightTransportState or {
    generation = 0,
    connected = false,
    lastError = nil,
    commandsRun = 0,
}
local state = _G.SafelightTransportState

local function catalogPath()
    local ok, path = pcall(function()
        return LrApplication.activeCatalog():getPath()
    end)
    if ok then
        return path
    end
    return nil
end

local function sessionInfo()
    return {
        protocol = Protocol.VERSION,
        plugin_version = Version.string,
        lightroom_version = LrApplication.versionString(),
        catalog = catalogPath(),
        wait_s = Protocol.POLL_WAIT_S,
    }
end

local function setConnected(connected, reason)
    if connected ~= state.connected then
        if connected then
            Logger.info("Connected to the Safelight hub")
        else
            Logger.info("Not connected to the Safelight hub: " .. tostring(reason))
        end
    end
    state.connected = connected
    state.lastError = reason
end

local function runCommand(config, command)
    LrFunctionContext.postAsyncTaskWithContext("SafelightCommand", function()
        local response = CommandRouter.dispatch(command)
        state.commandsRun = state.commandsRun + 1
        local _, status, err = HubConnection.post(
            config, "/v1/plugin/result", response, Protocol.RESULT_HTTP_TIMEOUT_S
        )
        if status ~= 200 then
            Logger.warn("Could not deliver result of " .. tostring(command.command) .. ": "
                .. tostring(err or status))
        end
    end)
end

local function pollLoop(myGeneration)
    local retryDelay = Protocol.RETRY_MIN_S
    while state.generation == myGeneration do
        local config = HubConnection.readConfig()
        if not config then
            setConnected(false, "hub not running")
            LrTasks.sleep(retryDelay)
            retryDelay = math.min(retryDelay + 1, Protocol.RETRY_MAX_S)
        else
            local reply, status, err = HubConnection.post(
                config, "/v1/plugin/poll", sessionInfo(), Protocol.POLL_HTTP_TIMEOUT_S
            )
            if state.generation ~= myGeneration then
                break
            end
            if status == 200 and type(reply) == "table" then
                setConnected(true)
                retryDelay = Protocol.RETRY_MIN_S
                for _, command in ipairs(reply.commands or {}) do
                    runCommand(config, command)
                end
            else
                local reason = err or ("HTTP " .. tostring(status))
                if status == 409 and type(reply) == "table" and reply.error then
                    reason = tostring(reply.error.message)
                end
                setConnected(false, reason)
                LrTasks.sleep(retryDelay)
                retryDelay = math.min(retryDelay + 1, Protocol.RETRY_MAX_S)
            end
        end
    end
    Logger.info("Safelight connection loop stopped")
end

-- Starts a new loop. Any loop from an earlier start (for example before a plug-in reload)
-- notices the generation change and stops on its own.
function Transport.start()
    state.generation = state.generation + 1
    local myGeneration = state.generation
    LrFunctionContext.postAsyncTaskWithContext("SafelightTransport", function()
        pollLoop(myGeneration)
    end)
end

function Transport.stop()
    state.generation = state.generation + 1
    state.connected = false
end

function Transport.status()
    return {
        connected = state.connected,
        last_error = state.lastError,
        commands_run = state.commandsRun,
        hub_config = HubConnection.configPath(),
    }
end

return Transport
