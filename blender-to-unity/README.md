# Part 2: Blender → Unity

Exports a model imported by [Part 1](../roblox-to-blender/README.md) from Blender, then imports it into Unity looking like it does in Roblox. It uses Roblox's own light falloff, spotlight cone, colours, tonemapper and sky.

| Piece | File |
|---|---|
| Blender add-on "Roblox Bridge -> Unity" (needs Part 1's add-on) | `blender-addon/roblox_bridge_unity/` (release: `roblox_bridge_unity.zip`) |
| Unity package "Roblox Bridge Importer" | `unity-package/` (release: `com.robloxbridge.importer.tgz`) |

Tested with Unity 6 (6000.6) and the Universal Render Pipeline (URP).

## Install
1. **Blender:** install and tick Part 1's add-on first. Then *Edit → Preferences → Add-ons → Install from Disk…*, pick `roblox_bridge_unity.zip`, and tick **Roblox Bridge -> Unity**.
2. **Unity**, once per project, using either option:
   - *Window → Package Manager → + → Install package from git URL…* and paste
     `https://github.com/obaba-samich/roblox-blender-bridge.git?path=/blender-to-unity/unity-package`
   - or *+ → Install package from tarball…* and pick `com.robloxbridge.importer.tgz`.

## Use
1. **Blender** (Roblox tab → *Export to Unity*): select the model, then click **Validate for Unity** and **Export FBX for Unity**.
   - It saves to `~/RobloxExports/Unity/<name>/`: `<name>.fbx`, `<name>.roblox.json` and `<name>_Textures/`.
   - To export without the Blender interface: `blender -b --factory-startup --python tools/unity_from_export.py -- <saved export folder>`.
2. **Unity:** open **Window → Roblox Bridge**.
   - **Export folder:** click **Browse…** and pick `~/RobloxExports/Unity/<name>`, or `~/RobloxExports/Unity` to choose among all of them.
   - **Import:** copies the export into `Assets/Roblox/<name>/`, builds the materials, creates the lights, places the model in the open scene and applies the Roblox sun, ambient and fog.
3. **Match Roblox's look** (in the same window):
   - Keep **Roblox style** and **Exact Roblox formulas** on, set **Colour curve** to **Roblox**, and leave every × slider at 1.
   - **Bake sky lighting** (save the scene first): sky light is then blocked by walls and roofs, as in Roblox. **Sky leak indoors** sets how much sky gets into rooms.
   - To measure your place's sky: open it in Studio at graphics quality Level 21 and paste `tools/calibrate_ambient.lua` into the Command Bar. Screenshot each stage it shows, then run
     `blender -b --factory-startup --python tools/fit_ambient.py -- <screenshots> -o <Unity project>/Assets/Roblox/<Model>/<Model>.ambient.json`
4. **Re-exporting** the same model updates it in place. With *Keep materials I edited* ticked, your material changes survive.

## More tools (Tools → Roblox Bridge)
- **Lamp grid → Build / On / Off:** Roblox's lamps without URP's per-object light limit, with static shadows. Use it for maps with many lights, and on Quest/Android.
- **Bake lamp grid into lightmaps:** bakes the lamp grid's light into lightmaps. Use it on standalone VR (Quest): baked light costs almost nothing to draw. Characters and other moving objects still get live lamp light.
- **Map scale:** the map at 100/75/50/25% of its Roblox size. The lights, the look and the lamp grid follow. Roblox maps often feel big in VR.

## Notes
- **Linux:** run Unity on **Vulkan** (Player Settings → Graphics APIs). On OpenGL it only draws 32 lights.
- **Quest/Android:** an HDR buffer plus a full-screen tonemap pass is expensive there. The generated *Roblox Lit* shader can tonemap per pixel instead: set the global shader float `_RbxShaderTonemap` to 1 and `_RbxTonemapExposure` to the Roblox Tonemap material's exposure. Then turn off HDR and the *Roblox Tonemap* renderer feature on the mobile renderer.
- The other calibration tools in `tools/` measure Roblox's lighting (`calibrate_lighting.lua` + `fit_lighting.py`), materials (`calibrate_materials.lua` + `fit_materials.py`) and wall lights (`calibrate_walllight.lua`). The values they found are already built in.
