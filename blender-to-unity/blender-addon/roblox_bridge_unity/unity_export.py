"""Validate + export FBX for Unity.

Output folder:
    <name>.fbx                     textures embedded (Unity extracts them)
    <name>.roblox.json             sidecar read by RobloxBridgeImporter.cs
    <name>_Textures/*.png          packed MetallicSmoothness maps (Unity Lit layout)

Axes: Roblox (x, y, z) -> Unity (x, y, -z). Roblox's front (-Z, LookVector)
becomes Unity's forward (+Z). Every object imports with rotation (0,0,0) at the
root and scale 1, 1 unit = 1 metre.
"""

import hashlib
import json
import math
import os
import shutil
from array import array

import bpy

from ._base import materials, uvtools


def _collect(use_selection):
    if use_selection:
        objs = set()
        for o in bpy.context.selected_objects:
            objs.add(o)
            objs.update(o.children_recursive)
        if not any(o.type == "MESH" for o in objs):
            # nothing useful selected: use the imported Roblox model(s) (newest last -> pick newest)
            roots = [o for o in bpy.context.view_layer.objects if o.get("rbx_root")]
            if roots:
                root = roots[-1]
                objs = {root, *root.children_recursive}
    else:
        objs = {o for o in bpy.context.view_layer.objects if o.visible_get()}
    return [o for o in objs if o.type in {"MESH", "EMPTY"}]


def _images_of(mat):
    out = []
    if mat and mat.use_nodes:
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image:
                out.append(n.image)
    return out


def validate(objs, check_overlap=True):
    errors, warnings = [], []
    meshes = {}
    for o in objs:
        if o.type == "MESH":
            meshes.setdefault(o.data.name, o)
            if any(s < 0 for s in o.scale):
                warnings.append("%s: negative scale (normals will flip in Unity) - apply scale first" % o.name)
    if not meshes:
        errors.append("nothing to export: select the imported model's root empty (or untick 'Selection only')")
    for name, o in meshes.items():
        me = o.data
        if not len(me.polygons):
            errors.append("%s: mesh has no faces" % name)
            continue
        co = array("f", [0.0]) * (len(me.vertices) * 3)
        me.vertices.foreach_get("co", co)
        if any(math.isnan(c) or math.isinf(c) for c in co):
            errors.append("%s: NaN/inf vertex positions" % name)
        if any(p.loop_total > 4 for p in me.polygons):
            warnings.append("%s: has n-gons (they will be triangulated)" % name)
        names = [l.name for l in me.uv_layers]
        if names[:1] != [uvtools.UV0]:
            errors.append("%s: missing UV0 '%s' (run 'Fix UVs')" % (name, uvtools.UV0))
        if uvtools.UV1 not in names:
            warnings.append("%s: no lightmap UV (Unity can generate one)" % name)
        if check_overlap:
            for ln in ((uvtools.UV1,) if me.get("rbx_tiled") else (uvtools.UV0, uvtools.UV1)):
                if ln in names and not uvtools.is_clean(me, ln):
                    warnings.append("%s: %s overlaps or leaves 0..1" % (name, ln))
        if not me.materials or not any(me.materials):
            warnings.append("%s: no material" % name)
        for m in me.materials:
            for img in _images_of(m):
                path = bpy.path.abspath(img.filepath)
                if not img.packed_file and not os.path.exists(path):
                    errors.append("%s: image file missing: %s" % (m.name, path))
    names = {}
    for m in {m for o in meshes.values() for m in o.data.materials if m}:
        names.setdefault(m.name.lower(), []).append(m.name)
    for k, v in names.items():
        if len(v) > 1:
            warnings.append("material names differ only by case: %s" % v)
    return errors, warnings


def _pack_metal_smooth(desc, out_path):
    rp, mp = desc.get("rough_image"), desc.get("metal_image")
    if not (rp or mp):
        return None
    src = [materials.load_pixels(p) if p else None for p in (mp, rp)]
    sizes = {(s[0], s[1]) for s in src if s}
    if len(sizes) != 1:
        return None
    w, h = sizes.pop()
    n = w * h
    out = array("f", [0.0]) * (n * 4)
    if src[0]:
        m = src[0][2][0::4]
    else:
        m = array("f", [desc.get("metallic", 0.0)]) * n
    out[0::4] = m; out[1::4] = m; out[2::4] = m
    if src[1]:
        out[3::4] = array("f", [1.0 - r for r in src[1][2][0::4]])
    else:
        out[3::4] = array("f", [1.0 - desc.get("roughness", 0.5)]) * n
    return materials.save_png(out_path, w, h, out, non_color=True)


