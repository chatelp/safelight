-- Metadata and keyword commands.
local LrTasks = import "LrTasks"
local Batch = require "Batch"
local Photos = require "Photos"
local Result = require "Result"

local Metadata = {}

-- Friendly field names -> setRawMetadata keys, with a validator for each value.
local function isString(value)
    return type(value) == "string"
end

local FIELDS = {
    rating = { key = "rating", valid = function(v) return type(v) == "number" and v >= 0 and v <= 5 and v % 1 == 0 end },
    pick = { key = "pickStatus", valid = function(v) return v == -1 or v == 0 or v == 1 end },
    label = { key = "colorNameForLabel", valid = function(v)
        return v == "red" or v == "yellow" or v == "green" or v == "blue" or v == "purple" or v == "none"
    end },
    title = { key = "title", valid = isString },
    caption = { key = "caption", valid = isString },
    headline = { key = "headline", valid = isString },
    copyright = { key = "copyright", valid = isString },
    creator = { key = "creator", valid = isString },
    location = { key = "location", valid = isString },
    city = { key = "city", valid = isString },
    state = { key = "stateProvince", valid = isString },
    country = { key = "country", valid = isString },
    country_code = { key = "isoCountryCode", valid = isString },
    event = { key = "event", valid = isString },
    alt_text = { key = "altTextAccessibility", valid = isString },
    extended_description = { key = "extDescrAccessibility", valid = isString },
    gps = { key = "gps", valid = function(v)
        return v == false or (type(v) == "table" and type(v.latitude) == "number" and type(v.longitude) == "number")
    end },
    gps_altitude = { key = "gpsAltitude", valid = function(v) return type(v) == "number" end },
    -- Safelight's own fields (MetadataDefinition.lua).
    validated = { plugin = "validated", valid = function(v) return type(v) == "boolean" end,
        convert = function(v) return v and "yes" or nil end },
    geometry_by = { plugin = "geometry", valid = function(v)
        return v == "safelight" or v == "user" or v == "by_hand" or v == "none"
    end, convert = function(v) return v ~= "none" and v or nil end },
    reject_reason = { plugin = "rejectReason", valid = isString,
        convert = function(v) return v ~= "" and v or nil end },
    -- The color Safelight gave the photo; "user": its color is the user's.
    safelight_label = { plugin = "safelightLabel", valid = function(v)
        return v == "red" or v == "yellow" or v == "green" or v == "blue" or v == "purple"
            or v == "user" or v == "none"
    end, convert = function(v) return v ~= "none" and v or nil end },
}

