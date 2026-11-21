-- Finding photos and describing them.
-- Photos are addressed by their UUID (the only stable id the SDK can look up directly).
local LrApplication = import "LrApplication"
local LrPathUtils = import "LrPathUtils"

local Photos = {}

local RAW_KEYS = {
    "uuid", "path", "fileFormat", "width", "height", "dateTimeOriginalISO8601", "rating",
    "pickStatus", "colorNameForLabel", "isVirtualCopy", "aperture", "shutterSpeed",
    "isoSpeedRating", "focalLength", "exposureBias", "lens", "gps", "editCount",
}
local FORMATTED_KEYS = { "cameraModel", "title", "caption", "keywordTags", "copyName" }

function Photos.catalog()
    return LrApplication.activeCatalog()
end

function Photos.uuid(photo)
    return photo:getRawMetadata("uuid")
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
    local out = {}
    for i, photo in ipairs(photos) do
        local r = raw[photo] or {}
        local f = formatted[photo] or {}
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
            label = nonEmpty(r.colorNameForLabel ~= "none" and r.colorNameForLabel or nil),
            virtual_copy = r.isVirtualCopy == true,
            copy_name = nonEmpty(f.copyName),
            camera = nonEmpty(f.cameraModel),
            lens = nonEmpty(r.lens),
            focal_length_mm = r.focalLength,
            aperture = r.aperture,
            shutter_s = r.shutterSpeed,
            iso = r.isoSpeedRating,
            exposure_bias = r.exposureBias,
            gps = r.gps,
            title = nonEmpty(f.title),
            caption = nonEmpty(f.caption),
            keywords = nonEmpty(f.keywordTags),
            edited = (r.editCount or 0) > 0,
        }
    end
    return out
end

return Photos
