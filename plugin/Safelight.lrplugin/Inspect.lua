-- Read-only inspection commands used to check what Lightroom exposes on a real install.
-- None of these change the catalog, the selection or the current module.
local LrApplication = import "LrApplication"
local LrApplicationView = import "LrApplicationView"
local LrDate = import "LrDate"
local LrDevelopController = import "LrDevelopController"
local LrTasks = import "LrTasks"

local Inspect = {}

-- Turns any Lua value into something JSON can carry: tables are summarized to a few levels,
-- long arrays are cut, and SDK objects become their type name.
local function describe(value, depth, maxItems)
    depth = depth or 3
    maxItems = maxItems or 3
    local kind = type(value)
    if kind == "number" then
        if value ~= value or value == math.huge or value == -math.huge then
            return tostring(value)
        end
        return value
    end
    if kind == "string" or kind == "boolean" or kind == "nil" then
        return value
    end
    if kind ~= "table" then
        return "<" .. kind .. ">"
    end
    if depth <= 0 then
        return "<table>"
    end
    local out = {}
    local isArray = #value > 0
    if isArray then
        local list = {}
        for i = 1, math.min(#value, maxItems) do
            list[#list + 1] = describe(value[i], depth - 1, maxItems)
        end
        if #value > maxItems then
            list[#list + 1] = "... " .. tostring(#value - maxItems) .. " more"
        end
        return list
    end
    for key, child in pairs(value) do
        out[tostring(key)] = describe(child, depth - 1, maxItems)
    end
    return out
end

local function typeMap(tbl)
    local types = {}
    local count = 0
    for key, value in pairs(tbl or {}) do
        types[tostring(key)] = type(value)
        count = count + 1
    end
    return types, count
end

local function activePhoto()
    local photo = LrApplication.activeCatalog():getTargetPhoto()
    if not photo then
        error("Select a photo in Lightroom first")
    end
    return photo
end

function Inspect.app(_)
    local catalog = LrApplication.activeCatalog()
    local photo = catalog:getTargetPhoto()
    local version = LrApplication.versionTable()
    return {
        lightroom = LrApplication.versionString(),
        version = { major = version.major, minor = version.minor, revision = version.revision },
        module = LrApplicationView.getCurrentModuleName(),
        catalog = catalog:getPath(),
        active_photo_uuid = photo and photo:getRawMetadata("uuid") or nil,
        selected_count = #(catalog:getTargetPhotos() or {}),
    }
end

function Inspect.developSettings(_)
    local photo = activePhoto()
    local settings = photo:getDevelopSettings() or {}
    local types, count = typeMap(settings)
    local tables = {}
    for key, value in pairs(settings) do
        if type(value) == "table" then
            tables[key] = describe(value, 5, 2)
        end
    end
    return {
        uuid = photo:getRawMetadata("uuid"),
        file_format = photo:getRawMetadata("fileFormat"),
        count = count,
        types = types,
        tables = tables,
    }
end

function Inspect.rawMetadata(_)
    local photo = activePhoto()
    local types, count = typeMap(photo:getRawMetadata(nil))
    return { count = count, types = types }
end

function Inspect.thumbnails(params)
    local photo = activePhoto()
    local sizes = (params and params.sizes) or { 256, 1024, 2048 }
    local results = {}
    for _, size in ipairs(sizes) do
        local started = LrDate.currentTime()
        local done, bytes, failure = false, nil, nil
        local request = photo:requestJpegThumbnail(size, size, function(data, errorMessage)
            if data then
                bytes = #data
            else
                failure = tostring(errorMessage)
            end
            done = true
        end)
        local waited = 0
        while not done and waited < 30 do
            LrTasks.sleep(0.02)
            waited = waited + 0.02
        end
        request = nil -- the SDK asks callers to hold the request until the callback ran
        results[#results + 1] = {
            requested = size,
            ms = math.floor((LrDate.currentTime() - started) * 1000 + 0.5),
            bytes = bytes,
            error = failure or (not done and "timed out" or nil),
        }
    end
    return { thumbnails = results }
end

local LOCAL_PARAM_CANDIDATES = {
    "local_Temperature", "local_Tint", "local_Exposure", "local_Contrast", "local_Highlights",
    "local_Shadows", "local_Whites", "local_Blacks", "local_Texture", "local_Clarity",
    "local_Dehaze", "local_Hue", "local_Saturation", "local_Sharpness", "local_LuminanceNoise",
    "local_Moire", "local_Defringe", "local_ToningHue", "local_ToningSaturation", "local_Grain",
    "local_Amount", "local_Vibrance", "local_RefineSaturation",
}

function Inspect.developController(_)
    local module = LrApplicationView.getCurrentModuleName()
    if module ~= "develop" then
        return { skipped = "Open the Develop module in Lightroom, then run this again.", module = module }
    end
    local out = { module = module }
    local okMasks, masks = pcall(LrDevelopController.getAllMasks)
    out.masks = okMasks and describe(masks, 5, 3) or ("error: " .. tostring(masks))
    local okSelected, selected = pcall(LrDevelopController.getSelectedMask)
    out.selected_mask = okSelected and describe(selected, 3) or ("error: " .. tostring(selected))
    local okTool, tool = pcall(LrDevelopController.getSelectedTool)
    out.selected_tool = okTool and tool or ("error: " .. tostring(tool))
    local okPv, pv = pcall(LrDevelopController.getProcessVersion)
    out.process_version = okPv and pv or nil
    local localParams = {}
    for _, name in ipairs(LOCAL_PARAM_CANDIDATES) do
        local ok, value = pcall(LrDevelopController.getValue, name)
        localParams[name] = ok and describe(value, 1) or ("error: " .. tostring(value))
    end
    out.local_params = localParams
    return out
end

return Inspect
