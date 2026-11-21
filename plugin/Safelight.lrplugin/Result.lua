-- Special results a command handler can return.
local Result = {}

-- A handler that stops because the user cancelled returns Result.cancelled(message).
function Result.cancelled(message)
    return { __safelightCancelled = true, message = message or "Cancelled" }
end

function Result.isCancelled(value)
    return type(value) == "table" and value.__safelightCancelled == true
end

-- Cancellation answer for a batch that stopped part way.
function Result.stoppedBatch(result)
    return Result.cancelled(
        "Stopped after " .. tostring(result.updated) .. " of " .. tostring(result.requested) .. " photos"
    )
end

return Result
