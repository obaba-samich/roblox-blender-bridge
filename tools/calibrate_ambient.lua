--[[ Roblox sky/ambient/sun calibration (run in YOUR place, so its own Lighting is measured)

Studio quality Level 21. View > Command Bar, paste this whole file, press Enter.
It builds two small test areas high above the map (one open to the sky, one inside a closed
box), cycles 4 stages and then removes everything; your Lighting is only changed while it
runs (Brightness 0 in stages 2-3) and restored at the end. Screenshot every stage.

Stages:  1 open, as-is (sun + sky + ambient)    2 open, sun off (sky + ambient)
         3 closed box, sun off (indoor ambient)  4 closed box, as-is
         5-7 open, as-is, turned 90/180/270 degrees (walls facing +X, -Z, -X; stage 1 is +Z)
Each cell is a white (diffuse) or black (reflection only) plate, facing up or facing the camera.
Read with tools/fit_ambient.py. Uses the same chart layout as calibrate_lighting.lua.
]]

local Lighting = game:GetService("Lighting")
local CoreGui = game:GetService("CoreGui")
local Selection = game:GetService("Selection")

local OPEN = Vector3.new(0, 2500, 0)
local CLOSED = Vector3.new(1000, 2500, 0)
local FOV = 70
local COLS, ROWS = 12, 6
local GRID = { x0 = 0.06, x1 = 0.94, y0 = 0.12, y1 = 0.90 }
local DIST = 20
local HOLD = 4
local CYCLES = 3
local STAGES = { { at = "open", sun = true }, { at = "open", sun = false }, { at = "closed", sun = false }, { at = "closed", sun = true },
	{ at = "open", sun = true, yaw = 90 }, { at = "open", sun = true, yaw = 180 }, { at = "open", sun = true, yaw = 270 } }
-- each turned stage has its own chart, side by side and far enough apart not to shade each other
local function origin(s)
	if s.at == "closed" then return CLOSED end
	return OPEN + Vector3.new((s.yaw or 0) / 90 * 300, 0, 0)
end

local savedBrightness = Lighting.Brightness
local cam = workspace.CurrentCamera
local savedCam = { cf = cam.CFrame, fov = cam.FieldOfView, type = cam.CameraType }
-- looking 40 degrees down, so plates facing up and plates facing the camera are both visible
local function camAt(p, yaw)
	return CFrame.new(p) * CFrame.Angles(0, math.rad(yaw or 0), 0) * CFrame.Angles(math.rad(-40), 0, 0)
end
local camCF = camAt(OPEN)
local function pinCamera()
	cam.CameraType = Enum.CameraType.Scriptable
	cam.FieldOfView = FOV
	cam.CFrame = camCF
end

local folder = Instance.new("Folder")
folder.Name = "RBX_AmbientCalibration"
folder.Parent = workspace

local function part(size, cf, col)
	local p = Instance.new("Part")
	p.Anchored, p.CanCollide, p.CanQuery, p.CanTouch = true, false, false, false
	p.Material = Enum.Material.SmoothPlastic
	p.Color = col
	p.Size = size
	p.CFrame = cf
	p.Parent = folder
	return p
end

-- cells: columns 0-5 face up, 6-11 face the camera; even columns white, odd black
local function buildChart(origin, yaw)
	camCF = camAt(origin, yaw)
	pinCamera()
	task.wait(0.1)
	local vp = cam.ViewportSize
	for r = 0, ROWS - 1 do
		for c = 0, COLS - 1 do
			local fx = GRID.x0 + (c + 0.5) / COLS * (GRID.x1 - GRID.x0)
			local fy = GRID.y0 + (r + 0.5) / ROWS * (GRID.y1 - GRID.y0)
			local ray = cam:ViewportPointToRay(fx * vp.X, fy * vp.Y, 0)
			local pos = ray.Origin + ray.Direction.Unit * DIST
			local cellPx = (GRID.x1 - GRID.x0) / COLS * vp.X
			local w = DIST * 2 * math.tan(math.rad(FOV) / 2) * cellPx / vp.Y * 0.8
			local col = (c % 2 == 0) and Color3.new(1, 1, 1) or Color3.new(0, 0, 0)
			if c < 6 then
				-- facing up: stretch along the view so it still fills the cell
				local fwd = Vector3.new(ray.Direction.X, 0, ray.Direction.Z).Unit
				local cf = CFrame.lookAt(pos, pos + fwd)
				part(Vector3.new(w, 0.05, w * 1.6), cf, col)
			else
				local flat = Vector3.new(ray.Origin.X, pos.Y, ray.Origin.Z)
				part(Vector3.new(w, w, 0.05), CFrame.lookAt(pos, flat), col)
			end
		end
	end
end

buildChart(OPEN)
for _, s in ipairs(STAGES) do
	if s.yaw then
		buildChart(origin(s), s.yaw)
	end
end
-- closed box around the second chart: 8-stud walls (thicker than Roblox's 4-stud light voxels)
do
	local inner, t = Vector3.new(90, 60, 90), 8
	local c = CLOSED + Vector3.new(0, -10, -25)
	local g = Color3.fromRGB(128, 128, 128)
	part(Vector3.new(inner.X + 2 * t, t, inner.Z + 2 * t), CFrame.new(c + Vector3.new(0, inner.Y / 2 + t / 2, 0)), g)
	part(Vector3.new(inner.X + 2 * t, t, inner.Z + 2 * t), CFrame.new(c - Vector3.new(0, inner.Y / 2 + t / 2, 0)), g)
	part(Vector3.new(t, inner.Y, inner.Z + 2 * t), CFrame.new(c + Vector3.new(inner.X / 2 + t / 2, 0, 0)), g)
	part(Vector3.new(t, inner.Y, inner.Z + 2 * t), CFrame.new(c - Vector3.new(inner.X / 2 + t / 2, 0, 0)), g)
	part(Vector3.new(inner.X, inner.Y, t), CFrame.new(c + Vector3.new(0, 0, inner.Z / 2 + t / 2)), g)
	part(Vector3.new(inner.X, inner.Y, t), CFrame.new(c - Vector3.new(0, 0, inner.Z / 2 + t / 2)), g)
end
buildChart(CLOSED)

local gui = Instance.new("ScreenGui")
gui.Name = "RBX_AmbientCalibration"
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
-- bit 4 (value 8) marks the ambient chart, so it can't be mixed up with calibrate_lighting shots;
-- the turned stages use codes 17-19 (bit 5)
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

local stage, cycle, finished, jumped = 1, 1, false, false
local function show()
	local s = STAGES[stage]
	Lighting.Brightness = s.sun and savedBrightness or 0
	camCF = camAt(origin(s), s.yaw)
	local code = stage <= 4 and stage + 8 or stage + 12
	for i = 1, 5 do
		bits[i].BackgroundColor3 = (bit32.band(code, bit32.lshift(1, i - 1)) ~= 0) and Color3.new(1, 1, 1)
			or Color3.new(0, 0, 0)
	end
	label.Text = ("Ambient stage %d / %d (round %d / %d) - screenshot now"):format(stage, #STAGES, cycle, CYCLES)
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
print(("Ambient calibration: each stage shows for %d s, %d rounds. Skip/back/stop: Workspace > RBX_Calibration_Controls."):format(HOLD, CYCLES))

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
Lighting.Brightness = savedBrightness
cam.CameraType = savedCam.type
cam.FieldOfView = savedCam.fov
cam.CFrame = savedCam.cf
print(ok and "Ambient calibration finished; Lighting restored." or ("Calibration stopped: " .. tostring(err)))
