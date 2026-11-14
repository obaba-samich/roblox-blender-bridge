"""Headless end-to-end test.

    blender -b --factory-startup --python tests/test_pipeline.py

Simulates the Studio plugin over real HTTP (chunked base64 uploads), builds
the scene, checks geometry/UVs/materials, exports FBX for Unity and re-imports
it to verify what Unity would receive.
"""
import base64
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "roblox-to-blender", "blender-addon"))
sys.path.insert(0, os.path.join(HERE, "..", "blender-to-unity", "blender-addon"))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402
from roblox_bridge import builder, meshio, pngio, server, uvtools  # noqa: E402
from roblox_bridge_unity import unity_export  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# ------------------------------------------------------------ fixtures
def mirrored_quad_mesh():
    """Two quads side by side whose UVs are IDENTICAL (mirrored texture) -> overlap.
    Bounding box 4 x 2 x 0.2 so MeshPart scaling is testable."""
    P = [(-2, -1, 0), (0, -1, 0), (0, 1, 0), (-2, 1, 0), (2, -1, 0), (2, 1, 0),
         (-2, -1, -0.2), (2, -1, -0.2)]
    T = [(0, 1, 2), (0, 2, 3), (4, 5, 2), (4, 2, 1), (6, 7, 4), (6, 4, 0)]
    # Roblox UVs: v=0 top
    quv = {0: (0, 1), 1: (1, 1), 2: (1, 0), 3: (0, 0)}
    uvs, nrm, cor = [], [], []
    for t in T[:2]:
        for v in t:
            cor.append(v); uvs.extend(quv[v]); nrm.extend((0, 0, 1))
    mirror = {4: (0, 1), 1: (1, 1), 2: (1, 0), 5: (0, 0)}
    for t in T[2:4]:
        for v in t:
            cor.append(v); uvs.extend(mirror[v]); nrm.extend((0, 0, 1))
    for t in T[4:]:
        for v in t:
            cor.append(v); uvs.extend((0.5, 0.5)); nrm.extend((0, -1, 0))
    pos = [c for p in P for c in p]
    return {"positions": pos, "corners": cor, "uvs": uvs, "normals": nrm, "has_uv": True, "has_normal": True}


def texture_rgba(w=64, h=64):
    """Left half red opaque, right half transparent (part color must show through),
    top row marker green so V flipping is verifiable."""
    px = bytearray()
    for y in range(h):
        for x in range(w):
            if y < 4:
                px += bytes((0, 255, 0, 255))
            elif x < w // 2:
                px += bytes((255, 0, 0, 255))
            else:
                px += bytes((0, 0, 0, 0))
    return bytes(px)


def cf(x, y, z, rot_y_deg=0.0):
    a = math.radians(rot_y_deg)
    c, s = math.cos(a), math.sin(a)
    return [x, y, z, c, 0, s, 0, 1, 0, -s, 0, c]


