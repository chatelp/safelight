local LrDialogs = import "LrDialogs"

local Logger = require "Logger"
local Transport = require "Transport"
local Version = require "Version"

local status = Transport.status()
local connection
if status.connected then
    connection = "Connected to the Safelight hub."
else
    connection = "Not connected to the Safelight hub (" .. tostring(status.last_error) .. ").\n"
        .. "The hub starts automatically when an AI client uses Safelight."
end

LrDialogs.message(
    "Safelight " .. Version.string,
    connection
        .. "\n\nCommands run: " .. tostring(status.commands_run)
        .. "\nHub settings: " .. tostring(status.hub_config)
        .. "\nLog file: " .. tostring(Logger.getPath()),
    "info"
)
