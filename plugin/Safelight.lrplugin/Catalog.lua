-- Catalog commands: find and look at photos, collections, virtual copies, rotation.
local LrApplication = import "LrApplication"
local LrApplicationView = import "LrApplicationView"
local LrStringUtils = import "LrStringUtils"
local LrTasks = import "LrTasks"

local Batch = require "Batch"
local Photos = require "Photos"
local Result = require "Result"

local Catalog = {}

local MAX_PAGE = 500

local function page(list, params)
    local offset = math.max(0, math.floor(tonumber(params.offset) or 0))
    local limit = math.max(1, math.min(math.floor(tonumber(params.limit) or 100), MAX_PAGE))
    local out = {}
    for index = offset + 1, math.min(offset + limit, #list) do
        out[#out + 1] = list[index]
    end
    return out, offset, limit
end

-- catalog.info {}: what Lightroom is showing right now.
function Catalog.info(_)
    local catalog = Photos.catalog()
    local active = catalog:getTargetPhoto()
    local version = LrApplication.versionTable()
    return {
        lightroom_version = LrApplication.versionString(),
        version = { major = version.major, minor = version.minor, revision = version.revision },
        catalog = catalog:getPath(),
        module = LrApplicationView.getCurrentModuleName(),
        active_photo = active and Photos.uuid(active) or nil,
        selected_count = #(catalog:getTargetPhotos() or {}),
    }
end

-- photos.get {uuids|target, limit?, offset?}: descriptions of photos.
function Catalog.getPhotos(params)
    local photos, missing = Photos.resolve(params, { allowEmpty = true })
    local selected, offset, limit = page(photos, params)
    return {
        total = #photos,
        offset = offset,
        limit = limit,
        photos = Photos.summaries(selected),
        missing = missing,
    }
end

-- photos.search {search, sort?, ascending?, limit?, offset?}: Lightroom's smart collection
-- search engine. `search` is one criterion, e.g. { criteria = "rating", operation = ">=",
-- value = 4 }, or { combine = "intersect" | "union" | "exclude", items = { ... } }.
-- Combined searches arrive as {combine = ..., items = {...}} because JSON cannot carry
-- Lua's mixed tables; Lightroom wants {combine = ..., [1] = ..., [2] = ...}.
local function toSearchDesc(desc)
    if type(desc) ~= "table" or type(desc.items) ~= "table" then
        return desc
    end
    local out = { combine = desc.combine or "intersect" }
    for index, item in ipairs(desc.items) do
        out[index] = toSearchDesc(item)
    end
    return out
end

function Catalog.search(params)
    if type(params.search) ~= "table" then
        error("search must be a search description object")
    end
    local found = Photos.catalog():findPhotos({
        searchDesc = toSearchDesc(params.search),
        sort = params.sort,
        ascending = params.ascending == true,
    }) or {}
    local selected, offset, limit = page(found, params)
    return {
        total = #found,
        offset = offset,
        limit = limit,
        photos = Photos.summaries(selected),
    }
end

-- photos.preview {uuids|target, size?}: JPEG preview rendered by Lightroom, base64.
function Catalog.preview(params)
    local photo = Photos.resolve(params)[1]
    local size = math.max(64, math.min(math.floor(tonumber(params.size) or 1024), 4096))
    local data, failure, done = nil, nil, false
    local request = photo:requestJpegThumbnail(size, size, function(jpeg, message)
        data = jpeg
        failure = message
        done = true
    end)
    local waited = 0
    while not done and waited < 60 do
        LrTasks.sleep(0.02)
        waited = waited + 0.02
    end
    request = nil -- held until the callback ran, as the SDK requires
    if not data then
        error("Lightroom could not render a preview: " .. tostring(failure or "timed out"))
    end
    return {
        uuid = Photos.uuid(photo),
        requested_size = size,
        mime_type = "image/jpeg",
        bytes = #data,
        data = LrStringUtils.encodeBase64(data),
    }
end

-- photos.rotate {uuids|target, direction = "left"|"right", dry_run?}
function Catalog.rotate(params, job)
    local direction = params.direction
    if direction ~= "left" and direction ~= "right" then
        error("direction must be left or right")
    end
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Rotate " .. #photos .. " photos " .. direction, missing)
    end
    local result = Batch.write("Safelight: Rotate", photos, job, function(photo)
        if direction == "left" then
            photo:rotateLeft()
        else
            photo:rotateRight()
        end
    end)
    result.missing = missing
    if result.cancelled then
        return Result.stoppedBatch(result)
    end
    return result
end

-- photos.create_virtual_copies {uuids|target, name?, dry_run?}: one copy per photo.
-- Lightroom creates copies of the selection, so the photos are selected first.
function Catalog.createVirtualCopies(params)
    local photos, missing = Photos.resolve(params)
    if params.dry_run then
        return Batch.plan(photos, "Create a virtual copy of " .. #photos .. " photos", missing)
    end
    local catalog = Photos.catalog()
    local others = {}
    for index = 2, #photos do
        others[#others + 1] = photos[index]
    end
    catalog:setSelectedPhotos(photos[1], others)
    catalog:createVirtualCopies(params.name)
    local copies = {}
    for _, photo in ipairs(catalog:getTargetPhotos() or {}) do
        copies[#copies + 1] = Photos.uuid(photo)
    end
    return { requested = #photos, copies = copies, missing = missing }
end

-- -- Collections ------------------------------------------------------------------------

local function describeCollections(parent, parentId, out, depth)
    if depth > 20 then
        return
    end
    for _, collection in ipairs(parent:getChildCollections() or {}) do
        out[#out + 1] = {
            id = collection.localIdentifier,
            name = collection:getName(),
            kind = collection:isSmartCollection() and "smart_collection" or "collection",
            parent_id = parentId,
        }
    end
    for _, set in ipairs(parent:getChildCollectionSets() or {}) do
        out[#out + 1] = {
            id = set.localIdentifier,
            name = set:getName(),
            kind = "collection_set",
            parent_id = parentId,
        }
        describeCollections(set, set.localIdentifier, out, depth + 1)
    end
end

local function collectionById(id, wantSet)
    local item = Photos.catalog():getCollectionByLocalIdentifier(tonumber(id))
    if not item then
        error("No collection with id " .. tostring(id))
    end
    local isSet = item:type() == "LrCollectionSet"
    if wantSet ~= nil and isSet ~= wantSet then
        error("Item " .. tostring(id) .. " is " .. (isSet and "a collection set" or "a collection"))
    end
    return item
end

-- collections.list {}: every collection and collection set, as a flat list with parents.
function Catalog.listCollections(_)
    local out = {}
    describeCollections(Photos.catalog(), nil, out, 0)
    return { count = #out, collections = out }
end

-- collections.photos {collection_id, limit?, offset?}
function Catalog.collectionPhotos(params)
    local collection = collectionById(params.collection_id, false)
    local photos = collection:getPhotos() or {}
    local selected, offset, limit = page(photos, params)
    return {
        collection = collection:getName(),
        total = #photos,
        offset = offset,
        limit = limit,
        photos = Photos.summaries(selected),
    }
end

-- collections.create {name, parent_id?, kind? = "collection"|"collection_set"|"smart_collection", search?}
function Catalog.createCollection(params)
    if type(params.name) ~= "string" or params.name == "" then
        error("name is required")
    end
    local kind = params.kind or "collection"
    local parent = params.parent_id and collectionById(params.parent_id, true) or nil
    local catalog = Photos.catalog()
    local created
    Batch.writeOnce("Safelight: Create Collection", function()
        if kind == "collection_set" then
            created = catalog:createCollectionSet(params.name, parent, true)
        elseif kind == "smart_collection" then
            if type(params.search) ~= "table" then
                error("a smart collection needs a search description")
            end
            created = catalog:createSmartCollection(params.name, toSearchDesc(params.search), parent, true)
        else
            created = catalog:createCollection(params.name, parent, true)
        end
    end)
    if not created then
        error("Lightroom did not create the collection")
    end
    return { id = created.localIdentifier, name = created:getName(), kind = kind }
end

local function changeMembership(params, job, add)
    local collection = collectionById(params.collection_id, false)
    if collection:isSmartCollection() then
        error("Photos cannot be added to or removed from a smart collection")
    end
    local photos, missing = Photos.resolve(params)
    local verb = add and "Add" or "Remove"
    if params.dry_run then
        return Batch.plan(photos, verb .. " " .. #photos .. " photos " .. (add and "to" or "from")
            .. " '" .. collection:getName() .. "'", missing)
    end
    job.progress(0, #photos, verb)
    Batch.writeOnce("Safelight: " .. verb .. " Photos", function()
        if add then
            collection:addPhotos(photos)
        else
            collection:removePhotos(photos)
        end
    end)
    job.progress(#photos, #photos, verb)
    return { collection = collection:getName(), updated = #photos, missing = missing }
end

-- collections.add {collection_id, uuids|target, dry_run?}
function Catalog.addToCollection(params, job)
    return changeMembership(params, job, true)
end

-- collections.remove_photos {collection_id, uuids|target, dry_run?}
function Catalog.removeFromCollection(params, job)
    return changeMembership(params, job, false)
end

-- collections.delete {collection_id, dry_run?}: deletes a collection or an empty set.
function Catalog.deleteCollection(params)
    local item = collectionById(params.collection_id)
    local name = item:getName()
    if params.dry_run then
        local count = 0
        if item:type() ~= "LrCollectionSet" then
            count = #(item:getPhotos() or {})
        end
        return {
            dry_run = true,
            affected = count,
            summary = "Delete '" .. name .. "' (the photos stay in the catalog)",
        }
    end
    Batch.writeOnce("Safelight: Delete Collection", function()
        item:delete()
    end)
    return { deleted = name }
end

return Catalog
