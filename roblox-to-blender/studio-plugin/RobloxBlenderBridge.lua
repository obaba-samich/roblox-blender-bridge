--[[
	Roblox -> Blender Bridge  (Studio plugin)
	Part 1 (Roblox -> Blender) of https://github.com/obaba-samich/roblox-blender-bridge

	Reads EXACT engine data:
	  * geometry via EditableMesh (positions, per-corner UVs, per-corner normals)
	  * texture pixels via EditableImage (raw RGBA8, no re-encoding loss)
	and streams it to the Blender add-on's local server (http://localhost:<port>).

	Assets EditableMesh/EditableImage refuse to open (not owned by you / the
	experience owner) are flagged so the Blender side can download them instead.

	Every request body is kept well under Roblox's 1024 KB HttpService limit.
]]

local HttpService = game:GetService("HttpService")
local AssetService = game:GetService("AssetService")
local Selection = game:GetService("Selection")
local MarketplaceService = game:GetService("MarketplaceService")
local MaterialService = game:GetService("MaterialService")
local Lighting = game:GetService("Lighting")
local GeometryService = game:GetService("GeometryService")
local SerializationService = game:GetService("SerializationService")
local EncodingService = game:GetService("EncodingService")

local PROTOCOL = 1
local RAW_CHUNK = 600000 -- raw bytes per request; base64 -> ~800 KB body (< 1024 KB limit)
local DEFAULT_PORT = 34873

---------------------------------------------------------------------------
-- settings
---------------------------------------------------------------------------
local function getSetting(key, default)
	local ok, v = pcall(function()
		return plugin:GetSetting(key)
	end)
	if ok and v ~= nil then
		return v
	end
	return default
end
local function setSetting(key, v)
	pcall(function()
		plugin:SetSetting(key, v)
	end)
end

local port = tonumber(getSetting("RBB_Port", DEFAULT_PORT)) or DEFAULT_PORT
local skipInvisible = getSetting("RBB_SkipInvisible", true) ~= false

---------------------------------------------------------------------------
-- UI
---------------------------------------------------------------------------
local toolbar = plugin:CreateToolbar("Blender Bridge")
local toggleButton = toolbar:CreateButton("RobloxBlenderBridge", "Export the selection to Blender", "", "Blender Bridge")
toggleButton.ClickableWhenViewportHidden = true

local widgetInfo = DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Float, false, false, 360, 480, 280, 320)
local widget
do
	local ok, w = pcall(function()
		return plugin:CreateDockWidgetPluginGuiAsync("RobloxBlenderBridge", widgetInfo)
	end)
	if not ok or not w then
		w = plugin:CreateDockWidgetPluginGui("RobloxBlenderBridge", widgetInfo)
	end
	widget = w
end
widget.Title = "Roblox → Blender Bridge"
widget.Name = "RobloxBlenderBridge"

-- takes the color NAME so an unknown enum item can never error outside the pcall
local function themeColor(name: string)
	local ok, col = pcall(function()
		return settings().Studio.Theme:GetColor((Enum.StudioStyleGuideColor :: any)[name])
	end)
	if ok and typeof(col) == "Color3" then
		return col
	end
	return name:find("Text") and Color3.new(0.9, 0.9, 0.9) or Color3.new(0.18, 0.18, 0.18)
end

local root = Instance.new("Frame")
root.Size = UDim2.fromScale(1, 1)
root.BackgroundColor3 = themeColor("MainBackground")
root.BorderSizePixel = 0
root.Parent = widget

local pad = Instance.new("UIPadding")
pad.PaddingLeft, pad.PaddingRight = UDim.new(0, 8), UDim.new(0, 8)
pad.PaddingTop, pad.PaddingBottom = UDim.new(0, 8), UDim.new(0, 8)
pad.Parent = root

local list = Instance.new("UIListLayout")
list.Padding = UDim.new(0, 6)
list.SortOrder = Enum.SortOrder.LayoutOrder
list.Parent = root

local order = 0
local function nextOrder()
	order += 1
	return order
end

local function label(text)
	local l = Instance.new("TextLabel")
	l.BackgroundTransparency = 1
	l.Size = UDim2.new(1, 0, 0, 18)
	l.Font = Enum.Font.SourceSans
	l.TextSize = 15
	l.TextXAlignment = Enum.TextXAlignment.Left
	l.TextColor3 = themeColor("MainText")
	l.Text = text
	l.LayoutOrder = nextOrder()
	l.Parent = root
	return l
end

local function button(text)
	local b = Instance.new("TextButton")
	b.Size = UDim2.new(1, 0, 0, 28)
	b.Font = Enum.Font.SourceSansBold
	b.TextSize = 16
	b.Text = text
	b.AutoButtonColor = true
	b.BackgroundColor3 = themeColor("Button")
	b.TextColor3 = themeColor("ButtonText")
	b.BorderSizePixel = 0
	b.LayoutOrder = nextOrder()
	b.Parent = root
	return b
end

local portRow = Instance.new("Frame")
portRow.BackgroundTransparency = 1
portRow.Size = UDim2.new(1, 0, 0, 24)
portRow.LayoutOrder = nextOrder()
portRow.Parent = root
local portLabel = Instance.new("TextLabel")
portLabel.BackgroundTransparency = 1
portLabel.Size = UDim2.new(0, 110, 1, 0)
portLabel.Font = Enum.Font.SourceSans
portLabel.TextSize = 15
portLabel.TextXAlignment = Enum.TextXAlignment.Left
portLabel.TextColor3 = themeColor("MainText")
portLabel.Text = "Blender port:"
portLabel.Parent = portRow
local portBox = Instance.new("TextBox")
portBox.Position = UDim2.new(0, 110, 0, 0)
portBox.Size = UDim2.new(1, -110, 1, 0)
portBox.Font = Enum.Font.Code
portBox.TextSize = 15
portBox.Text = tostring(port)
portBox.ClearTextOnFocus = false
portBox.BackgroundColor3 = themeColor("InputFieldBackground")
portBox.TextColor3 = themeColor("MainText")
portBox.BorderSizePixel = 0
portBox.Parent = portRow
portBox.FocusLost:Connect(function()
	local n = tonumber(portBox.Text)
	if n and n >= 1024 and n <= 65535 then
		port = math.floor(n)
		setSetting("RBB_Port", port)
	end
	portBox.Text = tostring(port)
end)

local skipBtn = button("")
local function refreshSkip()
	skipBtn.Text = (skipInvisible and "[x] " or "[ ] ") .. "Skip fully transparent parts (Transparency = 1)"
	skipBtn.TextSize = 14
end
refreshSkip()
skipBtn.MouseButton1Click:Connect(function()
	skipInvisible = not skipInvisible
	setSetting("RBB_SkipInvisible", skipInvisible)
	refreshSkip()
end)

local pingBtn = button("Test connection to Blender")
local exportBtn = button("Export Selection → Blender")
local exportWsBtn = button("Export whole Workspace → Blender")

local progressLabel = label("Idle.")

local logFrame = Instance.new("ScrollingFrame")
logFrame.Size = UDim2.new(1, 0, 1, -230)
logFrame.CanvasSize = UDim2.new(0, 0, 0, 0)
logFrame.AutomaticCanvasSize = Enum.AutomaticSize.Y
logFrame.ScrollBarThickness = 8
logFrame.BackgroundColor3 = themeColor("ScriptBackground")
logFrame.BorderSizePixel = 0
logFrame.LayoutOrder = nextOrder()
logFrame.Parent = root
local logText = Instance.new("TextLabel")
logText.BackgroundTransparency = 1
logText.Size = UDim2.new(1, -10, 0, 0)
logText.AutomaticSize = Enum.AutomaticSize.Y
logText.Font = Enum.Font.Code
logText.TextSize = 13
logText.TextWrapped = true
logText.TextXAlignment = Enum.TextXAlignment.Left
logText.TextYAlignment = Enum.TextYAlignment.Top
logText.TextColor3 = themeColor("MainText")
logText.Text = ""
logText.Parent = logFrame

local logLines = {}
local function log(msg)
	msg = tostring(msg)
	print("[BlenderBridge] " .. msg)
	table.insert(logLines, msg)
	while #logLines > 250 do
		table.remove(logLines, 1)
	end
	logText.Text = table.concat(logLines, "\n")
	task.defer(function()
		logFrame.CanvasPosition = Vector2.new(0, math.max(0, logFrame.AbsoluteCanvasSize.Y))
	end)
