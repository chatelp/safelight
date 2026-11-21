-- Export command: renders photos with their edits to files, with progress and cancellation.
local LrExportSession = import "LrExportSession"
local LrFileUtils = import "LrFileUtils"
local LrPathUtils = import "LrPathUtils"

local Photos = require "Photos"
local Result = require "Result"

local Export = {}

local FORMATS = { JPEG = true, TIFF = true, PNG = true, DNG = true, ORIGINAL = true }
local COLOR_SPACES = { sRGB = "sRGB", AdobeRGB = "AdobeRGB", DisplayP3 = "DisplayP3", ProPhotoRGB = "ProPhotoRGB" }
local COLLISIONS = { rename = "rename", overwrite = "overwrite", skip = "skip" }

local function exportSettings(params)
    local format = string.upper(tostring(params.format or "JPEG"))
    if not FORMATS[format] then
        error("format must be JPEG, TIFF, PNG, DNG or ORIGINAL")
    end
    local destination = params.destination
    if type(destination) ~= "string" or destination == "" or destination:sub(1, 1) ~= "/" then
        error("destination must be an absolute folder path")
    end
    local colorSpace = COLOR_SPACES[params.color_space or "sRGB"]
    if not colorSpace then
        error("color_space must be sRGB, AdobeRGB, DisplayP3 or ProPhotoRGB")
    end
    local collision = COLLISIONS[params.if_exists or "rename"]
    if not collision then
        error("if_exists must be rename, overwrite or skip")
    end
    local settings = {
        LR_export_destinationType = "specificFolder",
        LR_export_destinationPathPrefix = destination,
        LR_export_useSubfolder = false,
        LR_format = format,
        LR_export_colorSpace = colorSpace,
        LR_collisionHandling = collision,
        LR_reimportExportedPhoto = false,
        LR_renamingTokensOn = false,
        LR_minimizeEmbeddedMetadata = params.strip_metadata == true,
        LR_metadata_keywordOptions = "lightroomHierarchical",
        LR_removeLocationMetadata = params.remove_location == true,
        LR_includeVideoFiles = false,
        LR_outputSharpeningOn = params.sharpen ~= nil,
        LR_outputSharpeningMedia = params.sharpen == "matte" and "matte"
            or params.sharpen == "glossy" and "glossy" or "screen",
        LR_outputSharpeningLevel = 2,
    }
    if format == "JPEG" then
        local quality = math.max(1, math.min(tonumber(params.quality) or 85, 100))
        settings.LR_jpeg_quality = quality / 100
        settings.LR_jpeg_useLimitSize = false
    elseif format == "TIFF" then
        settings.LR_export_bitDepth = tonumber(params.bit_depth) == 16 and 16 or 8
        settings.LR_tiff_compressionMethod = "compressionMethod_ZIP"
    end
    local longEdge = tonumber(params.long_edge_px)
    if longEdge and longEdge > 0 then
        settings.LR_size_doConstrain = true
        settings.LR_size_resizeType = "longEdge"
        settings.LR_size_maxHeight = math.floor(longEdge)
        settings.LR_size_maxWidth = math.floor(longEdge)
        settings.LR_size_units = "pixels"
        settings.LR_size_doNotEnlarge = true
    else
        settings.LR_size_doConstrain = false
    end
    return settings, format
end

-- export.photos {uuids|target, destination, format?, quality?, long_edge_px?, color_space?,
--               if_exists?, sharpen?, strip_metadata?, remove_location?, dry_run?}
-- Returns the absolute path of every exported file. Existing files are kept by default
-- (new files get a numbered name).
function Export.photos(params, job)
    local settings, format = exportSettings(params)
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return {
            dry_run = true,
            affected = #photos,
            summary = "Export " .. #photos .. " photos as " .. format .. " to " .. params.destination,
            missing = missing,
        }
    end
    LrFileUtils.createAllDirectories(params.destination)
    local session = LrExportSession({ photosToExport = photos, exportSettings = settings })
    local exported, failed = {}, {}
    local total, handled, cancelled = #photos, 0, false
    for _, rendition in session:renditions() do
        if cancelled or job.progress(handled, total, "Exporting") then
            cancelled = true
            rendition:skipRender()
        else
            local ok, pathOrMessage = rendition:waitForRender()
            local uuid = rendition.photo and Photos.uuid(rendition.photo) or nil
            if ok then
                exported[#exported + 1] = { uuid = uuid, path = pathOrMessage, file_name = LrPathUtils.leafName(pathOrMessage) }
            else
                failed[#failed + 1] = { uuid = uuid, error = tostring(pathOrMessage) }
            end
        end
        handled = handled + 1
    end
    job.progress(handled, total, "Exported")
    if cancelled then
        return Result.cancelled("Stopped after exporting " .. #exported .. " of " .. total .. " photos")
    end
    return {
        destination = params.destination,
        format = format,
        exported = exported,
        failed = failed,
        missing = missing,
    }
end

return Export
