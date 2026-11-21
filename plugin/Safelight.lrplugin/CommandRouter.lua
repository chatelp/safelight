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

-- A handler that stops because the user cancelled returns CommandRouter.cancelled(message).
function CommandRouter.cancelled(message)
    return { __safelightCancelled = true, message = message or "Cancelled" }
end

-- Handlers called without a hub job (for example from a menu) get a job that never cancels.
local noJob = {
    progress = function()
        return false
    end,
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

-- Harmless long command used to check progress reports and cancellation end to end.
register("system.progress_test", function(params, job)
    local steps = math.max(1, math.min(tonumber(params.steps) or 5, 100))
    local delay = math.max(0, math.min(tonumber(params.delay_s) or 0.5, 5))
    for step = 1, steps do
        if job.progress(step - 1, steps, "Step " .. step .. " of " .. steps) then
            return CommandRouter.cancelled("Stopped before step " .. step .. " of " .. steps)
        end
        LrTasks.sleep(delay)
    end
    job.progress(steps, steps, "Done")
    return { steps = steps }
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
function CommandRouter.dispatch(request, job)
    job = job or noJob
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

    local ok, result = LrTasks.pcall(handler, request.params or {}, job)
    if not ok then
        Logger.error("Command failed: " .. command .. " -> " .. tostring(result))
        return {
            id = requestId,
            success = false,
            error = { code = "COMMAND_FAILED", message = tostring(result) },
        }
    end

    if type(result) == "table" and result.__safelightCancelled then
        return {
            id = requestId,
            success = false,
            error = { code = "CANCELLED", message = tostring(result.message) },
        }
    end

    return {
        id = requestId,
        success = true,
        result = result,
    }
end

return CommandRouter
