# Roblox Blender Bridge

Takes a Roblox Studio model to Blender, then to Unity, with its real geometry, textures, materials and lights.

It comes in two parts. Install only the first if you just want your Roblox models in Blender.

| Part | What it does | You install |
|---|---|---|
| [**1. Roblox → Blender**](roblox-to-blender/README.md) | Studio sends the selected model to Blender, which builds it with clean UVs and materials | a Roblox Studio plugin + a Blender add-on |
| [**2. Blender → Unity**](blender-to-unity/README.md) | Blender exports it for Unity; Unity imports it with its materials and lights | a Blender add-on + a Unity package |

**Coming soon:** Roblox lighting in Unity, so the map looks the way it does in Roblox. This includes Roblox's light falloff, colours, tonemapper and sky, maps with hundreds of lamps, and light baking for Quest.

## Download
Get the files from the [latest release](https://github.com/obaba-samich/roblox-blender-bridge/releases/latest):
- Part 1: `RobloxBlenderBridge.lua` (Studio) and `roblox_bridge.zip` (Blender)
- Part 2: `roblox_bridge_unity.zip` (Blender) and `com.robloxbridge.importer.tgz` (Unity), or the Unity git URL in [Part 2's guide](blender-to-unity/README.md#install)

To build them yourself: `python3 tools/build_release.py` (they go into `dist/`).

## Requirements
- Roblox Studio
- Blender 4.0 or newer (tested on 5.2)
- Unity (tested on Unity 6, 6000.6, with URP)

## Tests
Blender, headless:
- `blender -b --factory-startup --python tests/test_pipeline.py`
- `blender -b --factory-startup --python tests/test_addon.py`

## License
MIT, see [LICENSE](LICENSE). Not affiliated with Roblox Corporation, Blender Foundation or Unity Technologies.
