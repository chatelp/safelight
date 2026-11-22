-- LrDevelopController only acts on the photo shown in the Develop module. This helper
-- selects a photo, switches to Develop and waits until Lightroom has loaded it.
local LrApplicationView = import "LrApplicationView"
local LrDate = import "LrDate"
local LrDevelopController = import "LrDevelopController"
local LrTasks = import "LrTasks"

local Logger = require "Logger"
local Photos = require "Photos"

local DevelopModule = {}

local LOAD_TIMEOUT_S = 10

local function activeUuid()
    local photo = Photos.catalog():getTargetPhoto()
    return photo and Photos.uuid(photo) or nil
end

-- Shows `photo` in the Develop module, then runs fn() and returns its result.
-- The user's view stays in Develop on that photo afterwards.
function DevelopModule.withPhoto(photo, fn)
    local wanted = Photos.uuid(photo)
    if activeUuid() ~= wanted then
        Photos.catalog():setSelectedPhotos(photo, {})
    end
    if LrApplicationView.getCurrentModuleName() ~= "develop" then
        Logger.debug("Switching to Develop for " .. wanted)
        LrApplicationView.switchToModule("develop")
    end
    -- Wall-clock deadline: LrTasks.sleep can last much longer than asked.
    local deadline = LrDate.currentTime() + LOAD_TIMEOUT_S
    while LrApplicationView.getCurrentModuleName() ~= "develop" or activeUuid() ~= wanted do
        if LrDate.currentTime() > deadline then
            error("Lightroom did not open this photo in the Develop module in time (module: "
                .. tostring(LrApplicationView.getCurrentModuleName()) .. ")")
        end
        LrTasks.sleep(0.1)
    end
    Logger.debug("Develop shows " .. wanted)
    LrTasks.sleep(0.3) -- let Develop finish loading the photo's settings
    return fn()
end

-- Masking calls also need the Masking panel open.
function DevelopModule.withMasking(photo, fn)
    return DevelopModule.withPhoto(photo, function()
        if LrDevelopController.getSelectedTool() ~= "masking" then
            LrDevelopController.selectTool("masking")
            LrTasks.sleep(0.3)
        end
        return fn()
    end)
end

return DevelopModule
