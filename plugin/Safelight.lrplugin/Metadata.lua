-- Metadata and keyword commands.
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
        changes[#changes + 1] = { key = field.key, value = value }
    end
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Change metadata of " .. #photos .. " photos", missing)
    end
    local result = Batch.write("Safelight: Metadata", photos, job, function(photo)
        for _, change in ipairs(changes) do
            photo:setRawMetadata(change.key, change.value)
        end
    end)
    result.missing = missing
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
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
    local paths = {}
    for _, path in ipairs(params.keywords) do
        if #splitPath(path) > 0 then
            paths[#paths + 1] = path
        end
    end
    Batch.writeOnce("Safelight: Create Keywords", function()
        for _, path in ipairs(paths) do
            local parent = nil
            for _, name in ipairs(splitPath(path)) do
                parent = catalog:createKeyword(name, {}, true, parent, true)
            end
        end
    end)
    local keywords = {}
    for _, path in ipairs(paths) do
        local keyword = findByPath(path)
        if not keyword then
            error("Keyword could not be created: " .. path)
        end
        keywords[#keywords + 1] = keyword
    end
    local result = Batch.write("Safelight: Add Keywords", photos, job, function(photo)
        for _, keyword in ipairs(keywords) do
            photo:addKeyword(keyword)
        end
    end)
    result.missing = missing
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
