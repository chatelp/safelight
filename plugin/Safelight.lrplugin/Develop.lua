-- Develop commands: read and change settings, presets, snapshots, auto adjustments.
-- Settings go through the catalog (any photo, in batches); auto tone and slider ranges
-- need the Develop module (one photo at a time).
local LrApplication = import "LrApplication"
local LrDevelopController = import "LrDevelopController"
local LrTasks = import "LrTasks"

local Batch = require "Batch"
local DevelopModule = require "DevelopModule"
local DevelopRanges = require "DevelopRanges"
local Photos = require "Photos"
local Result = require "Result"

local Develop = {}

-- Develop settings of Process Version 2012 and later store these sliders under a
-- "2012" key; the plain names only drive photos still on Process Version 1 or 2.
local PV2012_KEYS = {
    Exposure = "Exposure2012",
    Contrast = "Contrast2012",
    Highlights = "Highlights2012",
    Shadows = "Shadows2012",
    Whites = "Whites2012",
    Blacks = "Blacks2012",
    Clarity = "Clarity2012",
}
local LEGACY_PROCESS_VERSIONS = { ["5.0"] = true, ["5.7"] = true }

local RAW_FORMATS = { RAW = true, DNG = true }

local function isRaw(photo)
    return RAW_FORMATS[photo:getRawMetadata("fileFormat")] == true
end

local function settingKey(name, current)
    local modern = PV2012_KEYS[name]
    if modern and not LEGACY_PROCESS_VERSIONS[tostring(current.ProcessVersion)] then
        return modern
    end
    return name
end

local function rangeFor(key, raw)
    local byFormat = raw and DevelopRanges.raw or DevelopRanges.non_raw
    return byFormat[key] or DevelopRanges.common[key]
end

local function clamp(value, range)
    if not range then
        return value, false
    end
    if value < range[1] then
        return range[1], true
    end
    if value > range[2] then
        return range[2], true
    end
    return value, false
end