def _flatten_copies(objs):
    """Temporary copies of `objs` in which every mesh has its rotation and scale baked
    into the vertices (objects end up with translation only). FBX/Unity axis conversion
    of rotated objects is unreliable; with no rotations there is nothing to get wrong.
    Returns (copies, cleanup)."""
    from mathutils import Matrix
    coll = bpy.data.collections.new("__rbx_unity_export")
    bpy.context.scene.collection.children.link(coll)
    cmap, worlds = {}, {}
    for o in objs:
        c = o.copy()
        if o.type == "MESH":
            c.data = o.data.copy()
        coll.objects.link(c)
        cmap[o] = c
        worlds[c] = o.matrix_world.copy()
    for c, w in worlds.items():
        c.parent = None
        c.matrix_parent_inverse = Matrix.Identity(4)
        c.matrix_world = w
    meshes = [c for c in cmap.values() if c.type == "MESH"]
    vl = bpy.context.view_layer
    for o in list(vl.objects.selected):
        o.select_set(False)
    for c in meshes:
        c.select_set(True)
    if meshes:
        vl.objects.active = meshes[0]
        with bpy.context.temp_override(selected_objects=meshes, selected_editable_objects=meshes,
                                       active_object=meshes[0], object=meshes[0]):
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    # empties too: keep only their location
    for c in cmap.values():
        if c.type == "EMPTY":
            c.matrix_world = Matrix.Translation(c.matrix_world.translation)
    # restore hierarchy (world positions unchanged)
    for o, c in cmap.items():
        if o.parent in cmap:
            w = c.matrix_world.copy()
            c.parent = cmap[o.parent]
            c.matrix_parent_inverse = Matrix.Identity(4)
            c.matrix_world = w
    # give the copies the real names (originals renamed out of the way meanwhile)
    orig_names = {o: o.name for o in cmap}
    for o in cmap:
        o.name = "__rbx_orig__" + o.name[:40]
    for o, c in cmap.items():
        c.name = orig_names[o]

    def cleanup():
        for c in list(coll.objects):
            data = c.data if c.type == "MESH" else None
            bpy.data.objects.remove(c, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.meshes.remove(data)
        bpy.data.collections.remove(coll)
        for o, n in orig_names.items():
            o.name = n

    return list(cmap.values()), cleanup



def _prop_list(props):
    """Roblox property dict -> [{"name", "value"}] (values as JSON text) for Unity's JsonUtility."""
    if not isinstance(props, dict):
        return []
    return [{"name": k, "value": v if isinstance(v, str) else json.dumps(v)} for k, v in sorted(props.items())]

def _light_markers(objs, side, share_fn):
    """Each Roblox light becomes two empties in the FBX: RBXL_<n> at the light and
    RBXL_<n>_aim one unit along its direction, so Unity gets position AND direction
    right regardless of axis conversion. The sun gets RBXSun / RBXSun_aim the same way.
    Returns (markers, cleanup)."""
    from mathutils import Matrix, Vector
    oset = set(objs)
    roots = [o for o in objs if o.get("rbx_root")]
    lights = [o for o in bpy.context.view_layer.objects
              if o.type == "LIGHT" and o.get("rbx_light") and o.parent in oset]
    made = []

    def empty(name, pos, parent):
        e = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(e)
        e.parent = parent
        e.matrix_parent_inverse = Matrix.Identity(4)
        e.matrix_world = Matrix.Translation(pos)
        made.append(e)
        return e

    side["lights"] = []
    for n, lo in enumerate(lights):
        L = json.loads(lo["rbx_light"])
        S = float(lo.parent.get("rbx_stud_scale", 0.28)) if lo.parent else 0.28
        pos = lo.matrix_world.translation.copy()
        fwd = (lo.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
        name = "RBXL_%d" % n
        empty(name, pos, lo.parent)
        empty(name + "_aim", pos + fwd, lo.parent)
        side["lights"].append({
            "marker": name, "kind": L.get("kind", "PointLight"), "name": L.get("name", ""),
            "enabled": bool(L.get("enabled", True)), "color": list(L.get("color") or (1, 1, 1)),
            "brightness": float(L.get("brightness", 1)), "range": float(L.get("range", 8)) * S,
            "angle": float(L.get("angle", 90)), "shadows": bool(L.get("shadows")),
            "owner": L.get("ownerObject", ""), "path": L.get("path", ""), "face": L.get("face", ""),
            "faceSize": [float(v) * S for v in (L.get("faceSize") or [])],
            "props": _prop_list(L.get("props")),
        })
    side["lighting"] = {}
    for r in roots:
        try:
            info = json.loads(r.get("rbx_lighting", "{}"))
        except Exception:
            info = {}
        if not info:
            continue
        sx, sy, sz = info.get("sunDirection") or (0, 1, 0)
        pos = r.matrix_world.translation.copy()
        empty("RBXSun", pos, r)
        empty("RBXSun_aim", pos + Vector((sx, -sz, sy)).normalized(), r)  # towards the sun
        # Roblox +X and -Z (sky "Rt" and "Ft") so Unity can put each skybox image on the right side
        empty("RBXAxis_rt", pos + Vector((1, 0, 0)), r)
        empty("RBXAxis_ft", pos + Vector((0, 1, 0)), r)
        info["studScale"] = float(r.get("rbx_stud_scale", 0.28))
        sky = info.get("sky") or {}
        sess = r.get("rbx_session", "")
        maps = {}
        for face, key in sky.items():
            if not key:
                continue
            for ext in (".png", ".jpg", ".jpeg", ".bmp"):
                pth = os.path.join(sess, "textures", key + ext)
                if os.path.exists(pth):
                    maps[face] = share_fn(pth, "Sky")
                    break
        if maps:
            info["skyMaps"] = maps
        info["props"] = _prop_list(info.get("props"))
        info["effects"] = [dict(e, props=_prop_list(e.get("props"))) for e in (info.get("effects") or [])
                           if isinstance(e, dict)]
        side["lighting"] = info
        break

    def cleanup():
        for e in made:
            bpy.data.objects.remove(e, do_unlink=True)

    return made, cleanup


def export(filepath, use_selection=True, force=False):
    """Returns (ok, messages)."""
    try:
        import numpy  # noqa: F401  (Blender's own FBX exporter needs it)
    except ImportError:
        return False, ["ERROR: this Blender has no numpy, so Blender's FBX exporter cannot run. "
                       "Distro-packaged Blender (apt) ships without it - use the official build from blender.org."]
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    objs = _collect(use_selection)
    errors, warnings = validate(objs)
    if errors and not force:
        return False, ["ERROR: " + e for e in errors] + ["warning: " + w for w in warnings]

    folder = os.path.dirname(bpy.path.abspath(filepath))
    stem = os.path.splitext(os.path.basename(filepath))[0]
    tex_dir = os.path.join(folder, stem + "_Textures")
    os.makedirs(tex_dir, exist_ok=True)

    mats = sorted({m for o in objs if o.type == "MESH" for m in o.data.materials if m}, key=lambda m: m.name)
    # each source image is written ONCE and shared by every material that uses it
    shared = {}

    def share(src, role):
        key = (os.path.abspath(src), role)
        if key not in shared:
            stem = os.path.splitext(os.path.basename(src))[0]
            if not stem.endswith("_" + role):
                stem = "%s_%s" % (stem, role)
            dst = os.path.join(tex_dir, stem + ".png")
            if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(src):
                shutil.copyfile(src, dst)
            shared[key] = os.path.relpath(dst, folder).replace(os.sep, "/")
        return shared[key]
    side = {"version": 1, "source": "RobloxBlenderBridge", "materials": [],
            # Roblox parts with CastShadow off (object names as in the FBX)
            "noShadow": sorted(o.name for o in objs if o.get("rbx_no_shadow"))}
    for m in mats:
        try:
            desc = json.loads(m.get("rbx_desc", "{}"))
        except Exception:
            desc = {}
        entry = {"name": m.name, "mode": "Opaque", "metallic": 0.0, "smoothness": 0.5,
                 "metallicSmoothness": "", "emission": [], "alpha": 1.0,
                 "emissionMap": "", "emissionStrength": 0.0,
                 "baseMap": "", "normalMap": "", "color": [1.0, 1.0, 1.0]}
        if desc:
            entry["mode"] = {"BLEND": "Transparent", "CLIP": "Cutout"}.get(desc.get("alpha_mode"), "Opaque")
            if not desc.get("base_image") and desc.get("alpha", 1.0) < 0.999:
                entry["mode"] = "Transparent"
            entry["alpha"] = float(desc.get("alpha", 1.0)) if (not desc.get("base_image") or desc.get("base_tint")) else 1.0
            rel = lambda pth: os.path.relpath(pth, folder).replace(os.sep, "/")
            if desc.get("base_image") and os.path.exists(desc["base_image"]):
                entry["baseMap"] = share(desc["base_image"], "BaseColor")
                if desc.get("base_tint"):  # shared texture x part colour
                    entry["tint"] = list(materials.srgb_to_linear(desc["base_tint"]))
                    entry["alpha"] = float(desc.get("alpha", 1.0))
            else:
                entry["color"] = list(materials.srgb_to_linear(desc.get("base_rgb", (1, 1, 1))))
            if desc.get("normal_image") and os.path.exists(desc["normal_image"]):
                entry["normalMap"] = share(desc["normal_image"], "Normal")
            entry["metallic"] = float(desc.get("metallic", 0.0))
            entry["smoothness"] = 1.0 - float(desc.get("roughness", 0.5))
            if desc.get("emission"):
                entry["emission"] = list(materials.srgb_to_linear(desc["emission"]))
            if desc.get("emission_image") and os.path.exists(desc["emission_image"]):
                entry["emissionMap"] = share(desc["emission_image"], "Emission")
                entry["emissionStrength"] = float(desc.get("emission_strength", 1.0))
            mskey = hashlib.sha1(repr((desc.get("rough_image"), desc.get("metal_image"),
                                       round(float(desc.get("metallic", 0)), 3), round(float(desc.get("roughness", 0.5)), 3))).encode()).hexdigest()[:10]
            ms_path = os.path.join(tex_dir, "MS_%s_MetallicSmoothness.png" % mskey)
            if ("ms", mskey) in shared:
                packed = shared[("ms", mskey)]
            elif desc.get("rough_image") or desc.get("metal_image"):
                packed = shared[("ms", mskey)] = _pack_metal_smooth(desc, ms_path)
            else:
                packed = None
            if packed:
                entry["metallicSmoothness"] = os.path.relpath(packed, folder).replace(os.sep, "/")
                entry["metallic"] = 1.0
                entry["smoothness"] = 1.0
        side["materials"].append(entry)

    markers, cleanup_markers = _light_markers(objs, side, share)
    try:
        copies, cleanup = _flatten_copies(objs + markers)
    except Exception:
        cleanup_markers()
        raise
    for o in bpy.context.view_layer.objects.selected:
        o.select_set(False)
    for c in copies:
        c.select_set(True)

    kw = dict(
        filepath=filepath, use_selection=True, object_types={"EMPTY", "MESH"},
        use_mesh_modifiers=True, global_scale=1.0, apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_ALL", axis_forward="Z", axis_up="Y",
        bake_space_transform=False, mesh_smooth_type="FACE", use_tspace=True,
        use_triangles=True, use_custom_props=False, add_leaf_bones=False,
        bake_anim=False, path_mode="STRIP", embed_textures=False, batch_mode="OFF",
    )
    try:
        try:
            bpy.ops.export_scene.fbx(**kw)
        except TypeError:
            kw.pop("use_triangles", None)
            bpy.ops.export_scene.fbx(**kw)
    finally:
        cleanup()
        cleanup_markers()
        for o in objs:
            o.select_set(True)

    with open(os.path.join(folder, stem + ".roblox.json"), "w", encoding="utf-8") as f:
        json.dump(side, f, indent=1)
    msgs = ["exported %s (%d objects, %d materials, %d lights)" % (filepath, len(objs), len(mats), len(side.get("lights") or []))]
    msgs += ["warning: " + w for w in warnings] + (["FORCED despite: " + e for e in errors] if errors else [])
    return True, msgs
