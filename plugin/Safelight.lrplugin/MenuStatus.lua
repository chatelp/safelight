local LrDialogs = import "LrDialogs"

local Logger = require "Logger"
local Version = require "Version"

LrDialogs.message(
    "Safelight " .. Version.string,
    "This development build does not connect to the Safelight hub yet.\n\nLog file: " .. tostring(Logger.getPath()),
    "info"
)
