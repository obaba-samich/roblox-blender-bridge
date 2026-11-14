"""UV checks, non-overlapping UV generation and texture rebaking.

Result for every mesh:
    UV0 "UVMap"      - texture UVs, guaranteed non-overlapping and inside 0..1
                       (if Roblox's UVs overlap/tile, textures are rebaked)
    UV1 "UVLightmap" - non-overlapping lightmap UVs (Unity "UV2")
"""

import math
import os
from array import array

import bpy

UV0 = "UVMap"
UV1 = "UVLightmap"
OVERLAP_TOLERANCE = 0.002  # fraction of covered texels allowed to be double-covered (rasterisation noise)


# ---------------------------------------------------------------- checking
def uv_stats(me, layer_name, res=256):
    """Returns dict(overlap=ratio, outside=bool). Rasterises every UV triangle at
    texel centres (strictly-inside test, so shared edges never double count)."""
    uvl = me.uv_layers.get(layer_name)
    if uvl is None:
        return {"overlap": 1.0, "outside": True, "missing": True}
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    loops = array("i", [0]) * (nt * 3)
    me.loop_triangles.foreach_get("loops", loops)
    uv = array("f", [0.0]) * (len(me.loops) * 2)
    uvl.data.foreach_get("uv", uv)
    if not len(uv):
        return {"overlap": 0.0, "outside": False}
    us, vs = uv[0::2], uv[1::2]
    outside = min(us) < -1e-4 or min(vs) < -1e-4 or max(us) > 1 + 1e-4 or max(vs) > 1 + 1e-4
    if outside:
        return {"overlap": 1.0, "outside": True}  # tiling UVs reuse texels by definition
    grid = bytearray(res * res)
    covered = over = 0
    for t in range(nt):
        l0, l1, l2 = loops[3 * t], loops[3 * t + 1], loops[3 * t + 2]
        x0, y0 = us[l0] * res - 0.5, vs[l0] * res - 0.5
        x1, y1 = us[l1] * res - 0.5, vs[l1] * res - 0.5
        x2, y2 = us[l2] * res - 0.5, vs[l2] * res - 0.5
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-12:
            continue
        if area < 0:
            x1, y1, x2, y2 = x2, y2, x1, y1
            area = -area
        minx = max(0, math.ceil(min(x0, x1, x2))); maxx = min(res - 1, math.floor(max(x0, x1, x2)))
        miny = max(0, math.ceil(min(y0, y1, y2))); maxy = min(res - 1, math.floor(max(y0, y1, y2)))
        if minx > maxx or miny > maxy:
            continue
        eps = 1e-7 * area
        for py in range(miny, maxy + 1):
            row = py * res
            for px in range(minx, maxx + 1):
                w0 = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
                if w0 <= eps:
                    continue
                w1 = (x0 - x2) * (py - y2) - (y0 - y2) * (px - x2)
                if w1 <= eps:
                    continue
                w2 = (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0)
                if w2 <= eps:
                    continue
                c = grid[row + px]
                if c == 0:
                    covered += 1
                    grid[row + px] = 1
                elif c == 1:
                    over += 1
                    grid[row + px] = 2
    if covered == 0 and nt > 0:
        return {"overlap": 1.0, "outside": False, "degenerate": True}  # UVs collapsed to a point/line
    ratio = (over / covered) if covered else 0.0
    return {"overlap": ratio, "outside": False}


def is_clean(me, layer_name):
    s = uv_stats(me, layer_name)
    return s["overlap"] <= OVERLAP_TOLERANCE and not s["outside"]


# ------------------------------------------------------------- generating
def _ctx_override():
    wm = bpy.context.window_manager
    for win in wm.windows if wm else []:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                return {"window": win, "area": area, "region": region}
    return {}


def _edit(obj, fn):
    vl = bpy.context.view_layer
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in list(vl.objects.selected):
        o.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    vl.objects.active = obj
    with bpy.context.temp_override(**_ctx_override(), active_object=obj, object=obj):
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            bpy.ops.mesh.reveal()
            bpy.ops.mesh.select_all(action="SELECT")
            fn()
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
    obj.select_set(False)


def ensure_layer(me, name):
    uvl = me.uv_layers.get(name)
    if uvl is None:
        uvl = me.uv_layers.new(name=name, do_init=False)
    return uvl