def scene_json():
    parts = [
        {"name": "Block", "fullName": "Workspace.TestModel.Block", "class": "Part", "shape": "Block", "cf": cf(10, 5, -3), "size": [4, 1, 2], "color": [0.2, 0.4, 0.8], "transparency": 0, "material": "Plastic", "decals": [
            {"kind": "Decal", "face": "Front", "texture": "a2001", "color": [1, 1, 1], "transparency": 0, "zindex": 1},
            {"kind": "Texture", "face": "Top", "texture": "a2001", "color": [1, 1, 1], "transparency": 0.5, "zindex": 1, "studsU": 1, "studsV": 1, "offsetU": 0, "offsetV": 0}]},
        {"name": "Ball", "class": "Part", "shape": "Ball", "cf": cf(0, 3, 0), "size": [3, 3, 3], "color": [1, 1, 0], "transparency": 0, "material": "SmoothPlastic", "decals": []},
        {"name": "Cyl", "class": "Part", "shape": "Cylinder", "cf": cf(5, 3, 0, 30), "size": [4, 2, 2], "color": [0, 1, 0], "transparency": 0.3, "material": "Glass", "decals": []},
        {"name": "Wedge", "class": "WedgePart", "shape": "Wedge", "cf": cf(-5, 1, 0), "size": [2, 2, 4], "color": [1, 0.5, 0], "transparency": 0, "material": "Wood", "decals": []},
        {"name": "Corner", "class": "CornerWedgePart", "shape": "CornerWedge", "cf": cf(-9, 1, 0), "size": [2, 2, 2], "color": [1, 0.5, 0], "transparency": 0, "material": "Metal", "decals": []},
        {"name": "Neon", "class": "Part", "shape": "Block", "cf": cf(0, 8, 0), "size": [1, 1, 1], "color": [1, 0, 1], "transparency": 0, "material": "Neon", "decals": [], "parent": 1},
        {"name": "Statue", "class": "MeshPart", "shape": "Mesh", "meshKind": "MeshPart", "mesh": "a1001", "texture": "a2001", "cf": cf(0, 2, 10, 90), "size": [8, 4, 0.4], "color": [0, 0, 1], "transparency": 0, "material": "Plastic", "decals": [], "parent": 2},
        {"name": "Hat/Top:1", "class": "Part", "shape": "Mesh", "meshKind": "SpecialMesh", "mesh": "a1001", "texture": "a2001", "special": {"type": "FileMesh", "scale": [2, 2, 2], "offset": [0, 1, 0], "vertexColor": [1, 1, 1]}, "cf": cf(0, 12, 0), "size": [1, 1, 1], "color": [1, 1, 1], "transparency": 0, "material": "Plastic", "decals": []},
        {"name": "PBR", "class": "MeshPart", "shape": "Mesh", "meshKind": "MeshPart", "mesh": "a1001", "cf": cf(0, 2, 20), "size": [4, 2, 0.2], "color": [1, 1, 1], "transparency": 0, "material": "Plastic", "decals": [],
         "surface": {"color": "a2001", "normal": "a3001", "roughness": "a3002", "metalness": "a3002", "alphaMode": "Overlay", "tint": [1, 1, 1],
                     "emissive": "a3002", "emissiveTint": [1, 0.5, 0], "emissiveStrength": 3}},
        {"name": "Leaf", "class": "MeshPart", "shape": "Mesh", "meshKind": "MeshPart", "mesh": "a1001", "doubleSided": True, "cf": cf(0, 2, 30), "size": [4, 2, 0.2], "color": [0, 1, 0], "transparency": 0, "material": "Plastic", "decals": []},
        {"name": "Sign", "class": "Part", "shape": "Block", "cf": cf(0, 2, 40), "size": [4, 4, 1], "color": [1, 1, 1], "transparency": 0, "material": "Plastic", "decals": [
            {"kind": "Decal", "face": "Front", "texture": "a2001", "normal": "a3001", "emissive": "a3002", "emissiveTint": [0, 1, 1], "emissiveStrength": 2,
             "color": [1, 1, 1], "transparency": 0, "zindex": 1, "rotation": 90, "uvOffset": [0, 0], "uvScale": [1, 1]}]},
        {"name": "VariantWall", "class": "Part", "shape": "Block", "cf": cf(0, 5, 50), "size": [20, 10, 1], "color": [0.8, 0.8, 0.8], "transparency": 0, "material": "Brick", "decals": [],
         "variant": {"name": "FancyBrick", "color": "a2001", "normal": "a3001", "studsPerTile": 2, "pattern": "Regular"}},
        {"name": "Union", "class": "UnionOperation", "shape": "Union", "cf": cf(20, 0, 0), "size": [2, 2, 2], "color": [0.5, 0.5, 0.5], "transparency": 0, "material": "Plastic", "decals": []},
        {"name": "UnionMesh", "class": "UnionOperation", "shape": "Mesh", "meshKind": "Union", "mesh": "Ua1", "cf": cf(30, 0, 0), "size": [3, 1, 1], "color": [0.5, 0.5, 0.5], "transparency": 0, "material": "Plastic", "decals": []},
        {"name": "Locked", "class": "MeshPart", "shape": "Mesh", "meshKind": "MeshPart", "mesh": "a999", "cf": cf(25, 0, 0), "size": [2, 2, 2], "color": [0.5, 0.5, 0.5], "transparency": 0, "material": "Plastic", "decals": []},
    ]
    return {"protocol": 1, "name": "TestModel", "ancestors": [{"id": 1, "name": "Folder", "class": "Folder"}, {"id": 2, "name": "Sub Model", "class": "Model", "parent": 1}],
            "parts": parts,
            "meshes": {"a1001": {"uri": "rbxassetid://1001", "ok": True}, "Ua1": {"uri": "union://Ua1/csg", "ok": True}, "a999": {"uri": "rbxassetid://abc", "ok": False, "err": "no permission"}},
            "lights": [
                {"kind": "PointLight", "name": "Lamp", "owner": "Workspace.TestModel.Block", "enabled": True, "brightness": 2, "color": [1, 0.8, 0.5], "range": 16, "shadows": True,
                 "position": [10, 6, -3], "direction": [0, -1, 0]},
                {"kind": "SpotLight", "name": "Spot", "enabled": True, "brightness": 1, "color": [1, 1, 1], "range": 20, "shadows": False,
                 "angle": 60, "position": [0, 10, 0], "direction": [1, 0, 0]},
                {"kind": "SurfaceLight", "name": "Panel", "enabled": False, "brightness": 3, "color": [0, 1, 1], "range": 8, "shadows": False,
                 "angle": 120, "position": [0, 5, 40], "direction": [0, 0, -1]}],
            "lighting": {"sunDirection": [0.5, 0.7, -0.5], "brightness": 3, "outdoorAmbient": [0.5, 0.5, 0.5], "ambient": [0.2, 0.2, 0.2],
                         "fogColor": [0.7, 0.7, 0.8], "fogStart": 0, "fogEnd": 500, "globalShadows": True,
                         "sky": {"ft": "a2001", "bk": "a2001", "lf": "a2001", "rt": "a2001", "up": "a2001", "dn": "a2001"}},
            "textures": {"a2001": {"uri": "rbxassetid://2001", "ok": True}, "a3001": {"uri": "rbxassetid://3001", "ok": True}, "a3002": {"uri": "rbxassetid://3002", "ok": True}}, "warnings": []}


