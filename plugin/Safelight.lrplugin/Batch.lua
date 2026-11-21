-- Applies a change to many photos: chunk by chunk inside catalog write gates, reporting
-- progress and stopping between chunks when the job is cancelled. Each chunk is one
-- Lightroom undo step named after the action.
local LrTasks = import "LrTasks"

local Photos = require "Photos"

local Batch = {}

local CHUNK_SIZE = 50
local WRITE_TIMEOUT = { timeout = 60 }

-- fn(photo) changes one photo and may return a per-photo result.
-- Returns { requested, updated, failures = { {uuid, error} }, cancelled }.
function Batch.write(actionName, photos, job, fn, chunkSize)
    local catalog = Photos.catalog()
    chunkSize = chunkSize or CHUNK_SIZE
    local total = #photos
    local updated, failures, cancelled = 0, {}, false
    local first = 1
    while first <= total do
        if job.progress(updated + #failures, total, actionName) then
            cancelled = true
            break
        end
        local last = math.min(first + chunkSize - 1, total)
        catalog:withWriteAccessDo(actionName, function()
            for index = first, last do
                local photo = photos[index]
                local ok, err = LrTasks.pcall(fn, photo)
                if ok then
                    updated = updated + 1
                else
                    failures[#failures + 1] = { uuid = Photos.uuid(photo), error = tostring(err) }
                end
            end
        end, WRITE_TIMEOUT)
        first = last + 1
    end
    job.progress(updated + #failures, total, actionName)
    return {
        requested = total,
        updated = updated,
        failures = failures,
        cancelled = cancelled,
    }
end

-- Runs fn() once inside a write gate.
function Batch.writeOnce(actionName, fn)
    local result
    Photos.catalog():withWriteAccessDo(actionName, function()
        result = fn()
    end, WRITE_TIMEOUT)
    return result
end

-- Standard answer to a dry run: how many photos would change.
function Batch.plan(photos, summary, missing)
    return {
        dry_run = true,
        affected = #photos,
        summary = summary,
        missing = missing,
    }
end

return Batch
