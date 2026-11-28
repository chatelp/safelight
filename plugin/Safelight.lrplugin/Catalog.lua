-- Catalog commands: find and look at photos, collections, virtual copies, rotation.
local LrApplication = import "LrApplication"
local LrApplicationView = import "LrApplicationView"
local LrDate = import "LrDate"
local LrPrefs = import "LrPrefs"
local LrStringUtils = import "LrStringUtils"
local LrTasks = import "LrTasks"

local Batch = require "Batch"
local DevelopModule = require "DevelopModule"
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

-- Safelight's smart collections, filled by Lightroom from Safelight's own fields.
local function field(name)
    return "sdktext:" .. _PLUGIN.id .. "." .. name
end

local function notValidated(criterion)
    return {
        combine = "intersect",
        criterion,
        { combine = "exclude", { criteria = field("validated"), operation = "==", value = "yes" } },
    }
end

local SMART = {
    to_check = function()
        return notValidated({ criteria = field("geometry"), operation = "==", value = "safelight" })
    end,
    by_hand = function()
        return notValidated({ criteria = field("geometry"), operation = "==", value = "by_hand" })
    end,
    validated = function()
        return { combine = "intersect", { criteria = field("validated"), operation = "==", value = "yes" } }
    end,
}

-- Bump when SMART changes, so that existing collections get the new rules.
local SMART_VERSION = 1

local function existing(id, wantSet)
    if not id then
        return nil
    end
    local ok, item = LrTasks.pcall(collectionById, id, wantSet)
    if ok and (wantSet or item:isSmartCollection()) then
        return item
    end
    return nil
end

-- The ids of Safelight's set and smart collections in this catalog (see safelight.collections).
local function knownCollections(catalog)
    local all = LrPrefs.prefsForPlugin().collections or {}
    return all[catalog:getPath()] or {}
end

-- safelight.photos {state = "to_check"|"by_hand"|"validated", limit?, offset?}: the photos
-- in one of Safelight's states, found with the rules of its smart collections, so the app
-- and Lightroom always show the same photos.
function Catalog.safelightPhotos(params)
    local rule = SMART[params.state]
    if not rule then
        error("state must be to_check, by_hand or validated")
    end
    local found = Photos.catalog():findPhotos({ searchDesc = rule() }) or {}
    local selected, offset, limit = page(found, params)
    return {
        state = params.state,
        total = #found,
        offset = offset,
        limit = limit,
        photos = Photos.summaries(selected),
    }
end

-- Selects `photos` in the current source and waits (at most 2 s) until Lightroom shows the
-- first one; returns how many photos are selected then, 0 if it did not.
local function selectIn(catalog, photos)
    local wanted = Photos.uuid(photos[1])
    local deadline = LrDate.currentTime() + 2
    catalog:setSelectedPhotos(photos[1], photos)
    while true do
        local target = catalog:getTargetPhoto()
        if target and Photos.uuid(target) == wanted then
            return #(catalog:getTargetPhotos() or {})
        end
        if LrDate.currentTime() > deadline then
            return 0
        end
        LrTasks.sleep(0.1)
        catalog:setSelectedPhotos(photos[1], photos) -- the source may have just changed
    end
end

-- photos.show {uuids|target, state?}: selects the photos in Lightroom's Library, inside
-- Safelight's smart collection for `state` when it exists and holds them, otherwise in
-- All Photographs. Changes what Lightroom shows, not the catalog. Refused while a command
-- works in the Develop module, which acts on the photo Lightroom shows.
function Catalog.show(params)
    if DevelopModule.isBusy() then
        error("Lightroom is changing a photo for an AI client. Try again in a moment.")
    end
    local photos, missing = Photos.resolve(params)
    local catalog = Photos.catalog()
    LrApplicationView.switchToModule("library")
    local collection = params.state and SMART[params.state]
        and existing(knownCollections(catalog)[params.state], false)
    if collection then
        catalog:setActiveSources({ collection })
        local shown = selectIn(catalog, photos)
        if shown > 0 then
            return { shown = shown, source = collection:getName(), missing = missing }
        end
    end
    catalog:setActiveSources(catalog.kAllPhotos)
    local shown = selectIn(catalog, photos)
    if shown == 0 then
        error("Lightroom did not show these photos. A Library filter may hide them: turn it off and try again.")
    end
    return { shown = shown, source = "All Photographs", missing = missing }
end

-- safelight.collections {set, names = {to_check, by_hand, validated}}: creates Safelight's
-- collection set and smart collections, or renames them. Found again by id (kept in the
-- plugin's preferences, per catalog), so the user may move them; a deleted one comes back.
function Catalog.safelightCollections(params)
    if type(params.set) ~= "string" or params.set == "" or type(params.names) ~= "table" then
        error("set and names are required")
    end
    local catalog = Photos.catalog()
    local prefs = LrPrefs.prefsForPlugin()
    local all = prefs.collections or {}
    local known = all[catalog:getPath()] or {}
    local set = existing(known.set, true) -- looked up before the write gate
    local found, wanted = {}, {}
    local rulesChanged = known.version ~= SMART_VERSION
    local work = not set or set:getName() ~= params.set
    for key, _ in pairs(SMART) do
        local name = params.names[key]
        if type(name) == "string" and name ~= "" then
            wanted[key] = name
            found[key] = existing(known[key], false)
            work = work or not found[key] or found[key]:getName() ~= name or rulesChanged
        end
    end
    if work then
        Batch.writeOnce("Safelight: Collections", function()
            if not set then
                set = catalog:createCollectionSet(params.set, nil, true)
            elseif set:getName() ~= params.set then
                set:setName(params.set)
            end
            for key, name in pairs(wanted) do
                local collection = found[key]
                if not collection then
                    found[key] = catalog:createSmartCollection(name, SMART[key](), set, true)
                else
                    if collection:getName() ~= name then
                        collection:setName(name)
                    end
                    if rulesChanged then
                        collection:setSearchDescription(SMART[key]())
                    end
                end
            end
        end)
    end
    -- New items have their ids once the write gate is over.
    local ids = { set = set and set.localIdentifier, version = SMART_VERSION }
    for key, _ in pairs(wanted) do
        ids[key] = found[key] and found[key].localIdentifier
    end
    all[catalog:getPath()] = ids
    prefs.collections = all
    return { changed = work, ids = ids }
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