# ---------------------------------------------------------------- HTTP
def post(port, path, body):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def send_blob(port, sid, kind, key, data, meta=None, chunk=600000):
    total = max(1, -(-len(data) // chunk))
    for i in range(total):
        body = {"session": sid, "kind": kind, "key": key, "index": i, "total": total,
                "data": base64.b64encode(data[i * chunk:(i + 1) * chunk]).decode()}
        if i == total - 1 and meta:
            body["meta"] = meta
        assert len(json.dumps(body)) < 1024 * 1024, "request over Roblox limit"
        post(port, "/blob", body)
    return total


def main():
    tmp = tempfile.mkdtemp(prefix="rbx_test_")
    print("work dir", tmp)

    # ---- mesh parser (v2 / v4 synthetic files)
    print("[mesh parsers]")
    verts = [(0, 0, 0, 0, 0, 1, 0, 0), (1, 0, 0, 0, 0, 1, 1, 0), (0, 1, 0, 0, 0, 1, 0, 1)]
    v2 = b"version 2.00\n" + struct.pack("<HBBII", 12, 36, 12, 3, 1) + b"".join(struct.pack("<9f", *v, 0) for v in verts) + struct.pack("<3I", 0, 1, 2)
    m2 = meshio.parse_roblox_mesh(v2)
    check(list(m2["corners"]) == [0, 1, 2] and abs(m2["uvs"][3] - 0) < 1e-6 and abs(m2["uvs"][5] - 1) < 1e-6, "v2.00 parsed")
    v4 = b"version 4.00\n" + struct.pack("<HHIIHHIHBB", 24, 0, 3, 1, 2, 0, 0, 0, 0, 0) + b"".join(struct.pack("<8f4b4B", *v, 0, 0, 0, 0, 255, 255, 255, 255) for v in verts) + struct.pack("<3I", 0, 1, 2) + struct.pack("<2I", 0, 1)
    m4 = meshio.parse_roblox_mesh(v4)
    check(len(m4["corners"]) == 3, "v4.00 parsed with LODs")
    v1 = b"version 1.00\n1\n[0,0,0][0,0,1][0,0.25,0][1,0,0][0,0,1][1,0.25,0][0,1,0][0,0,1][0,1,0]"
    m1 = meshio.parse_roblox_mesh(v1)
    check(abs(m1["uvs"][1] - 0.75) < 1e-6 and abs(m1["positions"][3] - 0.5) < 1e-6, "v1.00 V flipped to binary convention + 0.5 scale")
    sys.path.insert(0, HERE)
    import draco_fixture
    v7, ids = draco_fixture.make_v7([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0], [0, 0, 1] * 4, [0, 0, 1, 0, 0, 1, 1, 1], [0, 1, 2, 2, 1, 3])
    m7 = meshio.parse_roblox_mesh(v7)
    check(ids == [0, 1, 2] and len(m7["corners"]) == 6 and max(m7["uvs"]) > 0.99, "v7.00 Draco mesh decoded with Blender's Draco")
    from roblox_bridge import fetch
    check(fetch.check_credentials("", "")[0] is False, "credential check reports missing key")
    rt = meshio.read_rbm(meshio.write_rbm(m4))
    check(list(rt["corners"]) == [0, 1, 2], "rbm round trip")

    # ---- server over HTTP
    print("[server]")
    br = server.Bridge()
    br.export_root = os.path.join(tmp, "exports")
    br.blender_version = bpy.app.version_string
    port = 34991
    br.start(port)
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ping" % port) as r:
            pj = json.loads(r.read())
            check(pj["protocol"] == 1 and pj["can_download"] is False, "ping (reports can_download)")
        s = post(port, "/begin", {"name": "Test Model!", "protocol": 1})
        sid = s["session"]
        n = send_blob(port, sid, "mesh", "a1001", meshio.write_rbm(mirrored_quad_mesh()))
        check(n == 1, "mesh uploaded")
        # a union read in Studio: part-space studs, off-centre on purpose (must not be re-centred or stretched)
        send_blob(port, sid, "mesh", "Ua1", meshio.write_rbm({"positions": [0, 0, 0, 3, 0, 0, 3, 1, 0, 0, 1, 0], "corners": [0, 1, 2, 0, 2, 3],
                                                              "uvs": [0.0] * 12, "normals": [0, 0, 1] * 6, "has_uv": False, "has_normal": True}))
        big = texture_rgba(512, 512)  # 1 MB raw -> forces 2 chunks
        n = send_blob(port, sid, "texture", "a2001", big, {"w": 512, "h": 512, "format": "rgba8"})
        check(n == 2, "texture uploaded in %d chunks" % n)
        flat = lambda rgba, w=32: bytes(rgba) * (w * w)
        send_blob(port, sid, "texture", "a3001", flat((128, 128, 255, 255)), {"w": 32, "h": 32})
        send_blob(port, sid, "texture", "a3002", flat((200, 200, 200, 255)), {"w": 32, "h": 32})
        n = send_blob(port, sid, "scene", "scene", json.dumps(scene_json()).encode())
        post(port, "/commit", {"session": sid})
        for _ in range(100):
            if not br.jobs.empty():
                break
            time.sleep(0.05)
        sid2, d = br.jobs.get(timeout=5)
        check(os.path.exists(os.path.join(d, "textures", "a2001.png")), "texture saved as PNG")
        try:
            post(port, "/blob", {"session": sid, "kind": "texture", "key": "../evil", "index": 0, "total": 1, "data": "AAAA", "meta": {"w": 1, "h": 1}})
        except Exception:
            pass
        check(not os.path.exists(os.path.join(d, "evil.png")) and not os.path.exists(os.path.join(d, "..", "evil.png")), "path traversal blocked")
    finally:
        br.stop()

    # ---- build
    print("[build]")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rep = builder.build(d, {"stud_scale": 0.28, "bake_samples": 4})
    print("  summary:", rep["summary"])
    for n_ in rep["notes"]:
        print("   note:", n_)
    objs = {o.name: o for o in bpy.data.objects}
    check("Block" in objs and "Statue" in objs and "UNION_Union" in objs and "MISSING_Locked" in objs, "objects created incl. placeholders")
    check(objs["Neon"].parent.name == "Folder" and objs["Statue"].parent.name == "Sub Model", "hierarchy preserved")
    um = objs.get("UnionMesh")
    if um is not None:
        ws = [um.matrix_world @ v.co for v in um.data.vertices]
        xs, zs = [w.x for w in ws], [w.z for w in ws]
        check(abs(min(xs) - 8.4) < 1e-3 and abs(max(xs) - 9.24) < 1e-3 and abs(min(zs)) < 1e-3 and abs(max(zs) - 0.28) < 1e-3,
              "union mesh placed as-is in part space: x %.3f..%.3f z %.3f..%.3f" % (min(xs), max(xs), min(zs), max(zs)))
    else:
        check(False, "union mesh built as an object named after the union")
    blk = objs["Block"]
    t = blk.matrix_world.translation
    check((abs(t.x - 2.8) < 1e-4 and abs(t.y - 0.84) < 1e-4 and abs(t.z - 1.4) < 1e-4), "Roblox (10,5,-3) -> Blender (2.8,0.84,1.4): %s" % (tuple(round(v, 4) for v in t),))
    dims = blk.dimensions
    check(abs(dims.x - 1.12) < 1e-4 and abs(dims.y - 0.56) < 1e-4 and abs(dims.z - 0.28) < 1e-4, "Block size 4x1x2 studs -> %s m" % (tuple(round(v, 3) for v in dims),))
    st = objs["Statue"]
    sd = sorted(round(v, 3) for v in st.dimensions)
    check(sd == sorted([round(8 * 0.28, 3), round(4 * 0.28, 3), round(0.4 * 0.28, 3)]), "MeshPart bbox stretched to Size: %s" % sd)
    check(all(abs(s - 1) < 1e-6 for s in st.scale), "object scale is 1 (baked into mesh)")
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        me = o.data
        names = [l.name for l in me.uv_layers]
        check(names == ["UVMap", "UVLightmap"], "%s UV layers %s" % (o.name, names))
        if me.get("rbx_tiled"):
            check(uvtools.uv_stats(me, "UVMap")["outside"], "%s UVMap tiles (repeating texture kept)" % o.name)
        else:
            check(uvtools.is_clean(me, "UVMap"), "%s UVMap non-overlapping" % o.name)
        check(uvtools.is_clean(me, "UVLightmap"), "%s UVLightmap non-overlapping" % o.name)
    # composited texture: transparent half must show the part color (blue)
    stm = st.data.materials[0]
    check("rebaked_from" in json.loads(stm["rbx_desc"]), "Statue mirrored UVs were rebaked")
    img = next(n.image for n in stm.node_tree.nodes if n.type == "TEX_IMAGE")
    px = list(img.pixels[:])
    w, h = img.size
    blue = sum(1 for i in range(0, len(px), 4) if px[i + 2] > 0.9 and px[i] < 0.1)
    red = sum(1 for i in range(0, len(px), 4) if px[i] > 0.9 and px[i + 2] < 0.1)
    check(red > 0 and blue == 0, "TextureID: part Color does NOT show through texture alpha (%d red, %d blue)" % (red, blue))
    check(json.loads(stm["rbx_desc"])["alpha_mode"] == "OPAQUE", "TextureID at Transparency 0 is opaque")
    pbr = objs["PBR"].data.materials[0]
    pimg = next(n.image for n in pbr.node_tree.nodes if n.type == "TEX_IMAGE" and n.outputs["Color"].links and n.outputs["Color"].links[0].to_socket.name == "Base Color")
    ppx = list(pimg.pixels[:])
    white = sum(1 for i in range(0, len(ppx), 4) if min(ppx[i], ppx[i + 1], ppx[i + 2]) > 0.95)
    check(white > 0, "SurfaceAppearance Overlay: part Color (white) shows through alpha (%d texels)" % white)
    pd = json.loads(pbr["rbx_desc"])
    check(pd.get("emission_image") and abs(pd.get("emission_strength", 0) - 3) < 1e-6, "SA emissive mask * tint * strength exported")
    check(len(objs["Leaf"].data.polygons) == 2 * len(objs["Statue"].data.polygons), "DoubleSided doubles faces (%d vs %d)" % (len(objs["Leaf"].data.polygons), len(objs["Statue"].data.polygons)))
    wedge_d = json.loads(objs["Wedge"].data.materials[0]["rbx_desc"])
    check(wedge_d.get("base_image") and wedge_d.get("normal_image") and wedge_d.get("tile_studs") == 8,
          "built-in Wood: Roblox's real color + normal maps, tiled every 8 studs")
    check(not json.loads(objs["Neon"].data.materials[0]["rbx_desc"]).get("base_image"), "Neon stays untextured")
    lamp, spot = objs.get("RBXLight_0"), objs.get("RBXLight_1")
    check(lamp and lamp.type == "LIGHT" and lamp.data.type == "POINT" and (lamp.matrix_world.translation - Vector((2.8, 0.84, 1.68))).length < 1e-4,
          "PointLight created at Roblox (10,6,-3) -> Blender (2.8,0.84,1.68)")
    sf = spot.matrix_world.to_3x3() @ Vector((0, 0, -1))
    check(spot.data.type == "SPOT" and (sf - Vector((1, 0, 0))).length < 1e-4 and abs(math.degrees(spot.data.spot_size) - 60) < 1e-3,
          "SpotLight points along Roblox +X with a 60deg cone (%s)" % (tuple(round(x, 3) for x in sf),))
    check(objs["RBXLight_2"].hide_render, "disabled SurfaceLight kept but switched off")
    vw = objs["VariantWall"]
    vd = json.loads(vw.data.materials[0]["rbx_desc"])
    vimg = next(n.image for n in vw.data.materials[0].node_tree.nodes if n.type == "TEX_IMAGE")
    check(vw.data.get("rbx_tiled") and vd.get("normal_image") and vd.get("tile_studs") == 2 and not vd.get("rebaked_from"),
          "MaterialVariant: tiled colour + normal map kept tiling (no giant rebake)")
    ex = vw.data.uv_layers["UVMap"].data
    us = [ex[i].uv[0] for i in range(len(ex))]
    check(abs((max(us) - min(us)) - 10.0) < 0.01, "MaterialVariant tiles every StudsPerTile (20 studs / 2 = %.2f repeats)" % (max(us) - min(us)))
    dsign = next(o for o in bpy.data.objects if o.name.startswith("Decal_Front_Sign"))
    dd = json.loads(dsign.data.materials[0]["rbx_desc"])
    check(dd.get("normal_image") and dd.get("emission_image"), "PBR decal keeps normal + emissive maps")
    hat = objs["HatTop1"]
    check(abs(max(hat.dimensions) - 4 * 2 * 0.28) < 1e-3, "SpecialMesh uses Scale, not part size")

    # thumbnail letterbox trimming (700x700 thumbnail of a 2:1 image -> bands top & bottom)
    from roblox_bridge import materials as M
    W, H, band = 40, 40, 10
    rows = []
    for y in range(H):
        rows.append(bytes((0, 0, 0, 0)) * W if (y < band or y >= H - band) else bytes((255, 0, 0, 255)) * W)
    lb = os.path.join(tmp, "lb.png")
    open(lb, "wb").write(pngio.encode_rgba(W, H, b"".join(rows)))
    cp = M.crop_letterbox(lb, os.path.join(tmp, "lb_crop.png"))
    ci = bpy.data.images.load(cp)
    check(tuple(ci.size) == (40, 20), "thumbnail letterbox trimmed to the real image (%s)" % (tuple(ci.size),))
    one = os.path.join(tmp, "edge.png")  # transparency on ONE edge only must be kept
    open(one, "wb").write(pngio.encode_rgba(W, H, b"".join(rows[:H - band]) + bytes((255, 0, 0, 255)) * W * band))
    check(M.crop_letterbox(one, os.path.join(tmp, "edge_crop.png")) == one, "one-sided transparency is not cropped")

    # ---- unity export
    print("[unity export]")
    for o in bpy.context.view_layer.objects:
        o.select_set(o.name == "TestModel")
    out = os.path.join(tmp, "unity", "TestModel.fbx")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    def world_verts():
        out_ = {}
        for o in bpy.data.objects:
            if o.type == "MESH":
                out_[o.name] = sorted(tuple(round(c, 3) for c in (o.matrix_world @ v.co)) for v in o.data.vertices)
        return out_
    before = world_verts()
    names_before = sorted(o.name for o in bpy.data.objects)
    ok, msgs = unity_export.export(out, use_selection=True)
    check(sorted(o.name for o in bpy.data.objects) == names_before and world_verts() == before,
          "export leaves the Blender scene untouched")
    for m in msgs:
        print("   ", m)
    check(ok and os.path.getsize(out) > 1000, "FBX written")
    side = json.load(open(os.path.join(tmp, "unity", "TestModel.roblox.json")))
    modes = {m["name"].split("_")[0]: m["mode"] for m in side["materials"]}
    check(any(m["mode"] == "Transparent" for m in side["materials"]), "transparent material flagged in sidecar")
    check(any(m["emission"] for m in side["materials"]), "neon emission in sidecar")
    check(any(m["metallicSmoothness"] for m in side["materials"]), "packed MetallicSmoothness map written")
    tex_ok = [m for m in side["materials"] if m["baseMap"]]
    check(tex_ok and all(os.path.exists(os.path.join(tmp, "unity", m["baseMap"])) for m in tex_ok)
          and all(m["color"] == [1.0, 1.0, 1.0] for m in tex_ok),
          "%d textured materials: base maps written next to the FBX, tint white" % len(tex_ok))
    check(any(m["normalMap"] for m in side["materials"]), "normal maps written for Unity")
    L = side.get("lights") or []
    check(len(L) == 3 and abs(L[0]["range"] - 16 * 0.28) < 1e-6 and L[1]["angle"] == 60 and L[2]["enabled"] is False,
          "sidecar lists 3 lights with metre ranges")
    check(side.get("lighting", {}).get("fogEnd") == 500 and side["lighting"].get("studScale") == 0.28, "sidecar carries Roblox Lighting (sun, ambient, fog)")
    check(L[0].get("owner") == "", "a 4-stud Block holding a light keeps casting shadows (only bulbs/Neon/small parts don't): %r" % L[0].get("owner"))
    sm = side["lighting"].get("skyMaps") or {}
    check(len(sm) == 6 and len(set(sm.values())) == 1 and os.path.exists(os.path.join(tmp, "unity", sm["ft"])), "6 skybox faces exported (same image shared once)")
    files = os.listdir(os.path.join(tmp, "unity", "TestModel_Textures"))
    refs = [m[k] for m in side["materials"] for k in ("baseMap", "normalMap") if m[k]]
    check(all(os.path.exists(os.path.join(tmp, "unity", r)) for r in refs),
          "all %d texture references exist (%d files)" % (len(refs), len(set(refs))))
    wood = [m for m in side["materials"] if m["name"].startswith("Wood")]
    check(wood and wood[0].get("tint") and wood[0]["baseMap"].endswith("_BaseColor.png"), "built-in material: shared texture + part colour as tint")
    check(any(m.get("emissionMap") and os.path.exists(os.path.join(tmp, "unity", m["emissionMap"])) for m in side["materials"]), "emission map copied for Unity")

    # ---- re-import FBX
    print("[fbx re-import]")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=out, axis_forward="Z", axis_up="Y")
    objs = {o.name: o for o in bpy.data.objects}
    check("Statue" in objs, "objects present after re-import")
    me = objs["Statue"].data
    check([l.name for l in me.uv_layers] == ["UVMap", "UVLightmap"], "FBX keeps both UV sets in order")
    imgs = [i for i in bpy.data.images if i.size[0] > 0]
    check(len(imgs) >= 2, "embedded textures load (%d)" % len(imgs))
    t = objs["Block"].matrix_world.translation
    check(abs(t.x - 2.8) < 1e-3 and abs(t.y - 0.84) < 1e-3 and abs(t.z - 1.4) < 1e-3, "position survives FBX round trip: %s" % (tuple(round(v, 3) for v in t),))
    rot = objs["Block"].matrix_world.to_euler()
    check(all(abs(a) < 1e-4 for a in rot), "no stray axis rotation %s" % (tuple(round(math.degrees(a), 2) for a in rot),))
    mk = {o.name: o for o in bpy.data.objects if o.name.startswith("RBXL_") or o.name.startswith("RBXSun")}
    a = mk.get("RBXL_1_aim").matrix_world.translation - mk.get("RBXL_1").matrix_world.translation if "RBXL_1" in mk and "RBXL_1_aim" in mk else None
    check(a is not None and (a - Vector((1, 0, 0))).length < 1e-3, "light marker + aim marker in the FBX give the spot direction (%s)" % (a,))
    s_ = mk.get("RBXSun_aim").matrix_world.translation - mk.get("RBXSun").matrix_world.translation if "RBXSun" in mk and "RBXSun_aim" in mk else None
    check(s_ is not None and (s_ - Vector((0.5, 0.5, 0.7)).normalized()).length < 1e-3, "sun direction markers in the FBX")
    after = world_verts()
    bad = [n for n in before if n in after and after[n] != before[n]]
    missing = [n for n in before if n not in after]
    check(not bad and not missing, "every vertex of every part (incl. rotated Cyl 30deg, Statue 90deg) lands exactly where it was: bad=%s missing=%s" % (bad[:5], missing[:5]))
    rotated = [o.name for o in bpy.data.objects if o.type == "MESH" and any(abs(a) > 1e-4 for a in o.matrix_world.to_euler())]
    check(not rotated, "no object carries a rotation in the FBX (%s)" % rotated[:5])

    print("\nRESULT:", "PASS" if not FAIL else "FAIL (%d)" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    if os.environ.get("RBX_KEEP"):
        print("KEPT", os.path.join(tmp, "unity"))
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
