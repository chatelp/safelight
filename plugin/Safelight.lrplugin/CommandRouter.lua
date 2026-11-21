-- Maps command names received from the hub to handler functions.
-- The upstream catalog, develop, mask and export actions are wired back in roadmap lot L3,
-- once their bugs are fixed; until then only system and read-only inspection commands exist.
local LrTasks = import "LrTasks"

local Inspect = require "Inspect"
local Logger = require "Logger"
local Version = require "Version"

local CommandRouter = {
    handlers = {},
}

local function register(name, handler)
    CommandRouter.handlers[name] = handler
end

register("system.ping", function(_)
    return {
        pong = true,
        plugin = "Safelight",
        version = Version.string,
    }
end)

register("system.list_commands", function(_)
    local out = {}
    for name, _ in pairs(CommandRouter.handlers) do
        out[#out + 1] = name
    end
    table.sort(out)
    return { commands = out }
end)

register("inspect.app", Inspect.app)
register("inspect.develop_settings", Inspect.developSettings)
register("inspect.raw_metadata", Inspect.rawMetadata)
register("inspect.thumbnails", Inspect.thumbnails)
register("inspect.develop_controller", Inspect.developController)

-- Runs one command and always returns a response table for the hub.
function CommandRouter.dispatch(request)
    local requestId = type(request) == "table" and request.id or nil
    local command = type(request) == "table" and request.command or nil
    if type(command) ~= "string" or command == "" then
        return {
            id = requestId,
            success = false,
            error = { code = "BAD_REQUEST", message = "command is required" },
        }
    end

    local handler = CommandRouter.handlers[command]
    if not handler then
        return {
            id = requestId,
            success = false,
            error = { code = "UNKNOWN_COMMAND", message = "Unknown command: " .. command },
        }
    end

    local ok, result = LrTasks.pcall(handler, request.params or {})
    if not ok then
        Logger.error("Command failed: " .. command .. " -> " .. tostring(result))
        return {
            id = requestId,
            success = false,
            error = { code = "COMMAND_FAILED", message = tostring(result) },
        }
    end

    return {
        id = requestId,
        success = true,
        result = result,
    }
end

return CommandRouter