end
local function progress(msg)
	progressLabel.Text = msg
end

toggleButton.Click:Connect(function()
	widget.Enabled = not widget.Enabled
end)

---------------------------------------------------------------------------
-- base64 (buffer based, fast)
---------------------------------------------------------------------------
local B64 = buffer.fromstring("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")
local readu8, writeu8 = buffer.readu8, buffer.writeu8

local function base64Range(src: buffer, first: number, count: number): string
	local full = count - (count % 3)
	local out = buffer.create(math.floor((count + 2) / 3) * 4)
	local o = 0
	local i = first
	local stop = first + full
	while i < stop do
		local n = readu8(src, i) * 65536 + readu8(src, i + 1) * 256 + readu8(src, i + 2)
		writeu8(out, o, readu8(B64, n // 262144))
		writeu8(out, o + 1, readu8(B64, (n // 4096) % 64))
		writeu8(out, o + 2, readu8(B64, (n // 64) % 64))
		writeu8(out, o + 3, readu8(B64, n % 64))
		i += 3
		o += 4
	end
	local rem = count - full
	if rem == 1 then
		local n = readu8(src, i) * 65536
		writeu8(out, o, readu8(B64, n // 262144))
		writeu8(out, o + 1, readu8(B64, (n // 4096) % 64))
		writeu8(out, o + 2, 61)
		writeu8(out, o + 3, 61)
	elseif rem == 2 then
		local n = readu8(src, i) * 65536 + readu8(src, i + 1) * 256
		writeu8(out, o, readu8(B64, n // 262144))
		writeu8(out, o + 1, readu8(B64, (n // 4096) % 64))
		writeu8(out, o + 2, readu8(B64, (n // 64) % 64))
		writeu8(out, o + 3, 61)
	end
	return buffer.tostring(out)
end

---------------------------------------------------------------------------
-- HTTP
---------------------------------------------------------------------------
local function request(method: string, path: string, body)
	local url = ("http://localhost:%d%s"):format(port, path)
	local encoded = body and HttpService:JSONEncode(body) or nil
	if encoded and #encoded > 1000000 then
		error(("internal: request body too large (%d bytes)"):format(#encoded))
	end
	local lastErr = "unknown error"
	for attempt = 1, 5 do
		local ok, res = pcall(function()
			return HttpService:RequestAsync({
				Url = url,
				Method = method,
				Headers = encoded and { ["Content-Type"] = "application/json" } or nil,
				Body = encoded,
			})
		end)
		if ok then
			if res.Success then
				local dok, decoded = pcall(function()
					return HttpService:JSONDecode(res.Body)
				end)
				if dok then
					return decoded
				end
				lastErr = "bad JSON from Blender: " .. tostring(res.Body):sub(1, 200)
			else
				lastErr = ("HTTP %d: %s"):format(res.StatusCode, tostring(res.Body):sub(1, 300))
				if res.StatusCode >= 400 and res.StatusCode < 500 then
					error(lastErr)
				end
			end
		else
			lastErr = tostring(res)
			if lastErr:find("Number of requests exceeded") then
				task.wait(5) -- HttpService rate limit
			end
		end
		task.wait(0.4 * attempt)
	end
	error(lastErr)
end

-- send any binary/text blob in <1 MB requests
local function sendBlob(session: string, kind: string, key: string, data: buffer, meta)
	local len = buffer.len(data)
	local total = math.max(1, math.ceil(len / RAW_CHUNK))
	for i = 0, total - 1 do
		local first = i * RAW_CHUNK
		local count = math.min(RAW_CHUNK, len - first)
		local b64 = base64Range(data, first, count)
		request("POST", "/blob", {
			session = session,
			kind = kind,
			key = key,
			index = i,
			total = total,
			data = b64,
			meta = (i == total - 1) and meta or nil,
		})
		if total > 1 then
			progress(("  %s %s: chunk %d/%d"):format(kind, key, i + 1, total))
		end
	end
end

---------------------------------------------------------------------------
-- asset ids
---------------------------------------------------------------------------
local function normalizeId(s)
	if typeof(s) ~= "string" then
		return nil
	end
	s = s:gsub("^%s+", ""):gsub("%s+$", "")
	if s == "" then
		return nil
	end
	local num = s:match("^(%d+)$") or s:match("rbxassetid://(%d+)") or s:match("[?&][iI][dD]=(%d+)")
	if num then
		return "rbxassetid://" .. num, "a" .. num, num
	end
	local key = "u_" .. (s:gsub("[^%w]", "_")):sub(1, 90)
	return s, key, nil
end

---------------------------------------------------------------------------
-- Editable* loading (several API shapes, newest first)
---------------------------------------------------------------------------
local function tryAll(fns)
	local lastErr
	for _, f in ipairs(fns) do
		local ok, r = pcall(f)
		if ok and r ~= nil then
			return r
		end
		lastErr = r
	end
	return nil, tostring(lastErr)
end

local function loadEditableMesh(uri)
	return tryAll({
		function()
			return AssetService:CreateEditableMeshAsync(Content.fromUri(uri), { FixedSize = true })
		end,
		function()
			return AssetService:CreateEditableMeshAsync(Content.fromUri(uri))
		end,
		function()
			return (AssetService :: any):CreateEditableMeshAsync(uri)
		end,
	})
end

local function loadEditableImage(uri)
	return tryAll({
		function()
			return AssetService:CreateEditableImageAsync(Content.fromUri(uri))
		end,
		function()
			return (AssetService :: any):CreateEditableImageAsync(uri)
		end,
	})
end

local function finite(n)
	if n ~= n or n == math.huge or n == -math.huge then
		return 0
	end
	return n
end

-- EditableMesh -> compact binary buffer
-- layout (little endian):
--  "RBM1" | u32 nVerts | u32 nCorners | u8 hasUV | u8 hasNormal | u16 pad
--  f32[nVerts*3] positions | u32[nCorners] vertex index
--  f32[nCorners*2] uv (raw Roblox convention) | f32[nCorners*3] normals
local TRI = { 1, 2, 3 }
local QUAD = { 1, 2, 3, 1, 3, 4 } -- quads are split along the 1-3 diagonal
local function extractMesh(em)
	local faces = em:GetFaces()
	local vmap = {}
	local positions = {}
	local corners = {}
	local uvs = {}
	local normals = {}
	local nVerts = 0
	local hasUV, hasN = true, true
	if not pcall(function()
		return em:GetFaceUVs(faces[1])
	end) then
		hasUV = false
	end
	if not pcall(function()
		return em:GetFaceNormals(faces[1])
	end) then
		hasN = false
	end

	local skipped = 0
	for fi, f in ipairs(faces) do
		local vs = em:GetFaceVertices(f)
		local order = (#vs == 3 and TRI) or (#vs == 4 and QUAD) or nil
		if order then
			local fuv = hasUV and em:GetFaceUVs(f) or nil
			local fn = hasN and em:GetFaceNormals(f) or nil
			for _, c in ipairs(order) do
				local vid = vs[c]
				local idx = vmap[vid]
				if idx == nil then
					local p = em:GetPosition(vid)
					idx = nVerts
					nVerts += 1
					vmap[vid] = idx
					positions[#positions + 1] = finite(p.X)
					positions[#positions + 1] = finite(p.Y)
					positions[#positions + 1] = finite(p.Z)
				end
				corners[#corners + 1] = idx
				local uvId = fuv and fuv[c]
				if uvId then
					local uv = em:GetUV(uvId)
					uvs[#uvs + 1] = finite(uv.X)
					uvs[#uvs + 1] = finite(uv.Y)
				else
					uvs[#uvs + 1] = 0
					uvs[#uvs + 1] = 0
				end
				local nId = fn and fn[c]
				if nId then
					local n = em:GetNormal(nId)
					normals[#normals + 1] = finite(n.X)
					normals[#normals + 1] = finite(n.Y)
					normals[#normals + 1] = finite(n.Z)
				else
					normals[#normals + 1] = 0
					normals[#normals + 1] = 0
					normals[#normals + 1] = 0
				end
			end
		else
			skipped += 1
		end
		if fi % 3000 == 0 then
			task.wait()
		end
	end

	local nCorners = #corners
	local size = 16 + nVerts * 12 + nCorners * 4 + nCorners * 8 + nCorners * 12
	local b = buffer.create(size)
	buffer.writestring(b, 0, "RBM1")
	buffer.writeu32(b, 4, nVerts)
	buffer.writeu32(b, 8, nCorners)
	buffer.writeu8(b, 12, hasUV and 1 or 0)
	buffer.writeu8(b, 13, hasN and 1 or 0)
	local o = 16
	for i = 1, #positions do
		buffer.writef32(b, o, positions[i])
		o += 4
	end
	for i = 1, nCorners do
		buffer.writeu32(b, o, corners[i])
		o += 4
	end
	for i = 1, #uvs do
		buffer.writef32(b, o, uvs[i])
		o += 4
	end
	for i = 1, #normals do
		buffer.writef32(b, o, normals[i])
		o += 4
	end
	return b, nVerts, nCorners // 3, skipped
end

---------------------------------------------------------------------------
-- unions (PartOperation)
---------------------------------------------------------------------------
-- Plugins can't read union geometry directly. The way around it (from EgoMoose's MIT-licensed
-- rbx-csg-to-mesh-plugin, format notes from krakow10's rbx_mesh): union the part again with nothing,
-- which gives a local union whose render mesh is stored in the instance, serialize that to .rbxm,
-- find the mesh among the file's shared strings and decode it (CSGMDL v5 or the newer SolidMesh).

-- LZ4 block (rbxm chunks are LZ4 unless they start with the Zstd magic)
local function lz4Decompress(src, srcLen, dstLen)
	local dst = buffer.create(dstLen)
	local i, o = 0, 0
	while i < srcLen do
		local token = buffer.readu8(src, i)
		i += 1
		local lit = bit32.rshift(token, 4)
		if lit == 15 then
			repeat
				local x = buffer.readu8(src, i)
				i += 1
				lit += x
			until x ~= 255
		end
		if lit > 0 then
			buffer.copy(dst, o, src, i, lit)
			i += lit
			o += lit
		end
		if i >= srcLen then
			break
		end
		local off = buffer.readu16(src, i)
		i += 2
		local ml = bit32.band(token, 15)
		if ml == 15 then
			repeat
				local x = buffer.readu8(src, i)
				i += 1
				ml += x
			until x ~= 255
		end
		ml += 4
		local from = o - off
		if off >= ml then
			buffer.copy(dst, o, dst, from, ml)
		else -- overlapping copy (repeats the last `off` bytes)
			for k = 0, ml - 1 do
				buffer.writeu8(dst, o + k, buffer.readu8(dst, from + k))
			end
		end
		o += ml
	end
	return dst
end

-- every shared string (SSTR chunk) of a binary .rbxm buffer
local RBXM_MAGIC = "<roblox!\x89\xff\x0d\x0a\x1a\x0a"
local function rbxmSharedStrings(b)
	assert(buffer.readstring(b, 0, #RBXM_MAGIC) == RBXM_MAGIC, "not a binary rbxm")
	local o = 32 -- magic, u16 version, u32 classes, u32 instances, 8 reserved
	local out = {}
	while o + 16 <= buffer.len(b) do
		local name = buffer.readstring(b, o, 4)
		local clen, ulen = buffer.readu32(b, o + 4), buffer.readu32(b, o + 8)
		o += 16
		if name == "END\0" then
			break
		end
		local data
		if clen == 0 then
			data = buffer.create(ulen)
			buffer.copy(data, 0, b, o, ulen)
			o += ulen
		else
			local c = buffer.create(clen)
			buffer.copy(c, 0, b, o, clen)
			o += clen
			if buffer.readstring(c, 0, 4) == "\x28\xB5\x2F\xFD" then
				data = EncodingService:DecompressBuffer(c, Enum.CompressionAlgorithm.Zstd)
			else
				data = lz4Decompress(c, clen, ulen)
			end
		end
		if name == "SSTR" then
			local count = buffer.readu32(data, 4)
			local p = 8
			for _ = 1, count do
				local len = buffer.readu32(data, p + 16) -- after the 16-byte hash
				local sb = buffer.create(len)
				buffer.copy(sb, 0, data, p + 20, len)
				table.insert(out, sb)
				p += 20 + len
			end
		end
	end
	return out
end

-- delta-coded index list (both formats); reads `count` indices starting at byte `o`, returns indices, next byte
local function readIndexRun(b, o, count)
	local index, out = 0, table.create(count)
	for k = 1, count do
		local v0 = buffer.readu8(b, o)
		o += 1
		if bit32.band(v0, 0x80) == 0 then
			if bit32.band(v0, 0x40) == 0 then
				index += v0
			else
				index += bit32.bor(v0, 0x80) - 256
			end
		else
			index += bit32.bor(buffer.readu8(b, o + 1), bit32.lshift(buffer.readu8(b, o), 8), bit32.lshift(bit32.band(v0, 0x7F), 16))
			o += 2
		end
		out[k] = bit32.band(index, 0x7FFFFF)
	end
	return out, o
end

local CSGMDL5_HEADER = "\x15\x7d\x29\x15\x75\x6c\x35\x04\x34\x69" -- "CSGMDL" + u32 5, obfuscated
local SOLIDMESH_HEADER = "SolidMesh\x00\x00\x00\x00" -- after 14 bytes

-- -> { positions = {Vector3}, normals = {Vector3}, faces = {i}, faceNormals = {i} } (0-based indices)
local function parseCSGMDL5(b)
	local o = #CSGMDL5_HEADER
	local function q15(x) -- i16 -> -1..1 (Roblox's wrapped quantisation)
		local m = 32767
		return ((x - m + 32768) % 65536 - 32768) / m
	end
	local positions, normals = {}, {}
	local n = buffer.readu16(b, o)
	o += 2
	for i = 1, n do
		positions[i] = Vector3.new(buffer.readf32(b, o), buffer.readf32(b, o + 4), buffer.readf32(b, o + 8))
		o += 12
	end
	n = buffer.readu16(b, o)
	o += 6 -- count, byte size
	for i = 1, n do
		normals[i] = Vector3.new(q15(buffer.readi16(b, o)), q15(buffer.readi16(b, o + 2)), q15(buffer.readi16(b, o + 4)))
		o += 6
	end
	n = buffer.readu16(b, o) -- colours (rgba8)
	o += 2 + n * 4
	n = buffer.readu16(b, o) -- normal ids (u8)
	o += 2 + n
	n = buffer.readu16(b, o) -- uvs (f32 x2)
	o += 2 + n * 8
	n = buffer.readu16(b, o) -- tangents (count, byte size, i16 x3)
	o += 6 + n * 6
	local nIdx, nData = buffer.readu32(b, o), buffer.readu32(b, o + 4)
	o += 8
	local idx = readIndexRun(b, o, nIdx)
	o += nData
	local markers = {}
	for k = 1, buffer.readu8(b, o) do
		markers[k] = buffer.readu32(b, o + 1 + (k - 1) * 4)
	end
	local m0, m1, m2 = markers[1] or 0, markers[2] or #idx, markers[3]
	if m0 ~= 0 then
		idx = table.move(idx, m0 + 1, #idx, 1, {})
	end
	if not m2 then
		for _ = 1, m1 - m0 do
			table.remove(idx)
		end
	end
	return { positions = positions, normals = normals, faces = idx, faceNormals = idx }
end

local function parseSolidMesh(b)
	local o = 14 + #SOLIDMESH_HEADER
	local positions, normals = {}, {}
	local n = buffer.readu32(b, o)
	o += 8
	for i = 1, n do
		positions[i] = Vector3.new(buffer.readf32(b, o), buffer.readf32(b, o + 4), buffer.readf32(b, o + 8))
		o += 12
	end
	n = buffer.readu32(b, o)
	o += 8
	for i = 1, n do
		normals[i] = Vector3.new(buffer.readf32(b, o), buffer.readf32(b, o + 4), buffer.readf32(b, o + 8))
		o += 12
	end
	n = buffer.readu32(b, o) -- probably uvs (f32 x2)
	o += 8 + n * 8
	n = buffer.readu32(b, o) -- colours (f32 x3)
	o += 8 + n * 12
	local faces, faceNormals
	n = buffer.readu32(b, o)
	faces, o = readIndexRun(b, o + 8, n)
	n = buffer.readu32(b, o)
	faceNormals, o = readIndexRun(b, o + 8, n)
	return { positions = positions, normals = normals, faces = faces, faceNormals = faceNormals }
end

local function fnv1a(b)
	local h = 2166136261
	for i = 0, buffer.len(b) - 1 do
		h = bit32.bxor(h, buffer.readu8(b, i))
		h = (h * 16777619) % 4294967296
	end
	return h
end

-- union -> RBM1 buffer in the union's own space (studs, true size, no stretching), nVerts, nTris
local function extractUnion(part)
	local csg = GeometryService:UnionAsync(part, {}, {
		CollisionFidelity = part.CollisionFidelity,
		RenderFidelity = part.RenderFidelity,
		SplitApart = false,
	})[1]
	assert(csg, "UnionAsync returned nothing")
	local csgCf = csg.CFrame
	local ok, serialized = pcall(function()
		return SerializationService:SerializeInstancesAsync({ csg })
	end)
	csg:Destroy()
	assert(ok, "serialize failed: " .. tostring(serialized))
	local mesh
	for _, sb in ipairs(rbxmSharedStrings(serialized)) do
		local len = buffer.len(sb)
		if len >= 14 + #SOLIDMESH_HEADER and buffer.readstring(sb, 14, #SOLIDMESH_HEADER) == SOLIDMESH_HEADER then
			mesh = parseSolidMesh(sb)
			break
		elseif len >= #CSGMDL5_HEADER and buffer.readstring(sb, 0, #CSGMDL5_HEADER) == CSGMDL5_HEADER then
			mesh = parseCSGMDL5(sb)
			break
		end
	end
	assert(mesh, "no CSGMDL v5 / SolidMesh data in the union")
	assert(#mesh.faces > 0 and #mesh.faces % 3 == 0, "bad face list")
	local toPart = part.CFrame:Inverse() * csgCf -- the new union can sit elsewhere than the original
	local nV, nC = #mesh.positions, #mesh.faces
	local b = buffer.create(16 + nV * 12 + nC * 4 + nC * 8 + nC * 12)
	buffer.writestring(b, 0, "RBM1")
	buffer.writeu32(b, 4, nV)
	buffer.writeu32(b, 8, nC)
	buffer.writeu8(b, 12, 0) -- no UVs: union materials tile by studs (Blender box-maps them)
	buffer.writeu8(b, 13, 1)
	local o = 16
	for _, p in ipairs(mesh.positions) do
		local q = toPart * p
		buffer.writef32(b, o, finite(q.X))
		buffer.writef32(b, o + 4, finite(q.Y))
		buffer.writef32(b, o + 8, finite(q.Z))
		o += 12
	end
	for k = 1, nC do
		local vi = mesh.faces[k]
		assert(vi < nV, "vertex index out of range")
		buffer.writeu32(b, o, vi)
		o += 4
	end
	o += nC * 8 -- uvs (zero)
	for k = 1, nC do
		local nrm = mesh.normals[(mesh.faceNormals[k] or 0) + 1] or Vector3.yAxis
		local q = toPart:VectorToWorldSpace(nrm)
		buffer.writef32(b, o, finite(q.X))
		buffer.writef32(b, o + 4, finite(q.Y))
		buffer.writef32(b, o + 8, finite(q.Z))
		o += 12
	end
	return b, nV, nC // 3
end

---------------------------------------------------------------------------
-- scene description
---------------------------------------------------------------------------
local function v3(v)
	return { finite(v.X), finite(v.Y), finite(v.Z) }
end
local function c3(c)
	return { finite(c.R), finite(c.G), finite(c.B) }
end

-- read a property without ever erroring (missing / restricted / renamed properties)
local function getProp(inst, name)
	local ok, v = pcall(function()
		return (inst :: any)[name]
	end)
	if ok then
		return v
	end
	return nil
end

-- property value -> JSON-safe value (Color3/Vector3 -> arrays, enums -> names, sequences -> keypoints)
local function jsonValue(v)
	local t = typeof(v)
	if t == "number" then
		return finite(v)
	elseif t == "boolean" or t == "string" then
		return v
	elseif t == "Color3" then
		return c3(v)
	elseif t == "Vector3" then
		return v3(v)
	elseif t == "Vector2" then
		return { finite(v.X), finite(v.Y) }
	elseif t == "EnumItem" then
		return v.Name
	elseif t == "NumberRange" then
		return { finite(v.Min), finite(v.Max) }
	elseif t == "ColorSequence" then
		local out = {}
		for _, k in ipairs(v.Keypoints) do
			table.insert(out, { finite(k.Time), c3(k.Value) })
		end
		return out
	elseif t == "NumberSequence" then
		local out = {}
		for _, k in ipairs(v.Keypoints) do
			table.insert(out, { finite(k.Time), finite(k.Value) })
		end
		return out
	elseif t == "Content" then
		local ok, uri = pcall(function()
			return (v :: any).Uri
		end)
		return ok and uri or nil
	end
	return nil
end

-- every listed property that exists on inst, plus its attributes (missing ones are skipped)
local function dumpProps(inst, names)
	local out = {}
	for _, n in ipairs(names) do
		local v = jsonValue(getProp(inst, n))
		if v ~= nil then
			out[n] = v
		end
	end
	local ok, attrs = pcall(function()
		return inst:GetAttributes()
	end)
	if ok and attrs then
		for k, v in pairs(attrs) do
			local j = jsonValue(v)
			if j ~= nil then
				out["@" .. k] = j
			end
		end
	end
	return out
end

local LIGHT_PROPS = { "Brightness", "Color", "Enabled", "Range", "Shadows", "Angle", "Face" }
local LIGHTING_PROPS = {
	"Ambient", "OutdoorAmbient", "Brightness", "ColorShift_Top", "ColorShift_Bottom",
	"EnvironmentDiffuseScale", "EnvironmentSpecularScale", "ExposureCompensation",
	"GlobalShadows", "ShadowSoftness", "Technology", "LightingStyle", "PrioritizeLightingQuality",
	"ClockTime", "TimeOfDay", "GeographicLatitude", "FogColor", "FogStart", "FogEnd",
}
-- Lighting children (and Camera post effects) by class
local EFFECT_PROPS = {
	Sky = { "CelestialBodiesShown", "MoonAngularSize", "MoonTextureId", "SkyboxOrientation", "StarCount",
		"SunAngularSize", "SunTextureId", "SkyboxBk", "SkyboxDn", "SkyboxFt", "SkyboxLf", "SkyboxRt", "SkyboxUp" },
	Atmosphere = { "Color", "Decay", "Density", "Glare", "Haze", "Offset" },
	BloomEffect = { "Enabled", "Intensity", "Size", "Threshold" },
	ColorCorrectionEffect = { "Enabled", "Brightness", "Contrast", "Saturation", "TintColor" },
	ColorGradingEffect = { "Enabled", "TonemapperPreset" },
	SunRaysEffect = { "Enabled", "Intensity", "Spread" },
	DepthOfFieldEffect = { "Enabled", "FarIntensity", "FocusDistance", "InFocusRadius", "NearIntensity" },
	BlurEffect = { "Enabled", "Size" },
	Clouds = { "Enabled", "Color", "Cover", "Density" },
}

-- first non-empty asset reference among several property names; accepts
-- ContentId strings and the newer Content objects (uses Content.Uri)
local function firstUri(inst, names)
	for _, n in ipairs(names) do
		local v = getProp(inst, n)
		if typeof(v) == "Content" then
			local ok, uri = pcall(function()
				return (v :: any).Uri
			end)
			v = ok and uri or nil
		end
		if typeof(v) == "string" and v ~= "" then
			return v
		end
	end
	return nil
end

-- Studio prints "Asset … is not allowed to be forked" every time EditableMesh/EditableImage
-- is asked to open an asset you don't own. Like NExport, never ask unless we can PROVE
-- the asset is yours (or the experience owner's). Everything else goes to Blender's
-- downloader without Studio printing anything.
local function makeOwnershipCheck()
	local userId = 0
	pcall(function()
		userId = game:GetService("StudioService"):GetUserId()
	end)
	local ownerId, ownerIsGroup = game.CreatorId, game.CreatorType == Enum.CreatorType.Group
	local cache = {}

	local function lookup(numId)
		local info = tryAll({
			function()
				return (MarketplaceService :: any):GetProductInfoAsync(tonumber(numId), Enum.InfoType.Asset)
			end,
			function()
				return MarketplaceService:GetProductInfo(tonumber(numId), Enum.InfoType.Asset)
			end,
		})
		local mine = false -- unknown -> don't touch it (no error spam)
		if type(info) == "table" and type(info.Creator) == "table" then
			local c = info.Creator
			local cid = tonumber(c.CreatorTargetId) or tonumber(c.Id) or -1
			local isGroup = tostring(c.CreatorType) == "Group" or tostring(c.CreatorType) == "Enum.CreatorType.Group"
			mine = (not isGroup and userId ~= 0 and cid == userId) or (isGroup == ownerIsGroup and cid == ownerId and cid > 0)
		end
		cache[numId] = mine
	end

	local check = {}
	-- look many ids up in parallel (one web call each) before the export loop
	function check.prefetch(ids)
		local pending = 0
		for _, id in ipairs(ids) do
			if cache[id] == nil then
				pending += 1
				task.spawn(function()
					pcall(lookup, id)
					if cache[id] == nil then
						cache[id] = false
					end
					pending -= 1
				end)
				if pending >= 8 then
					repeat
						task.wait()
					until pending < 8
				end
			end
		end
		while pending > 0 do
			task.wait()
		end
	end
	function check.isMine(id)
		if cache[id] == nil then
			pcall(lookup, id)
		end
		return cache[id] == true
	end
	return check
end

local function collectParts(roots)
	local parts, seen = {}, {}
	local function add(p)
		if seen[p] then
			return
		end
		seen[p] = true
		if p:IsA("Terrain") then
			return
		end
		table.insert(parts, p)
	end
	for _, r in ipairs(roots) do
		if r:IsA("BasePart") then
			add(r)
		end
		for _, d in ipairs(r:GetDescendants()) do
			if d:IsA("BasePart") then
				add(d)
			end
		end
	end
	return parts
end

local function hasVisibleDecal(part)
	for _, ch in ipairs(part:GetChildren()) do
		if ch:IsA("Decal") and ch.Transparency < 1 then
			return true
		end
	end
	return false
end

local function exportInstances(roots, exportName)
	local warnings = {}
	local function warn(msg)
		table.insert(warnings, msg)
		log("WARN: " .. msg)
	end

	-- 1. connect
	progress("Connecting to Blender…")
	local pingOk, ping = pcall(request, "GET", "/ping")
	if not pingOk then
		error("Can't reach Blender on port " .. port .. ". Is Blender open with the Roblox Bridge add-on server running?\n" .. tostring(ping))
	end
	local canDownload = ping.can_download == true
	local ownership = makeOwnershipCheck()
	local isMine = ownership.isMine
	if ping.protocol ~= PROTOCOL then
		error(("Protocol mismatch (plugin %d, Blender %s). Update both sides."):format(PROTOCOL, tostring(ping.protocol)))
	end

	-- 2. walk instances
	local parts = collectParts(roots)
	if #parts == 0 then
		error("Nothing to export: the selection contains no parts.")
	end

	local stopAt = {}
	for _, r in ipairs(roots) do
		if r.Parent then
			stopAt[r.Parent] = true
		end
	end
	stopAt[workspace] = true
	stopAt[game] = true

	local ancestorIds, ancestors = {}, {}
	local nextId = 0
	local function ancestorId(inst)
		-- nearest non-BasePart ancestor chain, registered on demand
		if inst == nil or stopAt[inst] then
			return nil
		end
		if inst:IsA("BasePart") then
			return ancestorId(inst.Parent)
		end
		local id = ancestorIds[inst]
		if id then
			return id
		end
		nextId += 1
		id = nextId
		ancestorIds[inst] = id
		local parentId = ancestorId(inst.Parent)
		table.insert(ancestors, { id = id, name = inst.Name, class = inst.ClassName, parent = parentId })
		return id
	end

	local meshes, textures = {}, {}
	local variants = {}
	local function refMesh(s)
		local uri, key = normalizeId(s)
		if not uri then
			return nil
		end
		meshes[key] = meshes[key] or { uri = uri }
		return key
	end
	local function refTex(s)
		local uri, key = normalizeId(s)
		if not uri then
			return nil
		end
		textures[key] = textures[key] or { uri = uri }
		return key
	end

	local records = {}
	local counts = { union = 0, unionMesh = 0, truss = 0, clothing = 0, variant = 0 }

	-- MaterialVariant (explicit, or a MaterialService base-material override) -> tiled PBR maps
	-- every MaterialVariant in the place, wherever it lives (games often keep them in
	-- ReplicatedStorage/ServerStorage and move them into MaterialService at runtime)
	local variantIndex, variantByName = {}, {}
	do
		local svcs = { MaterialService }
		for _, n in ipairs({ "ReplicatedStorage", "ServerStorage", "Workspace", "Lighting", "StarterPlayer", "StarterGui", "ReplicatedFirst" }) do
			local ok, sv = pcall(function()
				return game:GetService(n)
			end)
			if ok and sv then
				table.insert(svcs, sv)
			end
		end
		for _, sv in ipairs(svcs) do
			local ok, desc = pcall(function()
				return sv:GetDescendants()
			end)
			if ok then
				for _, d in ipairs(desc) do
					if d:IsA("MaterialVariant") then
						local base = getProp(d, "BaseMaterial")
						local bn = typeof(base) == "EnumItem" and base.Name or "?"
						local k = bn .. "/" .. d.Name
						if variantIndex[k] == nil or sv == MaterialService then
							variantIndex[k] = d
						end
						variantByName[d.Name] = variantByName[d.Name] or d
					end
				end
			end
		end
	end
	local missingVariants = {}

	local function variantFor(part)
		local mat = part.Material.Name
		local name = getProp(part, "MaterialVariant")
		if typeof(name) ~= "string" or name == "" then
			local ok, o = pcall(function()
				return MaterialService:GetBaseMaterialOverride(part.Material)
			end)
			name = (ok and typeof(o) == "string" and o ~= "") and o or nil
		end
		-- an "override" that is just the built-in material's own name means: no variant
		if not name or name == mat then
			return nil
		end
		local ck = mat .. "/" .. name
		if variants[ck] == nil then
			local mv = variantIndex[ck] or variantByName[name]
			if not mv then
				local ok, r = pcall(function()
					return MaterialService:GetMaterialVariant(part.Material, name)
				end)
				mv = ok and r or nil
			end
			if mv then
				local pattern = getProp(mv, "MaterialPattern")
				variants[ck] = {
					name = name,
					color = refTex(firstUri(mv, { "ColorMapContent", "ColorMap" })),
					normal = refTex(firstUri(mv, { "NormalMapContent", "NormalMap" })),
					roughness = refTex(firstUri(mv, { "RoughnessMapContent", "RoughnessMap" })),
					metalness = refTex(firstUri(mv, { "MetalnessMapContent", "MetalnessMap" })),
					studsPerTile = tonumber(getProp(mv, "StudsPerTile")) or 10,
					pattern = typeof(pattern) == "EnumItem" and pattern.Name or "Regular",
				}
			else
				variants[ck] = false
				missingVariants[ck] = true
			end
		end
		if variants[ck] == false then
			counts.variant += 1
			return nil
		end
		return variants[ck]
	end
	for i, part in ipairs(parts) do
		local invisible = part.Transparency >= 0.999 and not hasVisibleDecal(part)
		if not (skipInvisible and invisible) then
			local rec = {
				name = part.Name,
				fullName = part:GetFullName(),
				class = part.ClassName,
				parent = ancestorId(part.Parent),
				cf = { part.CFrame:GetComponents() },
				size = v3(part.Size),
				color = c3(part.Color),
				transparency = part.Transparency,
				reflectance = part.Reflectance,
				material = part.Material.Name,
				decals = {},
			}
			if getProp(part, "CastShadow") == false then
				rec.castShadow = false -- Unity turns this part's shadow casting off too
			end
			for k, n in ipairs(rec.cf) do
				rec.cf[k] = finite(n)
			end

			if part:IsA("MeshPart") then
				rec.shape = "Mesh"
				rec.meshKind = "MeshPart"
				rec.mesh = refMesh(part.MeshId)
				rec.texture = refTex(firstUri(part, { "TextureContent", "TextureID" }))
				rec.doubleSided = getProp(part, "DoubleSided") == true
				if not rec.mesh then
					warn(part:GetFullName() .. ": MeshPart has no MeshId (exported as box)")
					rec.shape = "Block"
				end
			elseif part:IsA("PartOperation") then
				rec.shape = "Union"
				local ok, b, nv, nt = pcall(extractUnion, part)
				if ok then
					local key = ("U%08x%x"):format(fnv1a(b), buffer.len(b))
					meshes[key] = meshes[key] or { uri = "union://" .. key .. "/csg", union = b, verts = nv, tris = nt }
					rec.shape = "Mesh"
					rec.meshKind = "Union"
					rec.mesh = key
					counts.unionMesh += 1
				else
					counts.union += 1
					warn(part:GetFullName() .. ": union geometry not readable (" .. tostring(b) .. "), exported as a box")
				end
			elseif part:IsA("WedgePart") then
				rec.shape = "Wedge"
			elseif part:IsA("CornerWedgePart") then
				rec.shape = "CornerWedge"
			elseif part:IsA("TrussPart") then
				rec.shape = "Block"
				counts.truss += 1
			elseif part:IsA("Part") then
				rec.shape = part.Shape.Name
			else
				rec.shape = "Block"
			end

			if not part:IsA("MeshPart") then
				local sm = part:FindFirstChildOfClass("SpecialMesh")
				local bm = part:FindFirstChildOfClass("BlockMesh")
				if sm then
					rec.special = {
						type = sm.MeshType.Name,
						scale = v3(sm.Scale),
						offset = v3(sm.Offset),
						vertexColor = v3(sm.VertexColor),
					}
					if sm.MeshType == Enum.MeshType.FileMesh then
						rec.mesh = refMesh(sm.MeshId)
						rec.texture = refTex(sm.TextureId)
						rec.shape = rec.mesh and "Mesh" or "Block"
						rec.meshKind = "SpecialMesh"
					end
				elseif bm then
					rec.special = { type = "Brick", scale = v3(bm.Scale), offset = v3(bm.Offset), vertexColor = { 1, 1, 1 } }
				end
			end

			local sa = part:FindFirstChildOfClass("SurfaceAppearance")
			if sa then
				-- every property read defensively: one restricted property must not abort the export
				local tint = getProp(sa, "Color")
				local alphaMode = getProp(sa, "AlphaMode")
				local eTint = getProp(sa, "EmissiveTint")
				rec.surface = {
					color = refTex(firstUri(sa, { "ColorMapContent", "ColorMap" })),
					normal = refTex(firstUri(sa, { "NormalMapContent", "NormalMap" })),
					roughness = refTex(firstUri(sa, { "RoughnessMapContent", "RoughnessMap" })),
					metalness = refTex(firstUri(sa, { "MetalnessMapContent", "MetalnessMap" })),
					emissive = refTex(firstUri(sa, { "EmissiveMaskContent", "EmissiveMask" })),
					emissiveTint = typeof(eTint) == "Color3" and c3(eTint) or { 1, 1, 1 },
					emissiveStrength = tonumber(getProp(sa, "EmissiveStrength")) or 1,
					alphaMode = typeof(alphaMode) == "EnumItem" and alphaMode.Name or "Overlay",
					tint = typeof(tint) == "Color3" and c3(tint) or { 1, 1, 1 },
				}
				local su = rec.surface
				if not (su.color or su.normal or su.roughness or su.metalness or su.emissive) then
					warn(part:GetFullName() .. ": SurfaceAppearance maps not readable by plugins - using part color")
					rec.surface = nil
				end
			end

			for _, ch in ipairs(part:GetChildren()) do
				if ch:IsA("Decal") and ch.Transparency < 1 then
					local tex = refTex(firstUri(ch, { "ColorMapContent", "ColorMap", "TextureContent", "Texture" }))
					if tex then
						local uvOffset = getProp(ch, "UVOffset")
						local uvScale = getProp(ch, "UVScale")
						local eTint = getProp(ch, "EmissiveTint")
						local d = {
							kind = ch:IsA("Texture") and "Texture" or "Decal",
							face = ch.Face.Name,
							texture = tex,
							normal = refTex(firstUri(ch, { "NormalMapContent", "NormalMap" })),
							roughness = refTex(firstUri(ch, { "RoughnessMapContent", "RoughnessMap" })),
							metalness = refTex(firstUri(ch, { "MetalnessMapContent", "MetalnessMap" })),
							emissive = refTex(firstUri(ch, { "EmissiveMaskContent", "EmissiveMask" })),
							emissiveTint = typeof(eTint) == "Color3" and c3(eTint) or { 1, 1, 1 },
							emissiveStrength = tonumber(getProp(ch, "EmissiveStrength")) or 1,
							color = c3(ch.Color3),
							transparency = ch.Transparency,
							zindex = ch.ZIndex,
							rotation = tonumber(getProp(ch, "Rotation")) or 0,
							uvOffset = typeof(uvOffset) == "Vector2" and { uvOffset.X, uvOffset.Y } or { 0, 0 },
							uvScale = typeof(uvScale) == "Vector2" and { uvScale.X, uvScale.Y } or { 1, 1 },
						}
						if ch:IsA("Texture") then
							d.studsU = ch.StudsPerTileU
							d.studsV = ch.StudsPerTileV
							d.offsetU = ch.OffsetStudsU
							d.offsetV = ch.OffsetStudsV
						end
						table.insert(rec.decals, d)
					end
				elseif ch:IsA("Clothing") or ch:IsA("ShirtGraphic") then
					counts.clothing += 1
				end
			end
			if not rec.surface and not rec.texture then
				rec.variant = variantFor(part)
			end

			table.insert(records, rec)
		end
		if i % 500 == 0 then
			progress(("Scanning parts %d/%d"):format(i, #parts))
			task.wait()
		end
	end
	for _, r in ipairs(roots) do
		for _, d in ipairs(r:GetDescendants()) do
			if d:IsA("Clothing") or d:IsA("ShirtGraphic") then
				counts.clothing += 1
			end
		end
	end
	if counts.unionMesh > 0 then
		log(("%d union(s) exported with their real geometry."):format(counts.unionMesh))
	end
	if counts.union > 0 then
		warn(("%d union(s) couldn't be read and are exported as boxes named UNION_*. Fix: in Studio select the union, Right-click → Export Selection (.obj) or convert it to a MeshPart, then re-export."):format(counts.union))
	end
	if counts.truss > 0 then
		warn(("%d TrussPart(s) exported as plain boxes."):format(counts.truss))
	end
	if counts.clothing > 0 then
		warn("Shirts/Pants/ShirtGraphics found: Roblox composites clothing at runtime and does not expose the result; clothing textures are NOT exported.")
	end
	if counts.variant > 0 then
		local names = {}
		for k in pairs(missingVariants) do
			table.insert(names, k)
		end
		table.sort(names)
		warn(("%d part(s) use MaterialVariant(s) that don't exist anywhere in this place (base color only): %s"):format(
			counts.variant, table.concat(names, ", "):sub(1, 400)))
	end
	do
		local found = 0
		for _ in pairs(variantIndex) do
			found += 1
		end
		log(("Found %d MaterialVariant(s) in the place."):format(found))
	end

	-- 2b. lights (PointLight / SpotLight / SurfaceLight on parts or attachments) + Lighting
	local lights = {}
	do
		local seen = {}
		local function addLight(l)
			if seen[l] then
				return
			end
			seen[l] = true
			local par = l.Parent
			local cf, size, owner
			if par and par:IsA("BasePart") then
				cf, size, owner = par.CFrame, par.Size, par
			elseif par and par:IsA("Attachment") then
				cf, size = par.WorldCFrame, Vector3.zero
				owner = par.Parent and par.Parent:IsA("BasePart") and par.Parent or nil
			else
				return -- Roblox only lights direct children of parts/attachments
			end
			local rec = {
				kind = l.ClassName,
				name = l.Name,
				path = l:GetFullName(),
				enabled = getProp(l, "Enabled") ~= false,
				brightness = tonumber(getProp(l, "Brightness")) or 1,
				color = typeof(getProp(l, "Color")) == "Color3" and c3(l.Color) or { 1, 1, 1 },
				range = tonumber(getProp(l, "Range")) or 8,
				shadows = getProp(l, "Shadows") == true,
				props = dumpProps(l, LIGHT_PROPS), -- every Roblox value, kept on the Unity light
			}
			local pos = cf.Position
			local dir = Vector3.new(0, -1, 0)
			if l:IsA("SpotLight") or l:IsA("SurfaceLight") then
				local face = getProp(l, "Face")
				local nid = typeof(face) == "EnumItem" and face or Enum.NormalId.Front
				local local_n = Vector3.FromNormalId(nid)
				dir = cf:VectorToWorldSpace(local_n)
				rec.angle = tonumber(getProp(l, "Angle")) or 90
				-- on a part, Spot- and SurfaceLights emanate from the chosen face (creator-docs)
				pos = cf:PointToWorldSpace(local_n * (size / 2))
				rec.face = nid.Name
				if l:IsA("SurfaceLight") and size.Magnitude > 0 then
					-- the lit face's width x height in studs (a SurfaceLight shines from the whole face)
					local a = Vector3.new(math.abs(local_n.X), math.abs(local_n.Y), math.abs(local_n.Z))
					local s = size * (Vector3.one - a)
					local dims = {}
					for _, c in ipairs({ s.X, s.Y, s.Z }) do
						if c > 0 then
							table.insert(dims, finite(c))
						end
					end
					rec.faceSize = dims
				end
			end
			rec.owner = owner and owner:GetFullName() or nil
			rec.position = v3(pos)
			rec.direction = v3(dir)
			table.insert(lights, rec)
		end
		for _, r in ipairs(roots) do
			if r:IsA("Light") then
				addLight(r)
			end
			for _, d in ipairs(r:GetDescendants()) do
				if d:IsA("PointLight") or d:IsA("SpotLight") or d:IsA("SurfaceLight") then
					addLight(d)
				end
			end
		end
		if #lights > 0 then
			log(("Found %d light(s)."):format(#lights))
		end
	end
	local function col(name)
		local v = getProp(Lighting, name)
		return typeof(v) == "Color3" and c3(v) or nil
	end
	local lightingInfo = {
		sunDirection = (function()
			local ok, d = pcall(function()
				return Lighting:GetSunDirection()
			end)
			return ok and v3(d) or { 0, 1, 0 }
		end)(),
		brightness = tonumber(getProp(Lighting, "Brightness")) or 2,
		ambient = col("Ambient"),
		outdoorAmbient = col("OutdoorAmbient"),
		colorShiftTop = col("ColorShift_Top"),
		fogColor = col("FogColor"),
		fogStart = tonumber(getProp(Lighting, "FogStart")) or 0,
		fogEnd = tonumber(getProp(Lighting, "FogEnd")) or 100000,
		clockTime = tonumber(getProp(Lighting, "ClockTime")) or 14,
		environmentDiffuseScale = tonumber(getProp(Lighting, "EnvironmentDiffuseScale")) or 0,
		globalShadows = getProp(Lighting, "GlobalShadows") ~= false,
	}
	lightingInfo.exposure = tonumber(getProp(Lighting, "ExposureCompensation")) or 0
	lightingInfo.environmentSpecularScale = tonumber(getProp(Lighting, "EnvironmentSpecularScale")) or 0
	lightingInfo.colorShiftBottom = col("ColorShift_Bottom")
	lightingInfo.shadowSoftness = tonumber(getProp(Lighting, "ShadowSoftness")) or 0.2
	-- the complete Lighting service + every effect/sky/atmosphere/clouds object, as-is
	lightingInfo.props = dumpProps(Lighting, LIGHTING_PROPS)
	lightingInfo.effects = {}
	do
		local holders = { Lighting }
		local cam = workspace.CurrentCamera
		if cam then
			table.insert(holders, cam)
		end
		local terrain = workspace:FindFirstChildOfClass("Terrain")
		if terrain then
			table.insert(holders, terrain)
		end
		for _, h in ipairs(holders) do
			for _, ch in ipairs(h:GetChildren()) do
				local names = EFFECT_PROPS[ch.ClassName]
				if names then
					table.insert(lightingInfo.effects, {
						class = ch.ClassName,
						name = ch.Name,
						parent = h.Name,
						props = dumpProps(ch, names),
					})
				end
				if ch:IsA("ColorGradingEffect") and h == Lighting and getProp(ch, "Enabled") ~= false then
					local tp = getProp(ch, "TonemapperPreset")
					lightingInfo.tonemapper = typeof(tp) == "EnumItem" and tp.Name or "Default"
				end
			end
		end
	end
	do
		local sky = Lighting:FindFirstChildOfClass("Sky")
		if sky then
			lightingInfo.sky = {
				bk = refTex(firstUri(sky, { "SkyboxBk" })),
				dn = refTex(firstUri(sky, { "SkyboxDn" })),
				ft = refTex(firstUri(sky, { "SkyboxFt" })),
				lf = refTex(firstUri(sky, { "SkyboxLf" })),
				rt = refTex(firstUri(sky, { "SkyboxRt" })),
				up = refTex(firstUri(sky, { "SkyboxUp" })),
			}
		end
		local bloom = Lighting:FindFirstChildOfClass("BloomEffect")
		if bloom and getProp(bloom, "Enabled") ~= false then
			lightingInfo.bloom = {
				intensity = tonumber(getProp(bloom, "Intensity")) or 1,
				size = tonumber(getProp(bloom, "Size")) or 24,
				threshold = tonumber(getProp(bloom, "Threshold")) or 2,
			}
		end
		local cc = Lighting:FindFirstChildOfClass("ColorCorrectionEffect")
		if cc and getProp(cc, "Enabled") ~= false then
			lightingInfo.colorCorrection = {
				brightness = tonumber(getProp(cc, "Brightness")) or 0,
				contrast = tonumber(getProp(cc, "Contrast")) or 0,
				saturation = tonumber(getProp(cc, "Saturation")) or 0,
				tint = typeof(getProp(cc, "TintColor")) == "Color3" and c3(cc.TintColor) or { 1, 1, 1 },
			}
		end
	end
	do
		local atm = Lighting:FindFirstChildOfClass("Atmosphere")
		if atm then
			lightingInfo.atmosphere = {
				density = tonumber(getProp(atm, "Density")) or 0,
				color = typeof(getProp(atm, "Color")) == "Color3" and c3(atm.Color) or nil,
				haze = tonumber(getProp(atm, "Haze")) or 0,
			}
		end
	end

	-- 3. begin session
	local begin = request("POST", "/begin", { name = exportName, protocol = PROTOCOL })
	local session = begin.session
	log(("Session %s → %s"):format(session, tostring(begin.dir)))

	-- 4. assets. Blender keeps every downloaded/received asset in a per-machine cache and, when it can
	-- download (it has a login), fetches the rest itself: compressed files, 6 at a time. Reading them
	-- here instead (EditableMesh/EditableImage, re-encoded in Lua, sent in base64 through HttpService's
	-- 500 requests/minute) is the slow part, so Studio only reads what Blender asks for afterwards.
	local failed = 0
	local function sendMesh(key)
		local m = meshes[key]
		if m.union then -- read from the union during the scan
			local sok, serr = pcall(sendBlob, session, "mesh", key, m.union, { verts = m.verts, tris = m.tris })
			m.union = nil -- not part of the scene JSON
			m.ok = sok
			if not sok then
				m.err = "upload failed: " .. tostring(serr)
				failed += 1
			end
			task.wait()
			return
		end
		local numId = m.uri:match("^rbxassetid://(%d+)$")
		local em, err
		if numId and isMine(numId) then
			em, err = loadEditableMesh(m.uri)
		else
			err = "not owned by you"
			m.foreign = true
		end
		if em then
			local ok, b, nv, nt, skipped = pcall(extractMesh, em)
			pcall(function()
				em:Destroy()
			end)
			if ok then
				local sok, serr = pcall(sendBlob, session, "mesh", key, b, { verts = nv, tris = nt })
				if sok then
					m.ok = true
					if skipped > 0 then
						warn(("%s: %d face(s) with more than 4 corners skipped"):format(m.uri, skipped))
					end
				else
					m.ok = false
					m.err = "upload failed: " .. tostring(serr)
				end
			else
				m.ok = false
				m.err = "read failed: " .. tostring(b)
			end
		else
			m.ok = false
			m.err = err
		end
		if not m.ok then
			failed += 1
		end
		task.wait()
	end
	local function sendTexture(key)
		local t = textures[key]
		local numId = t.uri:match("^rbxassetid://(%d+)$")
		local img, err
		if numId and isMine(numId) then
			img, err = loadEditableImage(t.uri)
		else
			err = "not owned by you"
			t.foreign = true
		end
		if img then
			local ok, perr = pcall(function()
				local size = img.Size
				local buf = img:ReadPixelsBuffer(Vector2.zero, size)
				sendBlob(session, "texture", key, buf, { w = size.X, h = size.Y, format = "rgba8" })
			end)
			pcall(function()
				img:Destroy()
			end)
			t.ok = ok
			if not ok then
				t.err = tostring(perr)
			end
		else
			t.ok = false
			t.err = err
		end
		if not t.ok then
			failed += 1
		end
		task.wait()
	end
	-- read a list of assets here (ownership looked up first so Studio is only asked for what it allows)
	local function readHere(mKeys, tKeys)
		local ids = {}
		for _, pair in ipairs({ { mKeys, meshes }, { tKeys, textures } }) do
			for _, k in ipairs(pair[1]) do
				local n = pair[2][k].uri:match("^rbxassetid://(%d+)$")
				if n then
					table.insert(ids, n)
				end
			end
		end
		if #ids > 0 then
			progress(("Checking who owns %d asset(s)…"):format(#ids))
			ownership.prefetch(ids)
		end
		for i, key in ipairs(mKeys) do
			progress(("Mesh %d/%d  %s"):format(i, #mKeys, meshes[key].uri))
			sendMesh(key)
		end
		for i, key in ipairs(tKeys) do
			progress(("Texture %d/%d  %s"):format(i, #tKeys, textures[key].uri))
			sendTexture(key)
		end
	end

	local function sortedKeys(tbl)
		local keys = {}
		for k in pairs(tbl) do
			table.insert(keys, k)
		end
		table.sort(keys)
		return keys
	end
	local mAll, tAll = sortedKeys(meshes), sortedKeys(textures)
	local cached = { meshes = {}, textures = {} }
	if ping.cache then
		local ok, have = pcall(request, "POST", "/have", { meshes = mAll, textures = tAll })
		if ok and type(have) == "table" then
			for _, kind in ipairs({ "meshes", "textures" }) do
				for _, k in ipairs(have[kind] or {}) do
					cached[kind][k] = true
				end
			end
		end
	end
	local mRead, tRead, nCached = {}, {}, 0
	for _, pair in ipairs({ { mAll, "meshes", mRead, meshes }, { tAll, "textures", tRead, textures } }) do
		local keys, kind, out, tbl = pair[1], pair[2], pair[3], pair[4]
		for _, k in ipairs(keys) do
			if cached[kind][k] then
				nCached += 1
			elseif not ping.cache or not tbl[k].uri:match("^rbxassetid://%d+$") then
				table.insert(out, k)
			end
		end
	end
	if nCached > 0 then
		log(("%d asset(s) already in Blender's cache."):format(nCached))
	end
	if ping.cache then
		-- Blender tries every asset id first (many download without a login) and asks back for the rest
		log("Blender downloads the assets; Studio only reads what Roblox won't give it.")
	end
	readHere(mRead, tRead)

	if failed > 0 then
		log(("%d asset(s) aren't yours, so Blender downloads them instead."):format(failed))
		if not canDownload then
			log("Tip: some downloads need your login - add an Open Cloud API key in Blender → Preferences → Add-ons → Roblox Bridge.")
		end
	end

	-- 6. scene (sent as a blob too so huge places stay under the limit)
	progress("Sending scene description…")
	for _, m in pairs(meshes) do
		m.union = nil -- union mesh buffers were sent as blobs; they can't go into the JSON
	end
	local scene = {
		protocol = PROTOCOL,
		name = exportName,
		place = game.Name,
		placeId = game.PlaceId,
		ancestors = ancestors,
		parts = records,
		meshes = meshes,
		textures = textures,
		warnings = warnings,
		use2022Materials = getProp(MaterialService, "Use2022Materials") ~= false,
		lights = lights,
		lighting = lightingInfo,
	}
	local sceneJson = HttpService:JSONEncode(scene)
	sendBlob(session, "scene", "scene", buffer.fromstring(sceneJson), nil)
	request("POST", "/commit", { session = session, fallback = true })

	-- 7. wait for Blender to build
	local last = ""
	for _ = 1, 3600 do
		task.wait(0.5)
		local ok, st = pcall(request, "GET", "/status?session=" .. session)
		if ok then
			if st.message and st.message ~= last then
				last = st.message
				progress("Blender: " .. last)
			end
			if st.state == "done" or st.state == "error" then
				return st
			end
			if st.state == "need" and type(st.need) == "table" then
				-- Roblox wouldn't give Blender these; read the ones that are yours here
				local mk, tk = {}, {}
				for _, k in ipairs(st.need.meshes or {}) do
					if meshes[k] then
						table.insert(mk, k)
					end
				end
				for _, k in ipairs(st.need.textures or {}) do
					if textures[k] then
						table.insert(tk, k)
					end
				end
				log(("Blender couldn't download %d asset(s); reading yours in Studio."):format(#mk + #tk))
				pcall(readHere, mk, tk)
				request("POST", "/continue", { session = session })
			end
			if st.state == "unknown" then
				error("Blender lost this export session (was Blender restarted?). Export again.")
			end
		end
	end
	error("Timed out waiting for Blender to finish building.")
end

---------------------------------------------------------------------------
-- buttons
---------------------------------------------------------------------------
local busy = false
local function runExport(roots, name)
	if busy then
		log("An export is already running.")
		return
	end
	busy = true
	logLines = {}
	log(("Exporting %d root object(s) as '%s'"):format(#roots, name))
	local t0 = os.clock()
	local ok, res = pcall(exportInstances, roots, name)
	if ok then
		if res.state == "done" then
			log(("DONE in %.1fs: %s"):format(os.clock() - t0, tostring(res.message)))
		else
			log("BLENDER ERROR: " .. tostring(res.message))
		end
		for _, w in ipairs(res.warnings or {}) do
			log("  • " .. tostring(w))
		end
		progress(res.state == "done" and "Done ✔" or "Failed ✖ (see log)")
	else
		log("ERROR: " .. tostring(res))
		progress("Failed ✖ (see log)")
	end
	busy = false
end

pingBtn.MouseButton1Click:Connect(function()
	task.spawn(function()
		local ok, res = pcall(request, "GET", "/ping")
		if ok then
			log(("Connected: Blender %s, add-on %s, protocol %s"):format(tostring(res.blender), tostring(res.addon), tostring(res.protocol)))
			log("Exports are saved under: " .. tostring(res.export_root))
			progress("Connected ✔")
		else
			log("Connection failed: " .. tostring(res))
			log("Start Blender, open the sidebar (N) → Roblox tab → Start Server. Port must match.")
			progress("Not connected ✖")
		end
	end)
end)

exportBtn.MouseButton1Click:Connect(function()
	local sel = Selection:Get()
	if #sel == 0 then
		log("Select something first (a Model, Folder or Parts).")
		return
	end
	local name = #sel == 1 and sel[1].Name or (sel[1].Name .. "_and_" .. (#sel - 1) .. "_more")
	task.spawn(runExport, sel, name)
end)

exportWsBtn.MouseButton1Click:Connect(function()
	local roots = {}
	for _, ch in ipairs(workspace:GetChildren()) do
		if not ch:IsA("Terrain") and not ch:IsA("Camera") then
			table.insert(roots, ch)
		end
	end
	task.spawn(runExport, roots, game.Name ~= "" and game.Name or "Workspace")
end)

log("Roblox → Blender Bridge loaded. Port " .. port .. ".")
