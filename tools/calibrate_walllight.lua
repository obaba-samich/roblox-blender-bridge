--[[ Roblox light-on-a-wall test: how much a light lights the wall it hangs on, and whether a small
fixture part next to a light shadows it.

Studio quality Level 21, any place (a new Baseplate is fine). View > Command Bar, paste this whole
file, press Enter. It builds a white wall high above the map, turns your Lighting dark while it runs
(restored at the end), cycles 3 stages and removes everything. Screenshot every stage.

On the wall: a PointLight 1 stud in front of it (left) and one 4 studs in front (right); Brightness 1,
Range 16.
Stages:  1 shadows off
         2 shadows on, with a small cap above the left light and a thin bar between the right light and the wall
         3 SpotLights, Angle 180, facing away from the wall (shadows off)
Read with tools/fit_walllight.py.
]]

local Lighting = game:GetService("Lighting")
local CoreGui = game:GetService("CoreGui")
local Selection = game:GetService("Selection")

local O = Vector3.new(-1000, 2500, 0)   -- wall centre; the wall faces +Z
local FOV = 70
local DIST = 30
local HOLD = 4
local CYCLES = 3
local LIGHTS = { { x = -18, h = 1 }, { x = 18, h = 4 } }
local STAGES = { { shadows = false }, { shadows = true, fixtures = true }, { spot = true } }

local saved = {}
for _, k in ipairs({ "Brightness", "Ambient", "OutdoorAmbient", "EnvironmentDiffuseScale", "EnvironmentSpecularScale", "ExposureCompensation", "Technology" }) do
	saved[k] = Lighting[k]
end
local cam = workspace.CurrentCamera
local savedCam = { cf = cam.CFrame, fov = cam.FieldOfView, type = cam.CameraType }
local camCF = CFrame.lookAt(O + Vector3.new(0, 0, DIST), O)
local function pinCamera()
	cam.CameraType = Enum.CameraType.Scriptable
	cam.FieldOfView = FOV
	cam.CFrame = camCF
end

local folder = Instance.new("Folder")
folder.Name = "RBX_WallLightCalibration"
folder.Parent = workspace

local function part(size, cf, col, shadow)
	local p = Instance.new("Part")
	p.Anchored, p.CanCollide, p.CanQuery, p.CanTouch = true, false, false, false
	p.Material = Enum.Material.SmoothPlastic
	p.Color = col
	p.Size = size
	p.CFrame = cf
	p.CastShadow = shadow ~= false
	p.Parent = folder
	return p
end

part(Vector3.new(90, 60, 1), CFrame.new(O - Vector3.new(0, 0, 0.5)), Color3.new(1, 1, 1))

-- the lights: an invisible holder each, with a PointLight and a SpotLight (one enabled per stage)
local lights = {}
for i, l in ipairs(LIGHTS) do
	local holder = part(Vector3.new(0.2, 0.2, 0.2), CFrame.new(O + Vector3.new(l.x, 0, l.h)), Color3.new(1, 1, 1), false)
	holder.Transparency = 1
	local pl = Instance.new("PointLight")
	pl.Brightness, pl.Range, pl.Color = 1, 16, Color3.new(1, 1, 1)
	pl.Parent = holder
	local sl = Instance.new("SpotLight")
	sl.Brightness, sl.Range, sl.Color, sl.Angle, sl.Face = 1, 16, Color3.new(1, 1, 1), 180, Enum.NormalId.Front
	sl.Parent = holder
	-- Front of an unrotated part is -Z; turn the holder so Front faces away from the wall (+Z)
	holder.CFrame = CFrame.lookAt(holder.Position, holder.Position + Vector3.new(0, 0, 1))
	lights[i] = { point = pl, spot = sl }
end
-- fixtures (stage 2 only): a 2x0.2x2 cap 0.6 above the left light, a thin bar halfway between the right light and the wall
local fixtures = {
	part(Vector3.new(2, 0.2, 2), CFrame.new(O + Vector3.new(LIGHTS[1].x, 0.6, LIGHTS[1].h)), Color3.new(0.2, 0.2, 0.2)),
	part(Vector3.new(0.3, 8, 0.3), CFrame.new(O + Vector3.new(LIGHTS[2].x, 0, LIGHTS[2].h / 2)), Color3.new(0.2, 0.2, 0.2)),
}

local gui = Instance.new("ScreenGui")
gui.Name = "RBX_WallLightCalibration"
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
for i = 1, 5 do
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

Lighting.Brightness = 0
Lighting.Ambient = Color3.new(0, 0, 0)
Lighting.OutdoorAmbient = Color3.new(0, 0, 0)
Lighting.EnvironmentDiffuseScale = 0
Lighting.EnvironmentSpecularScale = 0
Lighting.ExposureCompensation = 0
Lighting.Technology = Enum.Technology.Future

local stage, cycle, finished, jumped = 1, 1, false, false
local function show()
	local s = STAGES[stage]
	for _, l in ipairs(lights) do
		l.point.Enabled = not s.spot
		l.spot.Enabled = s.spot == true
		l.point.Shadows = s.shadows == true
		l.spot.Shadows = s.shadows == true
	end
	for _, f in ipairs(fixtures) do
		f.Transparency = s.fixtures and 0 or 1
		f.CastShadow = s.fixtures == true
	end
	local code = stage + 19   -- 20-22
	for i = 1, 5 do
		bits[i].BackgroundColor3 = (bit32.band(code, bit32.lshift(1, i - 1)) ~= 0) and Color3.new(1, 1, 1)
			or Color3.new(0, 0, 0)
	end
	label.Text = ("Wall light stage %d / %d (round %d / %d) - screenshot now"):format(stage, #STAGES, cycle, CYCLES)
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
pinCamera()
show()
print(("Wall light test: each stage shows for %d s, %d rounds. Skip/back/stop: Workspace > RBX_Calibration_Controls."):format(HOLD, CYCLES))

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
folder:Destroy()
gui:Destroy()
for k, v in pairs(saved) do
	Lighting[k] = v
end
cam.CameraType = savedCam.type
cam.FieldOfView = savedCam.fov
cam.CFrame = savedCam.cf
print(ok and "Wall light test finished; Lighting restored." or ("Test stopped: " .. tostring(err)))
