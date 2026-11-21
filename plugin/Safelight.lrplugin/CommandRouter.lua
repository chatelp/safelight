-- Maps command names received from the hub to handler functions.
-- Commands are named <area>.<action>. Commands that change photos accept dry_run = true,
-- which only reports how many photos would change; the hub uses it before asking the
-- user to confirm large changes (see src/safelight/policy.py).
local LrTasks = import "LrTasks"

local Catalog = require "Catalog"
local Develop = require "Develop"
local Export = require "Export"
local Inspect = require "Inspect"
local Masks = require "Masks"
local Metadata = require "Metadata"
local Logger = require "Logger"
local Result = require "Result"
local Version = require "Version"

local CommandRouter = {
    handlers = {},
}


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
            return Result.cancelled("Stopped before step " .. step .. " of " .. steps)
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
register("inspect.develop_ranges", Develop.captureRanges)

-- Catalog and photos.
register("catalog.info", Catalog.info)
register("photos.get", Catalog.getPhotos)
register("photos.search", Catalog.search)
register("photos.preview", Catalog.preview)
register("photos.rotate", Catalog.rotate)
register("photos.create_virtual_copies", Catalog.createVirtualCopies)

-- Collections.
register("collections.list", Catalog.listCollections)
register("collections.photos", Catalog.collectionPhotos)
register("collections.create", Catalog.createCollection)
register("collections.add", Catalog.addToCollection)
register("collections.remove_photos", Catalog.removeFromCollection)
register("collections.delete", Catalog.deleteCollection)

-- Metadata and keywords.
register("metadata.set", Metadata.set)
register("keywords.list", Metadata.listKeywords)
register("keywords.add", Metadata.addKeywords)
register("keywords.remove", Metadata.removeKeywords)

-- Develop.
register("develop.get", Develop.get)
register("develop.apply", Develop.apply)
register("develop.copy", Develop.copy)
register("develop.auto_tone", Develop.autoTone)
register("develop.auto_white_balance", Develop.autoWhiteBalance)
register("develop.reset", Develop.reset)
register("develop.apply_preset", Develop.applyPreset)
register("presets.list", Develop.listPresets)
register("snapshots.list", Develop.listSnapshots)
register("snapshots.create", Develop.createSnapshot)
register("snapshots.apply", Develop.applySnapshot)
register("snapshots.delete", Develop.deleteSnapshot)

-- Masks.
register("masks.list", Masks.list)
register("masks.adjust", Masks.adjust)
register("masks.create", Masks.create)
register("masks.delete", Masks.delete)
register("masks.invert", Masks.invert)

-- Export.
register("export.photos", Export.photos)
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

    if Result.isCancelled(result) then
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
