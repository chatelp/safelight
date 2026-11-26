-- Finding photos and describing them.
-- Photos are addressed by their UUID (the only stable id the SDK can look up directly).
local LrApplication = import "LrApplication"
local LrPathUtils = import "LrPathUtils"
local LrTasks = import "LrTasks"

local Logger = require "Logger"

local Photos = {}

local RAW_KEYS = {
    "uuid", "path", "fileFormat", "width", "height", "dateTimeOriginalISO8601", "rating",
    "pickStatus", "colorNameForLabel", "isVirtualCopy", "aperture", "shutterSpeed",
    "isoSpeedRating", "focalLength", "focalLength35mm", "exposureBias", "lens", "gps", "editCount",
    "isInStackInFolder", "stackPositionInFolder",
}
local FORMATTED_KEYS = { "cameraModel", "title", "caption", "keywordTags", "copyName" }

function Photos.catalog()
    return LrApplication.activeCatalog()
end

function Photos.uuid(photo)
    return photo:getRawMetadata("uuid")
end

-- Safelight's own fields (MetadataDefinition.lua). Before Lightroom has updated the
-- catalog for them, reading fails: no photo can be validated yet, so it then has none.
-- LrTasks.pcall, not pcall: SDK calls may yield, which a plain pcall turns into an error.
Photos.FIELDS = { "geometry", "validated", "rejectReason" }

function Photos.isValidated(photo)
    local ok, value = LrTasks.pcall(function()
        return photo:getPropertyForPlugin(_PLUGIN.id, "validated")
    end)
    if not ok then
        Logger.error("Could not read the Validated field: " .. tostring(value))
        return false
    end
    return value == "yes"
end

local function safelightFields(catalog, photos)
    local ok, values = LrTasks.pcall(function()
        return catalog:batchGetPropertyForPlugin(photos, _PLUGIN.id, Photos.FIELDS)
    end)
    if not ok then
        Logger.error("Could not read Safelight's fields: " .. tostring(values))
        return {}
    end
    return values or {}
end

-- Resolves the photos a command works on:
--   params.uuids = { ... }  -> those photos (unknown ids are reported in `missing`)
--   params.target = "active"   -> the active photo
--   otherwise               -> the selected photos
-- Raises an error when nothing is found unless opts.allowEmpty.
function Photos.resolve(params, opts)
    local catalog = Photos.catalog()
    local photos, missing = {}, {}
    params = params or {}
    if type(params.uuids) == "table" and #params.uuids > 0 then
        for _, uuid in ipairs(params.uuids) do
            local photo = catalog:findPhotoByUuid(tostring(uuid))
            if photo then
                photos[#photos + 1] = photo
            else
                missing[#missing + 1] = tostring(uuid)
            end
        end
    elseif params.target == "active" then
        local photo = catalog:getTargetPhoto()
        if photo then
            photos[1] = photo
        end
    else
        photos = catalog:getTargetPhotos() or {}
    end
    if #photos == 0 and not (opts and opts.allowEmpty) then
        if #missing > 0 then
            error("No photo found with these ids: " .. table.concat(missing, ", "))
        end
        error("No photo selected in Lightroom. Select photos, or pass photo ids.")
    end
    return photos, missing
end

local function nonEmpty(value)
    if value == nil or value == "" then
        return nil
    end
    return value
end

-- Plain descriptions of photos, read in two batched calls (fast on large selections).
function Photos.summaries(photos)
    local catalog = Photos.catalog()
    local raw = catalog:batchGetRawMetadata(photos, RAW_KEYS) or {}
    local formatted = catalog:batchGetFormattedMetadata(photos, FORMATTED_KEYS) or {}
    local own = safelightFields(catalog, photos)
    local out = {}
    for i, photo in ipairs(photos) do
        local r = raw[photo] or {}
        local f = formatted[photo] or {}
        local s = own[photo] or {}
        out[i] = {
            uuid = r.uuid,
            file_name = r.path and LrPathUtils.leafName(r.path) or nil,
            path = r.path,
            format = r.fileFormat,
            width = r.width,
            height = r.height,
            captured_at = r.dateTimeOriginalISO8601,
            rating = r.rating or 0,
            pick = r.pickStatus or 0,
            -- Lightroom reports "gray" (not the documented "none") for photos without a label.
            label = (r.colorNameForLabel ~= "none" and r.colorNameForLabel ~= "gray")
                and nonEmpty(r.colorNameForLabel) or nil,
            virtual_copy = r.isVirtualCopy == true,
            copy_name = nonEmpty(f.copyName),
            camera = nonEmpty(f.cameraModel),
            lens = nonEmpty(r.lens),
            focal_length_mm = r.focalLength,
            focal_length_35mm = r.focalLength35mm,
            aperture = r.aperture,
            shutter_s = r.shutterSpeed,
            iso = r.isoSpeedRating,
            exposure_bias = r.exposureBias,
            gps = r.gps,
            title = nonEmpty(f.title),
            caption = nonEmpty(f.caption),
            keywords = nonEmpty(f.keywordTags),
            edited = (r.editCount or 0) > 0,
            -- 1 for the photo shown on top of a stack, 2+ for the frames under it
            -- (for example the brackets under an HDR merge); nil when not stacked.
            stack_position = r.isInStackInFolder and r.stackPositionInFolder or nil,
            -- Safelight's fields: validated photos keep their edits.
            validated = s.validated == "yes",
            geometry_by = nonEmpty(s.geometry),
            reject_reason = nonEmpty(s.rejectReason),
        }
    end
    return out
end

return Photos
