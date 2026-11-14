"""Add-on level test: enable like a user would, let the server auto-start,
upload over HTTP, let the add-on's own timer build, then run the panel
operators (validate, export for Unity [roblox_bridge_unity], fix UVs, import saved folder).

    blender -b --factory-startup --python tests/test_addon.py
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "roblox-to-blender", "blender-addon"))
sys.path.insert(0, os.path.join(HERE, "..", "blender-to-unity", "blender-addon"))
sys.path.insert(0, HERE)

import addon_utils  # noqa: E402
import bpy  # noqa: E402

import test_pipeline as tp  # noqa: E402  (fixtures; its main() only runs as __main__? no -> guarded below)

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def main():
    tmp = tempfile.mkdtemp(prefix="rbx_addon_")
    addon_utils.enable("roblox_bridge", default_set=True)
    import roblox_bridge as rb
    addon_utils.enable("roblox_bridge_unity", default_set=True)
    import roblox_bridge_unity as rbu
    p = rb.prefs()
    p.export_root = os.path.join(tmp, "exports")
    p.port = 34998
    rb.start_server()
    check(rb.bridge.running and rb.bridge.port == 34998, "server started from prefs")
    check(bpy.app.timers.is_registered(rb._tick), "build timer registered")

    port = 34998
    with urllib.request.urlopen("http://127.0.0.1:%d/ping" % port) as r:
        check(json.loads(r.read())["export_root"] == p.export_root, "ping reports export folder")
    sid = tp.post(port, "/begin", {"name": "AddonTest", "protocol": 1})["session"]
    tp.send_blob(port, sid, "mesh", "a1001", tp.meshio.write_rbm(tp.mirrored_quad_mesh()))
    tp.send_blob(port, sid, "texture", "a2001", tp.texture_rgba(64, 64), {"w": 64, "h": 64})
    flat = lambda rgba, w=32: bytes(rgba) * (w * w)
    tp.send_blob(port, sid, "texture", "a3001", flat((128, 128, 255, 255)), {"w": 32, "h": 32})
    tp.send_blob(port, sid, "texture", "a3002", flat((200, 200, 200, 255)), {"w": 32, "h": 32})
    scene = tp.scene_json()
    scene["parts"].insert(0, scene["parts"][0])  # same part twice -> mesh/material sharing path
    tp.send_blob(port, sid, "scene", "scene", json.dumps(scene).encode())
    tp.post(port, "/commit", {"session": sid})

    # drive the add-on's own main-thread timer like Blender's event loop would
    st = {}
    for _ in range(400):
        rb._tick()
        with urllib.request.urlopen("http://127.0.0.1:%d/status?session=%s" % (port, sid)) as r:
            st = json.loads(r.read())
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    print("  status:", st.get("message"))
    check(st.get("state") == "done", "Studio-facing status reaches 'done'")
    check(any("no permission" in w for w in st.get("warnings", [])), "warnings are reported back to Studio")
    n = len(scene["parts"])
    check(rb._last["summary"].startswith("%d/%d parts" % (n, n)), "panel summary: %s" % rb._last["summary"])
    check(len([o for o in bpy.data.objects if o.name.startswith("Block")]) == 2, "duplicate part built twice")
    check("UNION_Union" in bpy.data.objects and "MISSING_Locked" in bpy.data.objects, "without an OBJ, unions/locked meshes fall back to named placeholders")

    # panel operators
    for o in bpy.context.view_layer.objects:
        o.select_set(bool(o.get("rbx_root")))
    try:
        r = bpy.ops.rbx.validate(use_selection=True)
    except RuntimeError:
        r = None
    print("   validate notes:", rbu._last["notes"])
    check(rbu._last["summary"].startswith("Validation: 0 error"), rbu._last["summary"])
    fbx = os.path.join(tmp, "unity", "AddonTest.fbx")
    r = bpy.ops.rbx.export_unity(filepath=fbx, use_selection=True)
    check(r == {"FINISHED"} and os.path.exists(fbx), "Export FBX for Unity operator: %s" % rbu._last["summary"])
    check(os.path.exists(os.path.join(tmp, "unity", "AddonTest.roblox.json")), "sidecar written")

    bpy.ops.object.select_all(action="DESELECT")
    bpy.data.objects["Wedge"].select_set(True)
    r = bpy.ops.rbx.fix_uvs()
    check(r == {"FINISHED"}, "Fix UVs operator")

    session_dir = next(os.path.join(p.export_root, d) for d in os.listdir(p.export_root) if not d.startswith("_"))
    model = len(next(c for c in bpy.data.collections if c.name.startswith("RBX_")).objects)  # objects of the first imported model
    before = len(bpy.data.objects)
    try:
        r = bpy.ops.rbx.import_folder(directory=session_dir + os.sep)
    except RuntimeError as e:
        r = str(e)
    check(r == {"FINISHED"} and len(bpy.data.objects) == before + model and len([c for c in bpy.data.collections if c.name.startswith("RBX_")]) == 2, "Import Saved Export rebuilds the dump offline")

    # asset cache: what Studio sent is kept, and a second export gets it without Studio sending it again
    cache = os.path.join(p.export_root, "_cache")
    check(os.path.exists(os.path.join(cache, "meshes", "a1001.rbm")) and os.path.exists(os.path.join(cache, "textures", "a2001.png")),
          "Studio's meshes/textures are kept in the asset cache")
    have = tp.post(port, "/have", {"meshes": ["a1001", "a9999"], "textures": ["a2001"]})
    check(have == {"meshes": ["a1001"], "textures": ["a2001"]}, "/have lists cached assets: %s" % have)
    sid2 = tp.post(port, "/begin", {"name": "CacheTest", "protocol": 1})["session"]
    scene2 = json.loads(json.dumps(scene))
    scene2["meshes"]["a4242"] = {"uri": "rbxassetid://4242"}   # Roblox refuses it (faked: no network in tests)
    from roblox_bridge import fetch as rbfetch
    real_fetch = rbfetch.fetch_asset
    def refuse(asset_id, *a, **k):
        raise RuntimeError("HTTP 403 (test)")
    rbfetch.fetch_asset = refuse
    tp.send_blob(port, sid2, "scene", "scene", json.dumps(scene2).encode())
    tp.post(port, "/commit", {"session": sid2, "fallback": True})
    asked = False
    for _ in range(600):
        rb._tick()
        with urllib.request.urlopen("http://127.0.0.1:%d/status?session=%s" % (port, sid2)) as r:
            st = json.loads(r.read())
        if st["state"] == "need":
            asked = asked or st.get("need") == {"meshes": ["a4242"]}   # Studio would read it itself; here: not
            tp.post(port, "/continue", {"session": sid2})
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    rbfetch.fetch_asset = real_fetch
    d2 = rb.bridge.sessions[sid2]["dir"]
    check(st.get("state") == "done", "second export finishes from the cache")
    check(os.path.exists(os.path.join(d2, "meshes", "a1001.rbm")) and os.stat(os.path.join(d2, "meshes", "a1001.rbm")).st_ino
          == os.stat(os.path.join(cache, "meshes", "a1001.rbm")).st_ino, "cached mesh is hard-linked into the session (no copy)")
    check(asked, "Blender asks Studio for assets it couldn't download")

    rb.bridge.stop()
    addon_utils.disable("roblox_bridge_unity")
    addon_utils.disable("roblox_bridge")
    check(not bpy.app.timers.is_registered(rb._tick), "unregister cleans up timer")

    print("\nRESULT:", "PASS" if not FAIL else "FAIL (%d)" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1 if FAIL else 0)


try:
    main()
except SystemExit:
    raise
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(2)
