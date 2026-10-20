-- Timetable probe for Train Sim World 7, as a UE4SS Lua mod.
-- Writes the game's timetable, service, dispatcher and formation types (classes, structs and enums, with each
-- property's type and each function's parameters) to timetable_types.txt next to this mod's Scripts folder.
-- It runs once, 20 seconds after the game starts. Press Ctrl+Shift+T to run it again later, e.g. once a timetable is
-- loaded: that also lists the live timetable objects (dispatchers, services) in timetable_live.txt.
-- Progress goes to UE4SS.log, lines starting [TimetableProbe].
-- Reading some types crashes the game (a native fault pcall can't catch), so the type being read is kept in
-- probe_progress.txt: if the game dies, the next run adds it to probe_skip.txt and leaves it out.
-- Every ScriptStruct crashes when read from Lua in TSW7, so structs come from UE4SS's own C++ header generator
-- instead (GenerateSDK, into ue4ss\CXXHeaderDump), run after the Lua dump.

local KEYWORDS = { "Timetable", "Dispatcher", "YardManager", "Service", "Formation", "Spawn", "Scenario" }
local TYPE_KINDS = { Class = true, Enum = true, UserDefinedEnum = true }
local KIND_ORDER = { Class = 1, Enum = 2, UserDefinedEnum = 2 }
local LIVE_CLASSES = { "DispatcherAIV3", "YardManagerAIV2", "RouteTimetableDefinition", "RouteTimetableDataTrack",
                       "RouteTimetable", "SandboxRouteTimetable" }
local START_DELAY_MS = 20000

local OUT_DIR = debug.getinfo(1, "S").source:match("^@(.*[\\/])Scripts[\\/]") or ""
local PROGRESS_PATH = OUT_DIR .. "probe_progress.txt"
local SKIP_PATH = OUT_DIR .. "probe_skip.txt"

local function log(fmt, ...)
    print("[TimetableProbe] " .. string.format(fmt, ...) .. "\n")
end

local function try(f, fallback)
    local ok, value = pcall(f)
    if ok and value ~= nil then return value end
    return fallback
end

local function name_of(obj)
    return try(function() return obj:GetFName():ToString() end, "?")
end

local function matches(name)
    for _, keyword in ipairs(KEYWORDS) do
        if name:find(keyword, 1, true) then return true end
    end
    return false
end

-- "ArrayProperty<StructProperty<RouteTimetableServiceDefinition>>" and so on
local function type_of(prop)
    local kind = try(function() return prop:GetClass():GetFName():ToString() end, "?")
    local detail
    if kind == "ObjectProperty" or kind == "SoftObjectProperty" or kind == "WeakObjectProperty"
        or kind == "ClassProperty" or kind == "SoftClassProperty" or kind == "LazyObjectProperty" then
        detail = try(function() return name_of(prop:GetPropertyClass()) end)
    elseif kind == "StructProperty" then
        detail = try(function() return name_of(prop:GetStruct()) end)
    elseif kind == "ArrayProperty" or kind == "SetProperty" then
        detail = try(function() return type_of(prop:GetInner()) end)
        if not detail then detail = try(function() return type_of(prop:GetElementProp()) end) end
    elseif kind == "MapProperty" then
        local key = try(function() return type_of(prop:GetKeyProp()) end, "?")
        local value = try(function() return type_of(prop:GetValueProp()) end, "?")
        detail = key .. ", " .. value
    elseif kind == "EnumProperty" or kind == "ByteProperty" then
        detail = try(function() return name_of(prop:GetEnum()) end)
    end
    if detail then return kind .. "<" .. detail .. ">" end
    return kind
end

local function write_struct(out, obj, kind)
    local super = try(function() return name_of(obj:GetSuperStruct()) end)
    out:write(string.format("\n%s %s%s\n", kind, try(function() return obj:GetFullName() end, name_of(obj)),
        super and super ~= "?" and (" : " .. super) or ""))
    try(function()
        obj:ForEachProperty(function(prop)
            out:write(string.format("    %-60s %s\n", type_of(prop), name_of(prop)))
        end)
        return true
    end)
    if kind == "Class" then
        try(function()
            obj:ForEachFunction(function(fn)
                local params = {}
                try(function()
                    fn:ForEachProperty(function(p)
                        params[#params + 1] = type_of(p) .. " " .. name_of(p)
                    end)
                    return true
                end)
                out:write(string.format("    fn %s(%s)\n", name_of(fn), table.concat(params, ", ")))
            end)
            return true
        end)
    end
end

local function write_enum(out, obj)
    out:write(string.format("\nEnum %s\n", try(function() return obj:GetFullName() end, name_of(obj))))
    try(function()
        obj:ForEachName(function(name, value)
            out:write(string.format("    %s = %s\n", name:ToString(), tostring(value)))
        end)
        return true
    end)
end

local function read_file(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local text = f:read("a")
    f:close()
    return text
end

local function write_file(path, text, mode)
    local f = assert(io.open(path, mode or "w"))
    f:write(text)
    f:close()
end

-- Types that crashed an earlier run, including the one in progress when the game last died
local function load_skip_list()
    local crashed = read_file(PROGRESS_PATH)
    if crashed and crashed ~= "" then
        write_file(SKIP_PATH, crashed .. "\n", "a")
        log("last run died reading %s; skipping it from now on", crashed)
    end
    local skip = {}
    for line in (read_file(SKIP_PATH) or ""):gmatch("[^\r\n]+") do skip[line] = true end
    return skip
end

local function dump_types()
    local skip = load_skip_list()
    local found = {}
    ForEachUObject(function(obj)
        local kind = try(function() return obj:GetClass():GetFName():ToString() end)
        if kind and TYPE_KINDS[kind] then
            local name = name_of(obj)
            if matches(name) then found[#found + 1] = { kind = kind, obj = obj, name = name } end
        end
    end)
    table.sort(found, function(a, b)
        if KIND_ORDER[a.kind] ~= KIND_ORDER[b.kind] then return KIND_ORDER[a.kind] < KIND_ORDER[b.kind] end
        return a.name < b.name
    end)

    local path = OUT_DIR .. "timetable_types.txt"
    local out = assert(io.open(path, "w"))
    out:write(string.format("-- %d types whose names contain: %s\n", #found, table.concat(KEYWORDS, ", ")))
    local skipped = 0
    for _, t in ipairs(found) do
        local id = t.kind .. " " .. t.name
        if skip[id] then
            out:write("\n" .. id .. " -- skipped, crashed an earlier run\n")
            skipped = skipped + 1
        else
            write_file(PROGRESS_PATH, id)
            if t.kind == "Enum" or t.kind == "UserDefinedEnum" then
                write_enum(out, t.obj)
            else
                write_struct(out, t.obj, t.kind)
            end
            out:flush()
        end
    end
    out:close()
    write_file(PROGRESS_PATH, "")
    log("wrote %d types to %s (%d skipped)", #found - skipped, path, skipped)
end

local function dump_live()
    local path = OUT_DIR .. "timetable_live.txt"
    local out = assert(io.open(path, "w"))
    for _, class_name in ipairs(LIVE_CLASSES) do
        local all = FindAllOf(class_name) or {}
        out:write(string.format("\n%s: %d live\n", class_name, #all))
        for _, obj in ipairs(all) do
            out:write("    " .. try(function() return obj:GetFullName() end, "?") .. "\n")
        end
    end
    out:close()
    log("wrote live objects to %s", path)
end

local function run(with_live, with_headers)
    ExecuteInGameThread(function()
        local ok, err = pcall(dump_types)
        if not ok then log("type dump failed: %s", tostring(err)) end
        if with_live then
            ok, err = pcall(dump_live)
            if not ok then log("live dump failed: %s", tostring(err)) end
        end
        if with_headers then
            log("generating C++ headers")
            ok, err = pcall(GenerateSDK)
            log(ok and "headers done" or ("header generation failed: " .. tostring(err)))
        end
    end)
end

-- Which content paks the game's DLC manager accepted and rejected, and whether mods are allowed
local CONTENT_SETS = { "FailedPakFiles", "CachedContentDLCNames" }
-- Original WCMLTT has bUseTSW4Scoring = true; the TimetableShift test mods set it to false
local MARKER_ASSETS = { "/AABS_TrentValley_Gameplay/Timetable/WCMLTT.WCMLTT" }

local function dump_content_status(label, load_markers)
    local out = assert(io.open(OUT_DIR .. "content_status.txt", "a"))
    out:write(string.format("\n== %s (%s)\n", label, os.date("%H:%M:%S")))
    local settings = FindFirstOf("TS2GameUserSettings")
    out:write("bAllowMods = " .. tostring(try(function() return settings.bAllowMods end, "?")) .. "\n")
    local dlc = FindFirstOf("DLCManagementSystem")
    out:write("DLCManagementSystem: " .. (dlc and try(function() return dlc:GetFullName() end, "?") or "none") .. "\n")
    if dlc then
        for _, prop in ipairs(CONTENT_SETS) do
            local names = {}
            local ok, err = pcall(function()
                dlc[prop]:ForEach(function(a, b)
                    local elem = b or a
                    names[#names + 1] = try(function() return elem:get():ToString() end)
                        or try(function() return elem:ToString() end) or tostring(elem)
                end)
            end)
            table.sort(names)
            out:write(string.format("%s: %s\n", prop, ok and (#names .. " " .. table.concat(names, " | "))
                or ("unreadable: " .. tostring(err))))
        end
        local count = try(function() return #dlc.CachedContentList end, "?")
        out:write("CachedContentList entries: " .. tostring(count) .. "\n")
    end
    -- A patch mod flips this flag in the timetable it overrides, so its value says whose copy the game loaded.
    -- Only on the last snapshot: loading it earlier, before mods mount, would keep the original in memory.
    for _, path in ipairs(load_markers and MARKER_ASSETS or {}) do
        local asset = try(function() return StaticFindObject(path) end)
        local how = "already loaded"
        if not asset or not try(function() return asset:IsValid() end, false) then
            asset = try(function() return LoadAsset(path) end)
            how = "loaded now"
        end
        local value = asset and try(function() return tostring(asset.bUseTSW4Scoring) end, "?") or "not found"
        out:write(string.format("%s bUseTSW4Scoring = %s (%s)\n", path, value, how))
    end
    out:close()
    log("content status (%s) written", label)
end

local function content_status_later(seconds, load_markers)
    ExecuteWithDelay(seconds * 1000, function()
        ExecuteInGameThread(function()
            local ok, err = pcall(dump_content_status, seconds .. " s", load_markers)
            if not ok then log("content status failed: %s", tostring(err)) end
        end)
    end)
end

ExecuteWithDelay(START_DELAY_MS, function() run(false, true) end)
content_status_later(30, false)
content_status_later(60, false)

-- Watch for the game loading a marked timetable itself (e.g. when you open the route's timetable) and log which
-- copy it got: the original or a patch mod's.
local MARKER_NAMES = { WCMLTT = true, WCMLTT_Today = true, WCMLTT_TSWS_Test = true }
local seen_markers = {}

local function watch_markers()
    local out
    for _, tt in ipairs(FindAllOf("RouteTimetableDefinition") or {}) do
        local name = name_of(tt)
        if MARKER_NAMES[name] or name:find("TSWS", 1, true) then
            local value = try(function() return tt:GetPropertyValue("bUseTSW4Scoring") end)
            if value == nil then value = try(function() return tt.bUseTSW4Scoring end) end
            local line = string.format("%s %s bUseTSW4Scoring = %s (%s)", name,
                try(function() return tt:GetFullName() end, "?"), tostring(value), type(value))
            if seen_markers[name] ~= line then
                seen_markers[name] = line
                out = out or assert(io.open(OUT_DIR .. "content_status.txt", "a"))
                out:write(os.date("%H:%M:%S ") .. line .. "\n")
                log("%s", line)
            end
        end
    end
    if out then out:close() end
end

LoopAsync(10000, function()
    ExecuteInGameThread(function()
        local ok, err = pcall(watch_markers)
        if not ok then log("marker watch failed: %s", tostring(err)) end
    end)
    return false
end)
RegisterKeyBind(Key.T, { ModifierKey.CONTROL, ModifierKey.SHIFT }, function() run(true, false) end)
log("loaded; first dump in %d s, Ctrl+Shift+T to dump again with live objects", START_DELAY_MS // 1000)
