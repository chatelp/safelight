local Logger = require "Logger"
local Transport = require "Transport"
local Version = require "Version"

_G.Safelight = {
    version = Version.string,
    startedAt = os.time(),
    logFile = Logger.getPath(),
}

Logger.info("Safelight " .. Version.string .. " loaded")
Transport.start()
