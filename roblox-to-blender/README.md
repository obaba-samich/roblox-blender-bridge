# Part 1: Roblox → Blender

Sends a model from Roblox Studio to Blender with its real geometry, UVs, textures, materials, decals and lights.

| Piece | File |
|---|---|
| Roblox Studio plugin | `studio-plugin/RobloxBlenderBridge.lua` (release: `RobloxBlenderBridge.lua`) |
| Blender add-on "Roblox Bridge (Studio -> Blender)" | `blender-addon/roblox_bridge/` (release: `roblox_bridge.zip`) |

## Install
1. **Blender** (4.0 or newer; tested on 5.2): *Edit → Preferences → Add-ons → Install from Disk…*, pick `roblox_bridge.zip`, and tick **Roblox Bridge (Studio -> Blender)**.
2. **Roblox Studio:** *Plugins tab → Plugins Folder*, copy `RobloxBlenderBridge.lua` into that folder, and restart Studio. A **Blender Bridge** button appears in the Plugins tab.
3. *(Optional)* **API key**, for meshes owned by other creators that Roblox only gives out with a login:
   - Go to create.roblox.com → Open Cloud → API Keys and create a key on your **personal** account.
   - Add **legacy-asset** with the scope **`legacy-asset:manage`**, and allow IP `0.0.0.0/0`.
   - In Blender's add-on preferences, paste the key and click **Test API key / cookie**. The key is stored only in your Blender preferences.

## Use
1. Open **Blender**, press **N** in the 3D view and open the **Roblox** tab. It should say *Listening on localhost:34873*.
2. In **Studio**: Plugins tab → **Blender Bridge**. The first time, click **Test connection** and allow the localhost request.
3. Select a model, folder or parts and click **Export Selection → Blender**. Wait for **DONE** in the plugin log.
4. The model appears in Blender.
   - Every export is also saved in `~/RobloxExports/<name>_<time>/`. **Import Saved Export** rebuilds it any time without Studio.
   - Downloaded meshes and textures are cached in `~/RobloxExports/_cache/`, so each one is fetched only once.

## What gets exported
- **Geometry:** MeshParts, SpecialMeshes, every part shape, and decals/Textures (including PBR decals, rotation, UV offset/scale). Scale and rotation are exact.
- **Unions:** exported with their real shape, in the union's single colour.
- **Textures and materials:**
  - SurfaceAppearance (every alpha mode, emissive) and MaterialVariants.
  - Roblox's built-in materials (Wood, Brick, Metal…): the real textures, tiled every 8 studs.
- **UVs:** every mesh gets non-overlapping UV0 (textures) and UV2 (lightmaps). Mirrored or overlapping UVs are rebaked.
- **Lights and Lighting:** PointLight, SpotLight and SurfaceLight, plus the sun, ambient and fog.
- **Assets owned by other creators:** Studio never touches them, so there are no "not allowed" errors. Blender downloads them instead; textures always work, and some meshes need the API key.

## Known limits
These are also listed in `report.txt` in each export folder.

| Case | Result |
|---|---|
| A union that can't be read | A `UNION_*` box |
| Login-only meshes without an API key | `MISSING_*` boxes |
| Clothing, terrain, skinning/bones | Not exported |

After updating the plugin or add-on, restart Studio and Blender.

Next step: [Part 2: Blender → Unity](../blender-to-unity/README.md).