def unwrap(obj, layer_name, margin=0.01, method="SMART"):
    me = obj.data
    uvl = ensure_layer(me, layer_name)
    prev_active = me.uv_layers.active_index
    me.uv_layers.active = uvl

    def run():
        if method == "SMART":
            try:
                bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), margin_method="FRACTION",
                                         island_margin=margin, area_weight=0.0, correct_aspect=True,
                                         scale_to_bounds=False)
            except TypeError:
                bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=margin)
        else:
            bpy.ops.uv.lightmap_pack(PREF_CONTEXT="ALL_FACES", PREF_PACK_IN_ONE=False,
                                     PREF_NEW_UVLAYER=False, PREF_BOX_DIV=12, PREF_MARGIN_DIV=max(0.05, margin * 20))
    _edit(obj, run)
    me.uv_layers.active_index = prev_active
    return uvl


def copy_layer(me, src, dst):
    s = me.uv_layers[src]
    d = ensure_layer(me, dst)
    buf = array("f", [0.0]) * (len(me.loops) * 2)
    s.data.foreach_get("uv", buf)
    d.data.foreach_set("uv", buf)


def order_layers(me):
    """UVMap first (Unity UV0), UVLightmap second (Unity UV2). Drop extras."""
    for l in list(me.uv_layers):
        if l.name not in (UV0, UV1):
            me.uv_layers.remove(l)
    names = [l.name for l in me.uv_layers]
    if names == [UV1, UV0]:
        buf0 = array("f", [0.0]) * (len(me.loops) * 2)
        buf1 = array("f", [0.0]) * (len(me.loops) * 2)
        me.uv_layers[UV0].data.foreach_get("uv", buf0)
        me.uv_layers[UV1].data.foreach_get("uv", buf1)
        me.uv_layers[0].name = "__tmp"; me.uv_layers[1].name = UV1; me.uv_layers[0].name = UV0
        me.uv_layers[0].data.foreach_set("uv", buf0)
        me.uv_layers[1].data.foreach_set("uv", buf1)
    me.uv_layers.active_index = 0
    for l in me.uv_layers:
        l.active_render = l.name == UV0


def make_lightmap(obj, margin, notes):
    me = obj.data
    unwrap(obj, UV1, margin, "SMART")
    if not is_clean(me, UV1):
        unwrap(obj, UV1, margin, "LIGHTMAP")
        if not is_clean(me, UV1):
            notes.append("%s: lightmap UVs still overlap after two methods" % me.name)
            return False
    return True


# ----------------------------------------------------------------- baking
def _image_res(paths):
    best = 0
    for p in paths:
        if p and os.path.exists(p):
            img = bpy.data.images.load(p, check_existing=True)
            best = max(best, img.size[0], img.size[1])
    return best


