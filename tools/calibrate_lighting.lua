--[[ Roblox lighting calibration chart (for the Unity "Roblox Lit" match)

Run in a NEW Baseplate place (File > New), not your game:
  View > Command Bar, paste this whole file, press Enter.
The viewport shows a chart of patches in 7 lighting stages (the stage number
is shown at the bottom). Take one screenshot of each stage (any size; Studio's
window/panels around it are fine). Stages change by themselves every HOLD
seconds, 3 rounds. To skip/go back/stop early, click "1 Next stage",
"2 Previous stage" or "3 Finish" in the Explorer (Workspace > RBX_Calibration_Controls).
At the end everything is removed and Lighting is restored.

What it measures:
  stages 1-4  tonemapper: grey/colour patches lit only by Ambient = white,
              at ExposureCompensation -2, 0, +2, +4
  stages 5-7  light falloff: white/black plate pairs facing a PointLight at
              known distances (B=1 R=16, B=4 R=16, B=1 R=32). Black plates
              measure the specular part so it can be subtracted.
]]

local Lighting = game:GetService("Lighting")
local CoreGui = game:GetService("CoreGui")

local ORIGIN = Vector3.new(0, 3000, 0)
local FOV = 70
local COLS, ROWS = 12, 6
local GRID = { x0 = 0.06, x1 = 0.94, y0 = 0.12, y1 = 0.90 } -- viewport fractions
local TONE_DIST = 40 -- tonemap patches sit beyond every light's range
local HOLD = 2 -- seconds per stage
local CYCLES = 3 -- times through all stages

local GREYS = { 0, 4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 255 }
local COLOURS = {
	{ 255, 0, 0 }, { 0, 255, 0 }, { 0, 0, 255 }, { 255, 128, 0 }, { 200, 0, 255 }, { 0, 255, 255 },
	{ 255, 255, 0 }, { 128, 64, 32 }, { 64, 128, 255 }, { 255, 64, 64 }, { 32, 128, 32 }, { 255, 200, 150 },
}
-- 18 distances (studs) for the falloff rows, as white/black pairs
local DISTS = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 20, 28 }

local STAGES = {
	{ amb = 1, ec = -2 },
	{ amb = 1, ec = 0 },
	{ amb = 1, ec = 2 },
	{ amb = 1, ec = 4 },
	{ amb = 0, ec = 0, b = 1, r = 16 },
	{ amb = 0, ec = 0, b = 4, r = 16 },
	{ amb = 0, ec = 0, b = 1, r = 32 },
}

