-- Mask commands. Reading and adjusting existing masks goes through the catalog (any photo,
-- no UI); creating, deleting and inverting masks uses the Develop module on one photo.
local LrDevelopController = import "LrDevelopController"
local LrTasks = import "LrTasks"

local Batch = require "Batch"
local DevelopModule = require "DevelopModule"
local Photos = require "Photos"

local Masks = {}

-- Friendly local slider names -> develop-settings keys, with the factor between the
-- value shown in Lightroom and the stored value (stored = shown / scale).
local LOCAL = {
    Exposure = { key = "LocalExposure2012", scale = 4, min = -4, max = 4 },
    Contrast = { key = "LocalContrast2012", scale = 100, min = -100, max = 100 },
    Highlights = { key = "LocalHighlights2012", scale = 100, min = -100, max = 100 },
    Shadows = { key = "LocalShadows2012", scale = 100, min = -100, max = 100 },
    Whites = { key = "LocalWhites2012", scale = 100, min = -100, max = 100 },
    Blacks = { key = "LocalBlacks2012", scale = 100, min = -100, max = 100 },
    Texture = { key = "LocalTexture", scale = 100, min = -100, max = 100 },
    Clarity = { key = "LocalClarity2012", scale = 100, min = -100, max = 100 },
    Dehaze = { key = "LocalDehaze", scale = 100, min = -100, max = 100 },
    Temperature = { key = "LocalTemperature", scale = 100, min = -100, max = 100 },
    Tint = { key = "LocalTint", scale = 100, min = -100, max = 100 },
    Hue = { key = "LocalHue", scale = 1, min = -180, max = 180 },
    Saturation = { key = "LocalSaturation", scale = 100, min = -100, max = 100 },
    Sharpness = { key = "LocalSharpness", scale = 100, min = -100, max = 100 },
    Noise = { key = "LocalLuminanceNoise", scale = 100, min = -100, max = 100 },
    Moire = { key = "LocalMoire", scale = 100, min = -100, max = 100 },
    Defringe = { key = "LocalDefringe", scale = 100, min = -100, max = 100 },
    Grain = { key = "LocalGrain", scale = 100, min = 0, max = 100 },
    Amount = { key = "CorrectionAmount", scale = 1, min = 0, max = 1 },
}

local CREATABLE = {
    subject = { "aiSelection", "subject" },
    sky = { "aiSelection", "sky" },
    background = { "aiSelection", "background" },
    people = { "aiSelection", "people" },
}

local function describeComponent(component)
    return {
        id = component.MaskID,
        name = component.MaskName,
        kind = component.What,
        subtype = component.MaskSubType,
        inverted = component.MaskInverted == true,
    }
end

local function describeCorrection(correction)
    local values = {}
    for name, spec in pairs(LOCAL) do
        local stored = correction[spec.key]
        if type(stored) == "number" and stored ~= 0 and name ~= "Amount" then
            values[name] = stored * spec.scale
        end
    end
    local components = {}
    for _, component in ipairs(correction.CorrectionMasks or {}) do
        components[#components + 1] = describeComponent(component)
    end
    return {
        id = correction.CorrectionID,
        name = correction.CorrectionName,
        active = correction.CorrectionActive ~= false,
        amount = correction.CorrectionAmount,
        adjustments = values,
        components = components,
    }
end

-- masks.list {uuids|target}: masks of a photo with their non-zero adjustments.
function Masks.list(params)
    local photo = Photos.resolve(params)[1]
    local settings = photo:getDevelopSettings() or {}
    local out = {}
    for _, correction in ipairs(settings.MaskGroupBasedCorrections or {}) do
        out[#out + 1] = describeCorrection(correction)
    end
    return { uuid = Photos.uuid(photo), masks = out }
end

-- masks.adjust {uuids|target, mask_id, adjustments = {Exposure = 0.5, ...}, relative?, dry_run?}
-- Values are as shown in Lightroom (Exposure in EV, others -100..100).
function Masks.adjust(params)
    if type(params.adjustments) ~= "table" or next(params.adjustments) == nil then
        error("adjustments must be a non-empty object")
    end
    local photo = Photos.resolve(params)[1]
    local settings = photo:getDevelopSettings() or {}
    local corrections = settings.MaskGroupBasedCorrections or {}
    local target = nil
    for _, correction in ipairs(corrections) do
        if correction.CorrectionID == params.mask_id or correction.CorrectionName == params.mask_id then
            target = correction
        end
    end
    if not target then
        error("No mask '" .. tostring(params.mask_id) .. "' on this photo")
    end
    for name, value in pairs(params.adjustments) do
        local spec = LOCAL[name]
        if not spec then
            error("Unknown mask adjustment '" .. tostring(name) .. "'")
        end
        if type(value) ~= "number" then
            error("Mask adjustment '" .. name .. "' must be a number")
        end
        local shown = value
        if params.relative then
            shown = (target[spec.key] or 0) * spec.scale + value
        end
        shown = math.max(spec.min, math.min(spec.max, shown))
        target[spec.key] = shown / spec.scale
    end
    if params.dry_run then
        return { dry_run = true, affected = 1, summary = "Adjust mask '" .. tostring(target.CorrectionName) .. "'" }
    end
    Batch.writeOnce("Safelight: Adjust Mask", function()
        photo:applyDevelopSettings({ MaskGroupBasedCorrections = corrections }, "Safelight: Adjust Mask")
    end)
    return { uuid = Photos.uuid(photo), mask = describeCorrection(target) }
end

local function waitForSelectedMask(previous)
    local waited = 0
    while waited < 30 do
        local ok, selected = LrTasks.pcall(LrDevelopController.getSelectedMask)
        if ok and selected and selected ~= previous then
            return selected
        end
        LrTasks.sleep(0.25)
        waited = waited + 0.25
    end
    return nil
end

-- masks.create {uuids|target, kind = "subject"|"sky"|"background"|"people"}
-- Opens the photo in Develop and lets Lightroom detect the area. Returns the new mask id.
function Masks.create(params)
    local spec = CREATABLE[params.kind]
    if not spec then
        error("kind must be subject, sky, background or people")
    end
    local photo = Photos.resolve(params)[1]
    if params.dry_run then
        return { dry_run = true, affected = 1, summary = "Create a " .. params.kind .. " mask" }
    end
    local maskId = DevelopModule.withMasking(photo, function()
        local before = LrDevelopController.getSelectedMask()
        LrDevelopController.createNewMask(spec[1], spec[2])
        return waitForSelectedMask(before)
    end)
    return { uuid = Photos.uuid(photo), mask_id = maskId, kind = params.kind }
end

-- masks.delete {uuids|target, mask_id, dry_run?}
function Masks.delete(params)
    local photo = Photos.resolve(params)[1]
    if params.dry_run then
        return { dry_run = true, affected = 1, summary = "Delete mask " .. tostring(params.mask_id) }
    end
    DevelopModule.withMasking(photo, function()
        LrDevelopController.deleteMask(tostring(params.mask_id))
    end)
    return { uuid = Photos.uuid(photo), deleted = params.mask_id }
end

-- masks.invert {uuids|target, mask_id, dry_run?}
function Masks.invert(params)
    local photo = Photos.resolve(params)[1]
    if params.dry_run then
        return { dry_run = true, affected = 1, summary = "Invert mask " .. tostring(params.mask_id) }
    end
    DevelopModule.withMasking(photo, function()
        LrDevelopController.invertMask(tostring(params.mask_id))
    end)
    return { uuid = Photos.uuid(photo), inverted = params.mask_id }
end

return Masks