def rebake(obj, desc, out_dir, samples=8, margin_px=8, max_res=4096):
    """Bake every texture of `desc` from UVMap onto UVLightmap (non-overlapping),
    then make UVLightmap the new UVMap. Returns the new descriptor."""
    import json
    from . import materials

    me = obj.data
    scene = bpy.context.scene
    src_res = _image_res([desc.get("base_image"), desc.get("normal_image"), desc.get("rough_image"),
                          desc.get("metal_image"), desc.get("emission_image")])
    # keep texel density: tiled UVs cover `area` tiles of the source texture
    me.calc_loop_triangles()
    uvl = me.uv_layers[UV0]
    uvbuf = array("f", [0.0]) * (len(me.loops) * 2)
    uvl.data.foreach_get("uv", uvbuf)
    area = 0.0
    for lt in me.loop_triangles:
        a, b, c = lt.loops
        ax, ay, bx, by, cx, cy = uvbuf[2 * a], uvbuf[2 * a + 1], uvbuf[2 * b], uvbuf[2 * b + 1], uvbuf[2 * c], uvbuf[2 * c + 1]
        area += abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) * 0.5
    want = max(src_res, 1) * max(1.5, math.sqrt(max(area, 0.0)) * 1.4)
    res = int(min(max_res, max(256, 2 ** math.ceil(math.log2(want)))))

    # target UVs: a copy of the lightmap layout
    copy_layer(me, UV1, "__bake")
    target = me.uv_layers["__bake"]
    prev_mats = list(me.materials)
    saved = (scene.render.engine, scene.cycles.samples if hasattr(scene, "cycles") else None)
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    try:
        scene.cycles.use_denoising = False
    except Exception:
        pass
    bake = scene.render.bake
    bake.margin = margin_px
    if hasattr(bake, "margin_type"):
        bake.margin_type = "EXTEND"
    bake.use_clear = True
    bake.target = "IMAGE_TEXTURES"

    new = dict(desc)
    import re
    base = re.sub(r"[^A-Za-z0-9_\-]+", "_", "%s_%s" % (desc["name"], me.name))[:80]
    results = {}

    def bake_channel(label, src_path, socket, non_color_src, non_color_dst, kind="EMIT"):
        mat = bpy.data.materials.new("__rbx_bake")
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        uvn = nt.nodes.new("ShaderNodeUVMap"); uvn.uv_map = UV0
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(src_path, check_existing=True)
        tex.image.alpha_mode = "STRAIGHT"
        if non_color_src:
            tex.image.colorspace_settings.name = "Non-Color"
        tex.interpolation = "Linear"
        tex.extension = "REPEAT"
        nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
        if kind == "NORMAL":
            nm = nt.nodes.new("ShaderNodeNormalMap"); nm.uv_map = UV0
            bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
            nt.links.new(tex.outputs["Color"], nm.inputs["Color"])
            nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
            nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        else:
            em = nt.nodes.new("ShaderNodeEmission")
            nt.links.new(tex.outputs[socket], em.inputs["Color"])
            nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
        img = bpy.data.images.new("__rbx_bake_%s" % label, res, res, alpha=False, float_buffer=False)
        if non_color_dst:
            img.colorspace_settings.name = "Non-Color"
        tgt = nt.nodes.new("ShaderNodeTexImage")
        tgt.image = img
        for n in nt.nodes:
            n.select = False
        tgt.select = True
        nt.nodes.active = tgt

        me.materials.clear()
        me.materials.append(mat)
        me.uv_layers.active = target  # bake writes into the active UV layout
        for l in me.uv_layers:
            l.active_render = l.name == "__bake"
        vl = bpy.context.view_layer
        for o in list(vl.objects.selected):
            o.select_set(False)
        obj.hide_set(False)
        obj.hide_render = False
        obj.select_set(True)
        vl.objects.active = obj
        with bpy.context.temp_override(**_ctx_override(), active_object=obj, object=obj,
                                       selected_objects=[obj], selected_editable_objects=[obj]):
            if kind == "NORMAL":
                bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT", margin=margin_px, use_clear=True)
            else:
                bpy.ops.object.bake(type="EMIT", margin=margin_px, use_clear=True)
        w, h = img.size
        px = array("f", [0.0]) * (w * h * 4)
        img.pixels.foreach_get(px)
        bpy.data.images.remove(img)
        bpy.data.materials.remove(mat)
        return w, h, px

    try:
        if desc.get("base_image"):
            w, h, px = bake_channel("base", desc["base_image"], "Color", False, False)
            if desc.get("alpha_mode", "OPAQUE") != "OPAQUE":
                _, _, pa = bake_channel("alpha", desc["base_image"], "Alpha", False, True)
                px[3::4] = pa[0::4]
            else:
                px[3::4] = array("f", [1.0]) * (w * h)
            new["base_image"] = materials.save_png(os.path.join(out_dir, base + "_BaseColor.png"), w, h, px)
        if desc.get("normal_image"):
            w, h, px = bake_channel("normal", desc["normal_image"], "Color", True, True, "NORMAL")
            px[3::4] = array("f", [1.0]) * (w * h)
            new["normal_image"] = materials.save_png(os.path.join(out_dir, base + "_Normal.png"), w, h, px, True)
        if desc.get("emission_image"):
            w, h, px = bake_channel("emission", desc["emission_image"], "Color", False, False)
            px[3::4] = array("f", [1.0]) * (w * h)
            new["emission_image"] = materials.save_png(os.path.join(out_dir, base + "_Emission.png"), w, h, px)
        for key, label in (("rough_image", "Roughness"), ("metal_image", "Metallic")):
            if desc.get(key):
                w, h, px = bake_channel(label, desc[key], "Color", True, True)
                px[3::4] = array("f", [1.0]) * (w * h)
                new[key] = materials.save_png(os.path.join(out_dir, "%s_%s.png" % (base, label)), w, h, px, True)
    finally:
        me.materials.clear()
        for m in prev_mats:
            me.materials.append(m)
        scene.render.engine = saved[0]
        if saved[1] is not None:
            scene.cycles.samples = saved[1]

    # the baked layout becomes the texture UVs
    copy_layer(me, "__bake", UV0)
    me.uv_layers.remove(me.uv_layers["__bake"])
    order_layers(me)
    new["name"] = base[:60]
    new["rebaked_from"] = desc["name"]
    return new
