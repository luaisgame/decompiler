game:GetService("TeleportService"):Block()
repeat
    task.wait()
until game:IsLoaded()

local lplr = game.Players.LocalPlayer
task.spawn(function()
    for _, obj in game:GetDescendants() do
        if obj.Name:find(lplr.Name) then
            obj.Name = obj.Name:gsub(obj.Name, "LOCAL_PLAYER")
        end

        if game.ReplicatedFirst:IsAncestorOf(obj) then
            task.spawn(function()
                for _, prop in getproperties(obj) do
                    if typeof(obj[prop]) == "RBXScriptSignal" then
                        for _, conn in getconnections(obj[prop]) do
                            conn:Disconnect()
                        end
                    end
                end
            end)
        end
    end
end)
task.wait()
task.wait()

task.spawn(function()
    local self = coroutine.running()
    local done = {}

    local function filterKill(f)
        if not f then return end
        for _, v in table.clone(f()) do
            if not done[v] then
                done[v] = true
                if type(v) == "thread" and v ~= self then
                    pcall(coroutine.close, v)
                elseif type(v) == "function" then
                    pcall(hookfunction, v, coroutine.yield)
                end
            end
        end
    end

    filterKill(debug.getregistry)
    filterKill(getallthreads)
    filterKill(getgc)
end)
task.wait()

print("hi")

-- bypasses for anti decompile

--[[
local timeout = false
task.delay(1, function()
    timeout = true
end)

-- ### laments anti cheat (works for forgotten)
task.spawn(function()
    local foundlamentanticheat = false
    local oldnames = {}

    repeat
        task.wait()
        for index, child: Instance in game:GetService("ReplicatedFirst"):GetChildren() do
            if oldnames[child] and oldnames[child] ~= child.Name then
                child.Enabled = false
                foundlamentanticheat = true
            end
            oldnames[child] = child.Name
        end
    until timeout or foundlamentanticheat
end)
task.spawn(function()
    local foundpdanticheat = false
    repeat
	task.wait()
	local name = "anti modules"
    local found = nil

    for _, child in game.ReplicatedStorage:GetChildren() do
        if child.Name:Find(name) then found = child break end
    end

    if found then
		found:Destroy()
        foundpdanticheat = true
	end
    until foundpdanticheat or timeout
end)

task.wait(1)
]]

local response = request({
    Url = "http://127.0.0.1:5000/decompile",
    Method = "POST",
    Headers = {
        ["Content-Type"] = "application/json"
    },
    Body = game:GetService("HttpService"):JSONEncode({
        status = "joined"
    })
})

local Params = {
 RepoURL = "https://raw.githubusercontent.com/luau/UniversalSynSaveInstance/main/",
 SSI = "saveinstance",
}

local synsaveinstance = loadstring(game:HttpGet(Params.RepoURL .. Params.SSI .. ".luau", true), Params.SSI)()
local Options = {
    FileName = "game",
    AvoidFileOverwrite = false,
    ShutdownWhenDone = false,
    KillAllScripts = false,

    mode = "full",
    DecompileUnions = true,
	SaveUnions = true,
    SafeMode = false,
    SaveBytecode = true,
    DecompileScripts = false,
    ShowStatus = true,
    SavePlayerCharacters = true,
	SavePlayers = true,
}

synsaveinstance(Options)

request({
    Url = "http://127.0.0.1:5000/decompile",
    Method = "POST",
    Headers = {
        ["Content-Type"] = "application/json"
    },
    Body = game:GetService("HttpService"):JSONEncode({
        status = "finished"
    })
})