-- save + neutralise Lighting
local saved = {}
local LPROPS = {
	"Ambient", "OutdoorAmbient", "Brightness", "ColorShift_Top", "ColorShift_Bottom", "EnvironmentDiffuseScale",
	"EnvironmentSpecularScale", "ExposureCompensation", "GlobalShadows", "ClockTime", "FogEnd", "FogStart",
	"Technology", "LightingStyle", "PrioritizeLightingQuality",
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
set("Technology", Enum.Technology.Future)
set("LightingStyle", Enum.LightingStyle.Realistic)
set("PrioritizeLightingQuality", true)
set("Brightness", 0)
set("ClockTime", 0)
set("ColorShift_Top", Color3.new(0, 0, 0))
set("ColorShift_Bottom", Color3.new(0, 0, 0))
set("EnvironmentDiffuseScale", 0)
set("EnvironmentSpecularScale", 0)
set("GlobalShadows", false) -- Ambient everywhere, no sky/outdoor split
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
folder.Name = "RBX_LightCalibration"
folder.Parent = workspace

-- light at the camera, plates face it (N.L = 1)
local host = Instance.new("Part")
host.Anchored, host.CanCollide, host.CanQuery, host.CanTouch = true, false, false, false
host.Transparency = 1
host.Size = Vector3.new(0.05, 0.05, 0.05)
host.CFrame = camCF
host.Parent = folder
local light = Instance.new("PointLight")
light.Shadows = false
light.Enabled = false
light.Parent = host

local function cellRay(c, r)
	local vp = cam.ViewportSize
	local fx = GRID.x0 + (c + 0.5) / COLS * (GRID.x1 - GRID.x0)
	local fy = GRID.y0 + (r + 0.5) / ROWS * (GRID.y1 - GRID.y0)
	return cam:ViewportPointToRay(fx * vp.X, fy * vp.Y, 0), (GRID.x1 - GRID.x0) / COLS * vp.X
end

local function plate(c, r, dist, rgb)
	local ray, cellPx = cellRay(c, r)
	local vp = cam.ViewportSize
	-- world size of one cell at this distance (pinhole: viewport height spans 2*tan(fov/2))
	local w = dist * 2 * math.tan(math.rad(FOV) / 2) * cellPx / vp.Y * 0.8
	local p = Instance.new("Part")
	p.Anchored, p.CanCollide, p.CanQuery, p.CanTouch, p.CastShadow = true, false, false, false, false
	p.Material = Enum.Material.SmoothPlastic
	p.Color = Color3.fromRGB(rgb[1], rgb[2], rgb[3])
	p.Size = Vector3.new(w, w, 0.05)
	local pos = ray.Origin + ray.Direction.Unit * dist
	p.CFrame = CFrame.lookAt(pos, ray.Origin)
	p.Parent = folder
end

local tone = {}
for _, g in ipairs(GREYS) do
	table.insert(tone, { g, g, g })
end
for _, c in ipairs(COLOURS) do
	table.insert(tone, c)
end
for i, rgb in ipairs(tone) do
	local k = i - 1
	plate(k % COLS, math.floor(k / COLS), TONE_DIST, rgb)
end
for i, d in ipairs(DISTS) do
	local k = (i - 1) * 2
	local c, r = k % COLS, 3 + math.floor(k / COLS)
	plate(c, r, d, { 255, 255, 255 })
	plate(c + 1, r, d, { 0, 0, 0 })
end

-- screen markers (not tonemapped): magenta corners + 4 stage bits
local gui = Instance.new("ScreenGui")
gui.Name = "RBX_LightCalibration"
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
label.Size = UDim2.fromOffset(520, 26)
label.BackgroundColor3 = Color3.new(0, 0, 0)
label.TextColor3 = Color3.new(1, 1, 1)
label.TextSize = 18
label.Font = Enum.Font.SourceSansBold
label.Parent = gui

-- manual stepping: click these in the Explorer (Workspace > RBX_Calibration_Controls)
local Selection = game:GetService("Selection")
local controls = Instance.new("Folder")
controls.Name = "RBX_Calibration_Controls"
local buttons = {}
for i, n in ipairs({ "1 Next stage", "2 Previous stage", "3 Finish" }) do
	local b = Instance.new("Folder")
	b.Name = n
	b.Parent = controls
	buttons[b] = i
end
controls.Parent = workspace
local stage, cycle, finished, jumped = 1, 1, false, false
local function show()
	local s = STAGES[stage]
	local a = Color3.new(s.amb, s.amb, s.amb)
	set("Ambient", a)
	set("OutdoorAmbient", a)
	set("ExposureCompensation", s.ec)
	light.Enabled = s.b ~= nil
	if s.b then
		light.Brightness, light.Range = s.b, s.r
	end
	for i = 1, 4 do
		bits[i].BackgroundColor3 = (bit32.band(stage, bit32.lshift(1, i - 1)) ~= 0) and Color3.new(1, 1, 1)
			or Color3.new(0, 0, 0)
	end
	label.Text = ("Stage %d / %d (round %d / %d) - screenshot now"):format(stage, #STAGES, cycle, CYCLES)
end
local conn = Selection.SelectionChanged:Connect(function()
	for _, o in ipairs(Selection:Get()) do
		local act = buttons[o]
		if act then
			if act == 1 then
				if stage < #STAGES then stage += 1 elseif cycle < CYCLES then stage, cycle = 1, cycle + 1 else finished = true end
			elseif act == 2 then
				stage = math.max(1, stage - 1)
			else
				finished = true
			end
			jumped = true
			task.defer(function()
				Selection:Set({})
			end)
			if not finished then
				show()
			end
			return
		end
	end
end)
show()
print(("Lighting calibration: each stage shows for %d s, %d rounds. Skip/back/stop: Workspace > RBX_Calibration_Controls."):format(HOLD, CYCLES))

local ok, err = pcall(function()
	while not finished do
		jumped = false
		for _ = 1, HOLD * 10 do
			pinCamera()
			task.wait(0.1)
			if jumped or finished then
				break
			end
		end
		if not jumped and not finished then
			if stage < #STAGES then
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
	end
end)
conn:Disconnect()
controls:Destroy()

-- restore
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
print(ok and "Lighting calibration finished; Lighting restored." or ("Calibration stopped: " .. tostring(err)))
