-- Reads the plugin version from Info.lua so it is declared in one place only.
local Version = {}

local ok, info = pcall(require, "Info")
if ok and type(info) == "table" and type(info.VERSION) == "table" then
    Version.string = info.VERSION.display
        or string.format("%d.%d.%d", info.VERSION.major or 0, info.VERSION.minor or 0, info.VERSION.revision or 0)
else
    Version.string = "unknown"
end

return Version
