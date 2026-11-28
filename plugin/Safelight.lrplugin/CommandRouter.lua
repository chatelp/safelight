-- Maps command names received from the hub to handler functions.
-- Commands are named <area>.<action>. Commands that change photos accept dry_run = true,
-- which only reports how many photos would change; the hub uses it before asking the
-- user to confirm large changes (see src/safelight/policy.py).
local LrFileUtils = import "LrFileUtils"
local LrPathUtils = import "LrPathUtils"
local LrTasks = import "LrTasks"

local Catalog = require "Catalog"
local Develop = require "Develop"
local Export = require "Export"
local Inspect = require "Inspect"
local JSON = require "JSON"
local Masks = require "Masks"
local Metadata = require "Metadata"
local Photos = require "Photos"
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
register("photos.show", Catalog.show)
register("photos.rotate", Catalog.rotate)
register("photos.create_virtual_copies", Catalog.createVirtualCopies)

-- Collections.
register("collections.list", Catalog.listCollections)
register("collections.photos", Catalog.collectionPhotos)
register("collections.create", Catalog.createCollection)
register("collections.add", Catalog.addToCollection)
register("collections.remove_photos", Catalog.removeFromCollection)
register("collections.delete", Catalog.deleteCollection)
register("safelight.collections", Catalog.safelightCollections)
register("safelight.photos", Catalog.safelightPhotos)

-- Metadata and keywords.
register("metadata.set", Metadata.set)
register("keywords.list", Metadata.listKeywords)
register("keywords.add", Metadata.addKeywords)
register("keywords.remove", Metadata.removeKeywords)
register("safelight.tidy", Metadata.tidy)

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

-- Commands that change a photo's edits. Photos the user validated (Safelight field
-- "Validated") are taken out of them and reported, whatever the AI client asked.
-- Keep in sync with EDITING_COMMANDS in src/safelight/policy.py (a test checks it).
local EDITING = {
    ["develop.apply"] = true,
    ["develop.copy"] = true,
    ["develop.auto_tone"] = true,
    ["develop.auto_white_balance"] = true,
    ["develop.reset"] = true,
    ["develop.apply_preset"] = true,
    ["snapshots.create"] = true,
    ["snapshots.apply"] = true,
    ["snapshots.delete"] = true,
    ["masks.adjust"] = true,
    ["masks.create"] = true,
    ["masks.invert"] = true,
    ["masks.delete"] = true,
    ["photos.rotate"] = true,
}
local VALIDATED = "VALIDATED: the user validated this photo, so Safelight does not change its edits"

-- Returns the params without validated photos and the validated ids, or nil when the
-- command is not an edit or no photo is validated.
local function withoutValidated(command, params)
    if not EDITING[command] then
        return nil
    end
    local photos, missing = Photos.resolve(params, { allowEmpty = true })
    local kept, validated = {}, {}
    for _, photo in ipairs(photos) do
        if Photos.isValidated(photo) then
            validated[#validated + 1] = Photos.uuid(photo)
        else
            kept[#kept + 1] = Photos.uuid(photo)
        end
    end
    if #validated == 0 then
        return nil
    end
    local narrowed = {}
    for key, value in pairs(params) do
        narrowed[key] = value
    end
    narrowed.target = nil
    narrowed.uuids = kept
    return narrowed, validated, missing
end

local function runGuarded(handler, command, params, job)
    local narrowed, validated, missing = withoutValidated(command, params)
    if not narrowed then
        return handler(params, job)
    end
    local failures = {}
    for _, uuid in ipairs(validated) do
        failures[#failures + 1] = { uuid = uuid, error = VALIDATED }
    end
    if #narrowed.uuids == 0 then
        if params.dry_run then
            return { dry_run = true, affected = 0, validated = validated,
                summary = "Nothing to change: the user validated these photos", missing = missing }
        end
        return { requested = #validated, updated = 0, failures = failures,
            validated = validated, missing = missing }
    end
    for _, uuid in ipairs(missing) do
        narrowed.uuids[#narrowed.uuids + 1] = uuid -- still reported as missing by the command
    end
    local result = handler(narrowed, job)
    if type(result) == "table" and not Result.isCancelled(result) then
        result.validated = validated
        if not params.dry_run then
            result.requested = (result.requested or 0) + #validated
            result.failures = result.failures or {}
            for _, failure in ipairs(failures) do
                result.failures[#result.failures + 1] = failure
            end
        end
    end
    return result
end

-- The parameters each command accepts, from commands.json (shared with the fake catalog).
-- A wrong name is refused instead of being ignored: ignoring it once made a command answer
-- for the active photo instead of the photo asked for.
local DECLARED = (function()
    local ok, decoded = pcall(function()
        return JSON.decode(LrFileUtils.readFile(LrPathUtils.child(_PLUGIN.path, "commands.json")))
    end)
    if ok and type(decoded) == "table" and type(decoded.commands) == "table" then
        return decoded
    end
    Logger.error("Cannot read commands.json: " .. tostring(decoded))
    return nil
end)()

-- Returns why the params do not fit the command, or nil when they do.
local function checkParams(command, params)
    if not DECLARED then
        return "Safelight's plugin cannot read its commands.json: reinstall the plugin"
    end
    local spec = DECLARED.commands[command]
    if type(spec) ~= "table" then
        return "No parameters are declared for " .. command .. " in commands.json"
    end
    local known = {}
    for _, name in ipairs(DECLARED.common or {}) do
        known[name] = true
    end
    for _, name in ipairs(spec.params or {}) do
        known[name] = true
    end
    if spec.photos then
        known.uuids = true
        known.target = true
    end
    local unknown = {}
    for key, _ in pairs(params) do
        if not known[key] then
            unknown[#unknown + 1] = tostring(key)
        end
    end
    if #unknown > 0 then
        local accepted = {}
        for name, _ in pairs(known) do
            accepted[#accepted + 1] = name
        end
        table.sort(unknown)
        table.sort(accepted)
        return "Unknown parameter for " .. command .. ": " .. table.concat(unknown, ", ")
            .. " (accepted: " .. table.concat(accepted, ", ") .. ")"
    end
    if spec.photos then
        local ids = type(params.uuids) == "table" and #params.uuids > 0
        if params.uuids ~= nil and params.target ~= nil then
            return command .. ": give photo ids (uuids) or a target, not both"
        end
        if not ids and params.target ~= "selected" and params.target ~= "active" then
            local problem = command .. ' needs photos: photo ids (uuids), or target "selected" or "active"'
            if next(params) == nil then
                problem = problem .. ". An AI app started before a Safelight update may send"
                    .. " this: restart it"
            end
            return problem
        end
    end
    return nil
end

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

    local params = type(request.params) == "table" and request.params or {}
    local problem = checkParams(command, params)
    if problem then
        return {
            id = requestId,
            success = false,
            error = { code = "BAD_REQUEST", message = problem },
        }
    end

    local ok, result = LrTasks.pcall(runGuarded, handler, command, params, job)
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
