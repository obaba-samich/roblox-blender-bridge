--[[ Roblox base-material calibration: how bright each built-in material really is.

Run in a NEW Baseplate place (File > New), Studio quality Level 21.
View > Command Bar, paste this whole file, press Enter. Screenshot every stage.
Cycles 3 stages (part Color white / pink 109,70,70 / grey 128) x 3 rounds, then removes
everything and restores Lighting. Each cell is one material on a big plate facing the camera,
lit only by Ambient (white), so the screenshot shows its average colour.
Read with tools/fit_materials.py. Same chart layout as calibrate_lighting.lua.
]]

local Lighting = game:GetService("Lighting")
local CoreGui = game:GetService("CoreGui")
local Selection = game:GetService("Selection")

local ORIGIN = Vector3.new(0, 3000, 0)
local FOV = 70
local COLS, ROWS = 12, 6
local GRID = { x0 = 0.06, x1 = 0.94, y0 = 0.12, y1 = 0.90 }
local DIST = 70
local HOLD = 4
local CYCLES = 3
local EC = -1 -- keeps white plates out of the tonemapper's flat top

local MATERIALS = {
	"SmoothPlastic", "Plastic", "Wood", "WoodPlanks", "Brick", "Cobblestone", "Concrete", "Metal",
	"CorrodedMetal", "DiamondPlate", "Foil", "Granite", "Marble", "Slate", "Sand", "Fabric",
	"Glass", "Ice", "Grass", "Pebble", "Asphalt", "Basalt", "CrackedLava", "Glacier",
	"Ground", "LeafyGrass", "Limestone", "Mud", "Pavement", "Rock", "Salt", "Sandstone",
	"Snow", "Cardboard", "Carpet", "CeramicTiles", "ClayRoofTiles", "RoofShingles", "Leather", "Plaster",
	"Rubber", "Neon", "ForceField",
}
local COLOURS = { Color3.fromRGB(255, 255, 255), Color3.fromRGB(109, 70, 70), Color3.fromRGB(128, 128, 128) }

local saved = {}
local LPROPS = {
	"Ambient", "OutdoorAmbient", "Brightness", "ColorShift_Top", "ColorShift_Bottom", "EnvironmentDiffuseScale",
	"EnvironmentSpecularScale", "ExposureCompensation", "GlobalShadows", "ClockTime", "FogEnd", "FogStart",
}
for _, p in ipairs(LPROPS) do
	pcall(function()
		saved[p] = (Lighting :: any)[p]
	end)
end
local function set(p, v)
	pcall(function()
		(Lighting :: any)[p] = v
	end)
end
local parked = {}
for _, ch in ipairs(Lighting:GetChildren()) do
	if ch:IsA("Sky") or ch:IsA("Atmosphere") or ch:IsA("PostEffect") or ch:IsA("Clouds") then
		table.insert(parked, ch)
		ch.Parent = nil
	end
end
set("Brightness", 0)
set("ClockTime", 0)
set("ColorShift_Top", Color3.new(0, 0, 0))
set("ColorShift_Bottom", Color3.new(0, 0, 0))
set("EnvironmentDiffuseScale", 0)
set("EnvironmentSpecularScale", 0)
set("GlobalShadows", false)
set("Ambient", Color3.new(1, 1, 1))
set("OutdoorAmbient", Color3.new(1, 1, 1))
set("ExposureCompensation", EC)
set("FogStart", 100000)
set("FogEnd", 100000)

local cam = workspace.CurrentCamera
local savedCam = { cf = cam.CFrame, fov = cam.FieldOfView, type = cam.CameraType }
local camCF = CFrame.lookAt(ORIGIN, ORIGIN + Vector3.new(0, 0, -1))
local function pinCamera()
	cam.CameraType = Enum.CameraType.Scriptable
	cam.FieldOfView = FOV
	cam.CFrame = camCF
end
pinCamera()
task.wait(0.2)