-- Computes the settings to write on one photo. `changes` maps setting names (plain or
-- "2012") to values; with relative = true, numbers are added to the current values.
-- Numbers are clamped to the photo's slider range. Returns the table and the notes.
local function resolveSettings(photo, current, changes, relative)
    local raw = isRaw(photo)
    local out, notes = {}, {}
    for name, value in pairs(changes) do
        local key = settingKey(name, current)
        if type(value) == "number" then
            if relative then
                local base = current[key]
                if type(base) ~= "number" then
                    error("Cannot change '" .. name .. "' relatively: it has no numeric value")
                end
                value = base + value
            end
            local clamped, wasClamped = clamp(value, rangeFor(key, raw))
            if wasClamped then
                notes[#notes + 1] = name .. " limited to " .. tostring(clamped)
            end
            out[key] = clamped
        else
            if relative then
                error("Only numeric settings can be changed relatively ('" .. name .. "' is not)")
            end
            out[key] = value
        end
    end
    return out, notes
end

-- develop.get {uuids|target, keys?}: current settings of the first photo.
function Develop.get(params)
    local photos = Photos.resolve(params)
    local photo = photos[1]
    local settings = photo:getDevelopSettings() or {}
    local out = settings
    if type(params.keys) == "table" and #params.keys > 0 then
        out = {}
        for _, name in ipairs(params.keys) do
            local key = settingKey(name, settings)
            out[name] = settings[key]
        end
    else
        -- Masks have their own command; keep the answer readable.
        out = {}
        for key, value in pairs(settings) do
            if key ~= "MaskGroupBasedCorrections" then
                out[key] = value
            end
        end
    end
    return {
        uuid = Photos.uuid(photo),
        format = photo:getRawMetadata("fileFormat"),
        process_version = settings.ProcessVersion,
        settings = out,
    }
end

-- develop.apply {uuids|target, settings, relative?, history_name?, snapshot?, dry_run?}
-- With `snapshot`, each photo first gets a snapshot of that name (its settings before).
function Develop.apply(params, job)
    if type(params.settings) ~= "table" or next(params.settings) == nil then
        error("settings must be a non-empty object")
    end
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        local action = params.history_name and params.history_name:gsub("^Safelight: ", "")
            or "Change develop settings"
        return Batch.plan(photos, action .. " on " .. #photos .. " photos", missing)
    end
    local notes = {}
    local result = Batch.write(params.history_name or "Safelight: Develop", photos, job, function(photo)
        if type(params.snapshot) == "string" and params.snapshot ~= "" then
            -- Never overwrite: running the same change twice keeps the first "before".
            local exists = false
            for _, snapshot in ipairs(photo:getDevelopSnapshots() or {}) do
                if snapshot.name == params.snapshot then
                    exists = true
                end
            end
            if not exists then
                photo:createDevelopSnapshot(params.snapshot, false)
            end
        end
        local current = photo:getDevelopSettings() or {}
        local settings, photoNotes = resolveSettings(photo, current, params.settings, params.relative == true)
        photo:applyDevelopSettings(settings, params.history_name)
        if #photoNotes > 0 then
            notes[Photos.uuid(photo)] = photoNotes
        end
    end)
    result.missing = missing
    result.notes = notes
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

-- develop.white_balance_auto {uuids|target, dry_run?}: Auto white balance, computed now.
function Develop.autoWhiteBalance(params, job)
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Set white balance to Auto on " .. #photos .. " photos", missing)
    end
    local result = Batch.write("Safelight: Auto White Balance", photos, job, function(photo)
        photo:applyDevelopSettings({ WhiteBalance = "Auto" }, "Safelight: Auto White Balance", true)
    end)
    result.missing = missing
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

-- develop.auto_tone {uuids|target, dry_run?}: Lightroom's Auto Tone, one photo at a time in
-- the Develop module (the catalog API only knows the legacy auto flags).
function Develop.autoTone(params, job)
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Apply Auto Tone to " .. #photos .. " photos", missing)
    end
    local done = 0
    for index, photo in ipairs(photos) do
        if job.progress(index - 1, #photos, "Auto Tone") then
            return Result.cancelled("Stopped after " .. done .. " of " .. #photos .. " photos")
        end
        DevelopModule.withPhoto(photo, function()
            LrDevelopController.setAutoTone()
        end)
        done = done + 1
    end
    job.progress(done, #photos, "Auto Tone")
    return { requested = #photos, updated = done, missing = missing }
end

-- develop.reset {uuids|target, dry_run?}: reset all develop settings (undoable in history).
function Develop.reset(params, job)
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Reset all develop settings of " .. #photos .. " photos", missing)
    end
    local done = 0
    for index, photo in ipairs(photos) do
        if job.progress(index - 1, #photos, "Reset") then
            return Result.cancelled("Stopped after " .. done .. " of " .. #photos .. " photos")
        end
        DevelopModule.withPhoto(photo, function()
            LrDevelopController.resetAllDevelopAdjustments()
        end)
        done = done + 1
    end
    job.progress(done, #photos, "Reset")
    return { requested = #photos, updated = done, missing = missing }
end

-- develop.copy {from: uuid, uuids|target, keys?, dry_run?}: copy settings from one photo.
-- Without keys, copies everything except crop, masks, spot removal and local fixes.
local NOT_COPIED_BY_DEFAULT = {
    CropTop = true, CropLeft = true, CropBottom = true, CropRight = true, CropAngle = true,
    CropConstrainAspectRatio = true, MaskGroupBasedCorrections = true, RedEyeInfo = true,
    orientation = true,
}
function Develop.copy(params, job)
    if type(params.from) ~= "string" then
        error("from must be the id of the photo to copy from")
    end
    local source = Photos.catalog():findPhotoByUuid(params.from)
    if not source then
        error("No photo found with id " .. params.from)
    end
    local photos, missing = Photos.resolve(params)
    local sourceSettings = source:getDevelopSettings() or {}
    local settings = {}
    if type(params.keys) == "table" and #params.keys > 0 then
        for _, name in ipairs(params.keys) do
            local key = settingKey(name, sourceSettings)
            settings[key] = sourceSettings[key]
        end
    else
        for key, value in pairs(sourceSettings) do
            if not NOT_COPIED_BY_DEFAULT[key] then
                settings[key] = value
            end
        end
    end
    if params.dry_run then
        return Batch.plan(photos, "Copy develop settings to " .. #photos .. " photos", missing)
    end
    local result = Batch.write("Safelight: Copy Settings", photos, job, function(photo)
        photo:applyDevelopSettings(settings, "Safelight: Copy Settings")
    end)
    result.missing = missing
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

-- presets.list {}: develop presets, by folder.
function Develop.listPresets(_)
    local presets = {}
    for _, folder in ipairs(LrApplication.developPresetFolders() or {}) do
        local folderName = folder:getName()
        for _, preset in ipairs(folder:getDevelopPresets() or {}) do
            presets[#presets + 1] = { uuid = preset:getUuid(), name = preset:getName(), folder = folderName }
        end
    end
    return { count = #presets, presets = presets }
end

-- develop.apply_preset {preset_uuid, uuids|target, amount?, dry_run?}
function Develop.applyPreset(params, job)
    local preset = LrApplication.developPresetByUuid(tostring(params.preset_uuid))
    if not preset then
        error("No develop preset with id " .. tostring(params.preset_uuid))
    end
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Apply preset '" .. preset:getName() .. "' to " .. #photos .. " photos", missing)
    end
    local amount = tonumber(params.amount)
    local result = Batch.write("Safelight: Preset " .. preset:getName(), photos, job, function(photo)
        photo:applyDevelopPreset(preset, nil, amount)
    end)
    result.preset = preset:getName()
    result.missing = missing
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

-- snapshots.list {uuids|target}
function Develop.listSnapshots(params)
    local photo = Photos.resolve(params)[1]
    local snapshots = {}
    for _, snapshot in ipairs(photo:getDevelopSnapshots() or {}) do
        snapshots[#snapshots + 1] = { id = snapshot.snapshotID, name = snapshot.name }
    end
    return { uuid = Photos.uuid(photo), snapshots = snapshots }
end

-- snapshots.create {name, uuids|target, dry_run?}: one snapshot per photo.
function Develop.createSnapshot(params, job)
    if type(params.name) ~= "string" or params.name == "" then
        error("name is required")
    end
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Create snapshot '" .. params.name .. "' on " .. #photos .. " photos", missing)
    end
    local result = Batch.write("Safelight: Snapshot", photos, job, function(photo)
        photo:createDevelopSnapshot(params.name, params.update_existing == true)
    end)
    result.missing = missing
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

local function findSnapshot(photo, params)
    for _, snapshot in ipairs(photo:getDevelopSnapshots() or {}) do
        if snapshot.snapshotID == params.snapshot_id or (params.name and snapshot.name == params.name) then
            return snapshot
        end
    end
    return nil
end

-- snapshots.apply {uuids|target, snapshot_id | name}
function Develop.applySnapshot(params)
    local photo = Photos.resolve(params)[1]
    local snapshot = findSnapshot(photo, params)
    if not snapshot then
        error("No snapshot with this id or name on this photo")
    end
    Batch.writeOnce("Safelight: Apply Snapshot", function()
        photo:applyDevelopSnapshot(snapshot.snapshotID)
    end)
    return { uuid = Photos.uuid(photo), applied = snapshot.name }
end

-- snapshots.delete {uuids|target, snapshot_id | name}
function Develop.deleteSnapshot(params)
    local photo = Photos.resolve(params)[1]
    local snapshot = findSnapshot(photo, params)
    if not snapshot then
        error("No snapshot with this id or name on this photo")
    end
    if params.dry_run then
        return { dry_run = true, affected = 1, summary = "Delete snapshot '" .. snapshot.name .. "'" }
    end
    Batch.writeOnce("Safelight: Delete Snapshot", function()
        photo:deleteDevelopSnapshot(snapshot.snapshotID)
    end)
    return { uuid = Photos.uuid(photo), deleted = snapshot.name }
end

-- inspect.develop_ranges {uuids|target}: real slider ranges for one photo (Develop module).
function Develop.captureRanges(params)
    local photo = Photos.resolve(params)[1]
    local settings = photo:getDevelopSettings() or {}
    local names = {}
    for key, value in pairs(settings) do
        if type(value) == "number" then
            names[#names + 1] = key
        end
    end
    for plain, _ in pairs(PV2012_KEYS) do
        names[#names + 1] = plain
    end
    local ranges, unavailable = {}, {}
    DevelopModule.withPhoto(photo, function()
        for _, name in ipairs(names) do
            local ok, low, high = LrTasks.pcall(LrDevelopController.getRange, name)
            if ok and type(low) == "number" and type(high) == "number" then
                ranges[name] = { low, high }
            else
                unavailable[#unavailable + 1] = name
            end
        end
    end)
    -- The 2012 keys share the range of their plain name in the Develop module.
    for plain, modern in pairs(PV2012_KEYS) do
        if ranges[plain] and not ranges[modern] then
            ranges[modern] = ranges[plain]
        end
    end
    table.sort(unavailable)
    return { format = photo:getRawMetadata("fileFormat"), ranges = ranges, unavailable = unavailable }
end

return Develop
