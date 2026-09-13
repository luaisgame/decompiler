game:GetService("TeleportService"):Block()
repeat
    task.wait()
until game:IsLoaded() and game.Players.LocalPlayer

local lplr = game.Players.LocalPlayer
local plrName = lplr.Name

--// protection from skidders (brings back a scrapped USSI feature)
task.spawn(function()
    local protectedName = "LOCAL_PLAYER-protected-by-iispeaklua/Decompile bot#9868"
    local services = { game:GetService("Players"), game:GetService("Workspace"), game:GetService("ReplicatedStorage") }

    local function checkobj(obj: Instance)
        local propsNames = { "DisplayName", "Name" }
        for _, propName in propsNames do
            pcall(function()
                obj[propName] = tostring(obj[propName]):gsub(plrName, protectedName)
            end)
        end
            
        for attribute, Value in obj:GetAttributes() do
            task.spawn(function()
                if Value:find(plrName) then
                    obj[attribute] = tostring(Value):gsub(plrName, protectedName)
                end
            end)
        end
    end

    for _, service in services do
        for _, descendant in service:GetDescendants() do
            task.spawn(checkobj, descendant)
        end
        service.DescendantAdded:Connect(checkobj)
    end
end)

task.wait()

-- bypasses for anti decompile

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

task.wait(0.1)

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
    KillAllScripts = true,

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