local folder = Instance.new("Folder")
folder.Name = "RBX_MaterialCalibration"
folder.Parent = workspace
local plates = {}
local vp = cam.ViewportSize
for i, name in ipairs(MATERIALS) do
	local k = i - 1
	local c, r = k % COLS, math.floor(k / COLS)
	local fx = GRID.x0 + (c + 0.5) / COLS * (GRID.x1 - GRID.x0)
	local fy = GRID.y0 + (r + 0.5) / ROWS * (GRID.y1 - GRID.y0)
	local ray = cam:ViewportPointToRay(fx * vp.X, fy * vp.Y, 0)
	local cellPx = (GRID.x1 - GRID.x0) / COLS * vp.X
	local w = DIST * 2 * math.tan(math.rad(FOV) / 2) * cellPx / vp.Y * 0.85
	local p = Instance.new("Part")
	p.Anchored, p.CanCollide, p.CanQuery, p.CanTouch, p.CastShadow = true, false, false, false, false
	local ok = pcall(function()
		p.Material = (Enum.Material :: any)[name]
	end)
	if not ok then
		p.Material = Enum.Material.SmoothPlastic
	end
	p.Size = Vector3.new(w, w, 1)
	local pos = ray.Origin + ray.Direction.Unit * DIST
	p.CFrame = CFrame.lookAt(pos, ray.Origin)
	p.Parent = folder
	table.insert(plates, p)
end

local gui = Instance.new("ScreenGui")
gui.Name = "RBX_MaterialCalibration"
gui.IgnoreGuiInset = true
gui.DisplayOrder = 1000
gui.Parent = CoreGui
local function square(fx, fy, col)
	local f = Instance.new("Frame")
	f.AnchorPoint = Vector2.new(0.5, 0.5)
	f.Position = UDim2.fromScale(fx, fy)
	f.Size = UDim2.fromOffset(18, 18)
	f.BorderSizePixel = 0
	f.BackgroundColor3 = col
	f.Parent = gui
	return f
end
local MAG = Color3.fromRGB(255, 0, 255)
square(0.02, 0.03, MAG)
square(0.98, 0.03, MAG)
square(0.02, 0.97, MAG)
square(0.98, 0.97, MAG)
local bits = {}
for i = 1, 4 do
	bits[i] = square(0.40 + (i - 1) * 0.0667, 0.05, Color3.new(0, 0, 0))
end
local label = Instance.new("TextLabel")
label.AnchorPoint = Vector2.new(0.5, 1)
label.Position = UDim2.fromScale(0.5, 0.995)
label.Size = UDim2.fromOffset(760, 26)
label.BackgroundColor3 = Color3.new(0, 0, 0)
label.TextColor3 = Color3.new(1, 1, 1)
label.TextSize = 18
label.Font = Enum.Font.SourceSansBold
label.Parent = gui

-- stage codes 13-15 (bits) so shots can't be mixed up with the other charts
local stage, cycle, finished = 1, 1, false
local function show()
	for _, p in ipairs(plates) do
		p.Color = COLOURS[stage]
	end
	local code = stage + 12
	for i = 1, 4 do
		bits[i].BackgroundColor3 = (bit32.band(code, bit32.lshift(1, i - 1)) ~= 0) and Color3.new(1, 1, 1)
			or Color3.new(0, 0, 0)
	end
	label.Text = ("Material stage %d / %d (round %d / %d) - screenshot now"):format(stage, #COLOURS, cycle, CYCLES)
end
show()
local ok, err = pcall(function()
	while not finished do
		for _ = 1, HOLD * 10 do
			pinCamera()
			task.wait(0.1)
		end
		if stage < #COLOURS then
			stage += 1
		elseif cycle < CYCLES then
			stage, cycle = 1, cycle + 1
		else
			finished = true
		end
		if not finished then
			show()
		end
	end
end)
folder:Destroy()
gui:Destroy()
for p, v in pairs(saved) do
	set(p, v)
end
for _, ch in ipairs(parked) do
	ch.Parent = Lighting
end
cam.CameraType = savedCam.type
cam.FieldOfView = savedCam.fov
cam.CFrame = savedCam.cf
print(ok and "Material calibration finished; Lighting restored." or ("Calibration stopped: " .. tostring(err)))
