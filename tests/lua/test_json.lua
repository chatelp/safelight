-- Run with: lua5.1 tests/lua/test_json.lua (or luajit). Exits non-zero on failure.
package.path = "plugin/Safelight.lrplugin/?.lua;" .. package.path
local JSON = require "JSON"

local failures = 0
local function check(name, condition)
    if not condition then
        failures = failures + 1
        io.stderr:write("FAIL " .. name .. "\n")
    end
end

local decoded = JSON.decode('{"a":"caf\\u00e9 \\ud83d\\udcf7","n":[1,2.5,-3e2],"o":{"t":true,"z":null}}')
check("accents and emoji decode to UTF-8", decoded.a == "café 📷")
check("numbers", decoded.n[1] == 1 and decoded.n[2] == 2.5 and decoded.n[3] == -300)
check("booleans", decoded.o.t == true)
check("raw UTF-8 round trip", JSON.decode(JSON.encode({ s = "é📷" })).s == "é📷")
check("arrays stay arrays", JSON.encode({ 1, 2, 3 }) == "[1,2,3]")
check("escapes", JSON.decode(JSON.encode({ s = 'a "q"\n\\' })).s == 'a "q"\n\\')

if failures > 0 then
    os.exit(1)
end
print("Lua JSON tests passed")
