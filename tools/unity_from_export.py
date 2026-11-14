"""Studio export folder -> Unity FBX without opening Blender's UI (the same add-on code as the
"Import Saved Export" and "Export FBX for Unity" buttons).

    blender -b --factory-startup --python tools/unity_from_export.py -- ~/RobloxExports/Lobby_<time> [~/RobloxExports/Unity/Lobby/Lobby.fbx]

With no export folder given, the newest one in ~/RobloxExports is used; the FBX defaults to
~/RobloxExports/Unity/<name>/<name>.fbx. Cached/missing assets are filled in first (downloads).
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "roblox-to-blender", "blender-addon"))
sys.path.insert(0, os.path.join(HERE, "..", "blender-to-unity", "blender-addon"))

import addon_utils  # noqa: E402
import bpy  # noqa: E402


def main(argv):
    root = os.path.expanduser("~/RobloxExports")
    if argv:
        d = os.path.abspath(os.path.expanduser(argv[0])).rstrip("/")
    else:
        dirs = [p for p in glob.glob(os.path.join(root, "*_????????_??????")) if os.path.exists(os.path.join(p, "scene.json"))]
        d = max(dirs, key=os.path.getmtime)
    name = re.sub(r"_\d{8}_\d{6}$", "", os.path.basename(d))
    fbx = os.path.abspath(os.path.expanduser(argv[1])) if len(argv) > 1 else os.path.join(root, "Unity", name, name + ".fbx")
    print("UNITYEXPORT from", d, "->", fbx)

    addon_utils.enable("roblox_bridge", default_set=True)
    import roblox_bridge as rb
    addon_utils.enable("roblox_bridge_unity", default_set=True)
    import roblox_bridge_unity as rbu
    p = rb.prefs()
    p.auto_start = False
    p.export_root = root
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    r = bpy.ops.rbx.import_folder(directory=d + os.sep)
    print("UNITYEXPORT import:", r, rb._last.get("summary"))
    if r != {"FINISHED"}:
        sys.exit(1)
    for o in bpy.context.view_layer.objects:
        o.select_set(bool(o.get("rbx_root")))
    os.makedirs(os.path.dirname(fbx), exist_ok=True)
    r = bpy.ops.rbx.export_unity(filepath=fbx, use_selection=True)
    print("UNITYEXPORT export:", r, rbu._last.get("summary"))
    for n in rbu._last.get("notes") or []:
        print("UNITYEXPORT note:", n)
    sys.exit(0 if r == {"FINISHED"} else 1)


main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
