# Part 2: Blender → Unity

Exports a model imported by [Part 1](../roblox-to-blender/README.md) from Blender, then imports it into Unity with its materials, textures and lights.

| Piece | File |
|---|---|
| Blender add-on "Roblox Bridge -> Unity" (needs Part 1's add-on) | `blender-addon/roblox_bridge_unity/` (release: `roblox_bridge_unity.zip`) |
| Unity package "Roblox Bridge Importer" | `unity-package/` (release: `com.robloxbridge.importer.tgz`) |

Tested with Unity 6 (6000.6), with the Universal Render Pipeline (URP) or the Built-in pipeline.

> **Coming soon: Roblox lighting in Unity.** It will add Roblox's own light falloff, spotlight cone, colours, tonemapper, sky light and ambient, support for maps with hundreds of lamps, and light baking for standalone VR (Quest). For now, the model's lights come in as plain Unity Point and Spot lights.

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
   - **Import:** copies the export into `Assets/Roblox/<name>/`, builds the materials (`<name>_Materials/`), creates the lights and places the model in the open scene.
3. **Re-exporting** the same model updates it in place. With *Keep materials I edited* ticked, your material changes survive. **Rebuild materials of selected** remakes them from the export.

## What comes over
- Every part and mesh, at Roblox's exact size and position: 1 stud = 0.28 m, Roblox's avatar scale.
- Materials (URP Lit, or Standard on the Built-in pipeline): colour, normal, metallic/smoothness and emission maps; opaque, cutout or transparent.
- PointLight, SpotLight and SurfaceLight as Unity Point and Spot lights, with Roblox's colour, range, angle and shadow setting.
- Parts with CastShadow off don't cast shadows. Decals don't either.
