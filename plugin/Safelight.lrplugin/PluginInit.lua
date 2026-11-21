local Logger = require "Logger"
local Version = require "Version"

_G.Safelight = {
    version = Version.string,
    startedAt = os.time(),
    logFile = Logger.getPath(),
}

-- The connection to the Safelight hub is added with the new transport (roadmap lot L1).
Logger.info("Safelight " .. Version.string .. " loaded")