local function fieldNames()
    local names = {}
    for name, _ in pairs(FIELDS) do
        names[#names + 1] = name
    end
    table.sort(names)
    return table.concat(names, ", ")
end

-- metadata.set {uuids|target, fields = {rating = 4, label = "red", ...}, dry_run?}
function Metadata.set(params, job)
    if type(params.fields) ~= "table" or next(params.fields) == nil then
        error("fields must be a non-empty object. Known fields: " .. fieldNames())
    end
    local changes = {}
    for name, value in pairs(params.fields) do
        local field = FIELDS[name]
        if not field then
            error("Unknown field '" .. tostring(name) .. "'. Known fields: " .. fieldNames())
        end
        if not field.valid(value) then
            error("Invalid value for '" .. name .. "'")
        end
        if field.key == "gps" and value == false then
            value = nil -- removes the location
        end
        if field.convert then
            value = field.convert(value)
        end
        changes[#changes + 1] = { key = field.key, plugin = field.plugin, value = value }
    end
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Change metadata of " .. #photos .. " photos", missing)
    end
    local result = Batch.write("Safelight: Metadata", photos, job, function(photo)
        for _, change in ipairs(changes) do
            if change.plugin then
                photo:setPropertyForPlugin(_PLUGIN, change.plugin, change.value)
            else
                photo:setRawMetadata(change.key, change.value)
            end
        end
    end)
    result.missing = missing
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

-- safelight.tidy {validated_label, legacy_label, check?}: photos the user validated lose
-- the color Safelight gave them (or get `validated_label`), and Safelight forgets it; a
-- color the user changed since is left alone, and recorded as the user's ("user").
-- Photos marked before Safelight recorded its colors count as Safelight's when they have
-- its Geometry field and `legacy_label`. `check`: photo ids, of which the validated ones
-- are returned (for the app's list of photos to check).
local COLORS = { red = true, yellow = true, green = true, blue = true, purple = true, none = true }

local function photosWith(catalog, field)
    local ok, found = LrTasks.pcall(function()
        return catalog:findPhotosWithProperty(_PLUGIN.id, field)
    end)
    return ok and found or {}
end

function Metadata.tidy(params)
    if not COLORS[params.validated_label] or not COLORS[params.legacy_label] then
        error("validated_label and legacy_label must be colors or none")
    end
    local catalog = Photos.catalog()
    local found, seen = {}, {}
    for _, field in ipairs({ "safelightLabel", "validated" }) do
        for _, photo in ipairs(photosWith(catalog, field)) do
            if not seen[photo] then
                seen[photo] = true
                found[#found + 1] = photo
            end
        end
    end
    local changes = {}
    if #found > 0 then
        local okOwn, own = LrTasks.pcall(function()
            return catalog:batchGetPropertyForPlugin(found, _PLUGIN.id,
                { "validated", "geometry", "safelightLabel" })
        end)
        own = okOwn and own or {}
        local raw = catalog:batchGetRawMetadata(found, { "colorNameForLabel" }) or {}
        for _, photo in ipairs(found) do
            local fields = own[photo] or {}
            local label = (raw[photo] or {}).colorNameForLabel
            local mine = fields.safelightLabel
            if not mine and (fields.geometry == "safelight" or fields.geometry == "by_hand") then
                mine = params.legacy_label ~= "none" and params.legacy_label or nil
                if label ~= mine then
                    mine = nil -- not a color Safelight gave
                end
            end
            if fields.safelightLabel == "user" then
                mine = nil -- the user's color
            elseif fields.safelightLabel and label ~= fields.safelightLabel then
                changes[#changes + 1] = { photo = photo, record = "user" }
            elseif mine and fields.validated == "yes" then
                changes[#changes + 1] = { photo = photo, color = mine }
            end
        end
    end
    local changed = {}
    if #changes > 0 then
        Batch.writeOnce("Safelight: Validated", function()
            for _, change in ipairs(changes) do
                -- Checked again in the gate: the user may have changed it meanwhile.
                if change.color and change.photo:getRawMetadata("colorNameForLabel") == change.color then
                    change.photo:setRawMetadata("colorNameForLabel", params.validated_label)
                    changed[#changed + 1] = Photos.uuid(change.photo)
                end
                change.photo:setPropertyForPlugin(_PLUGIN, "safelightLabel", change.record)
            end
        end)
    end
    local validated = {}
    for _, uuid in ipairs(type(params.check) == "table" and params.check or {}) do
        local photo = catalog:findPhotoByUuid(tostring(uuid))
        if photo and Photos.isValidated(photo) then
            validated[#validated + 1] = tostring(uuid)
        end
    end
    return { changed = changed, validated = validated }
end

-- -- Keywords ----------------------------------------------------------------------------

local function splitPath(path)
    local parts = {}
    for raw in tostring(path):gmatch("[^>]+") do
        local part = raw:gsub("^%s+", ""):gsub("%s+$", "")
        if part ~= "" then
            parts[#parts + 1] = part
        end
    end
    return parts
end

local function childNamed(keywords, name)
    local wanted = name:lower()
    for _, keyword in ipairs(keywords or {}) do
        if keyword:getName():lower() == wanted then
            return keyword
        end
    end
    return nil
end

-- Finds an existing keyword by full path "A > B > C" (case-insensitive), or nil.
local function findByPath(path)
    local parts = splitPath(path)
    if #parts == 0 then
        return nil
    end
    local node = childNamed(Photos.catalog():getKeywords(), parts[1])
    for index = 2, #parts do
        if not node then
            return nil
        end
        node = childNamed(node:getChildren(), parts[index])
    end
    return node
end

-- Finds keywords by name anywhere in the hierarchy.
local function findByName(name, out, nodes, prefix)
    for _, keyword in ipairs(nodes or {}) do
        local path = prefix and (prefix .. " > " .. keyword:getName()) or keyword:getName()
        if keyword:getName():lower() == name:lower() then
            out[#out + 1] = { keyword = keyword, path = path }
        end
        findByName(name, out, keyword:getChildren(), path)
    end
    return out
end

-- keywords.list {query?, limit?}: keyword paths, optionally containing `query`.
function Metadata.listKeywords(params)
    local query = params.query and tostring(params.query):lower() or nil
    local limit = math.max(1, math.min(tonumber(params.limit) or 500, 5000))
    local out, total = {}, 0
    local function walk(nodes, prefix, depth)
        if depth > 30 then
            return
        end
        for _, keyword in ipairs(nodes or {}) do
            local path = prefix and (prefix .. " > " .. keyword:getName()) or keyword:getName()
            if not query or path:lower():find(query, 1, true) then
                total = total + 1
                if #out < limit then
                    out[#out + 1] = path
                end
            end
            walk(keyword:getChildren(), path, depth + 1)
        end
    end
    walk(Photos.catalog():getKeywords(), nil, 0)
    return { total = total, keywords = out }
end

-- keywords.add {uuids|target, keywords = {"Places > France > Paris", ...}, dry_run?}
-- Missing keywords are created. Lightroom only lets a new keyword be used once the write
-- that created it has finished, so creation and tagging are two steps.
function Metadata.addKeywords(params, job)
    if type(params.keywords) ~= "table" or #params.keywords == 0 then
        error("keywords must be a non-empty list of keyword paths")
    end
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Add " .. #params.keywords .. " keywords to " .. #photos .. " photos", missing)
    end
    local catalog = Photos.catalog()
    local created = {}
    Batch.writeOnce("Safelight: Create Keywords", function()
        for _, path in ipairs(params.keywords) do
            local parts = splitPath(path)
            local parent = nil
            for _, name in ipairs(parts) do
                parent = catalog:createKeyword(name, {}, true, parent, true)
                if not parent then
                    break
                end
            end
            if #parts > 0 then
                created[#created + 1] = { path = path, keyword = parent }
            end
        end
    end)
    -- The objects returned inside the write gate are usable once it has finished; fall back
    -- to a lookup by path, and report keywords Lightroom refused to create.
    local keywords, notCreated = {}, {}
    for _, entry in ipairs(created) do
        local keyword = entry.keyword or findByPath(entry.path)
        if keyword then
            keywords[#keywords + 1] = keyword
        else
            notCreated[#notCreated + 1] = entry.path
        end
    end
    if #keywords == 0 then
        error("Lightroom refused to create these keywords: " .. table.concat(notCreated, ", "))
    end
    local result = Batch.write("Safelight: Add Keywords", photos, job, function(photo)
        for _, keyword in ipairs(keywords) do
            photo:addKeyword(keyword)
        end
    end)
    result.missing = missing
    result.keywords_not_created = notCreated
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

-- keywords.remove {uuids|target, keywords = {"Paris" or "Places > France > Paris"}, dry_run?}
-- A plain name matches that keyword anywhere in the hierarchy; a path matches exactly.
-- Keywords are removed from the photos, not deleted from the catalog.
function Metadata.removeKeywords(params, job)
    if type(params.keywords) ~= "table" or #params.keywords == 0 then
        error("keywords must be a non-empty list of keyword names or paths")
    end
    local keywords, notFound = {}, {}
    for _, request in ipairs(params.keywords) do
        local matches = {}
        if tostring(request):find(">", 1, true) then
            local keyword = findByPath(request)
            if keyword then
                matches[1] = { keyword = keyword }
            end
        else
            matches = findByName(tostring(request), {}, Photos.catalog():getKeywords(), nil)
        end
        if #matches == 0 then
            notFound[#notFound + 1] = tostring(request)
        end
        for _, match in ipairs(matches) do
            keywords[#keywords + 1] = match.keyword
        end
    end
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Remove " .. #keywords .. " keywords from " .. #photos .. " photos", missing)
    end
    local result = Batch.write("Safelight: Remove Keywords", photos, job, function(photo)
        for _, keyword in ipairs(keywords) do
            photo:removeKeyword(keyword)
        end
    end)
    result.missing = missing
    result.keywords_not_found = notFound
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

return Metadata
