"""Roblox appearance -> plain PBR materials.

Everything Roblox does at render time (part Color showing through texture
alpha, SurfaceAppearance tint, decal Color3/Transparency, SpecialMesh
VertexColor) is COMPOSITED INTO THE PNG here, so the result is a simple
"image -> Base Color" material that looks identical in Blender, in the FBX,
and in Unity (whose standard shaders can't reproduce Roblox's blending).

Pure python pixel math (Ubuntu's Blender ships without numpy).
"""

import hashlib
import json
import os
import re
from array import array

import bpy

# Roblox Material enum -> (roughness, metallic). Approximations of the look;
# Roblox's own material textures are not accessible to plugins.
PBR = {
    "Plastic": (0.55, 0.0), "SmoothPlastic": (0.25, 0.0), "Neon": (0.5, 0.0),
    "Wood": (0.7, 0.0), "WoodPlanks": (0.7, 0.0), "Marble": (0.3, 0.0),
    "Slate": (0.8, 0.0), "Concrete": (0.9, 0.0), "Granite": (0.6, 0.0),
    "Brick": (0.9, 0.0), "Pebble": (0.85, 0.0), "Cobblestone": (0.85, 0.0),
    "Rock": (0.9, 0.0), "Sandstone": (0.85, 0.0), "Basalt": (0.85, 0.0),
    "CrackedLava": (0.8, 0.0), "Limestone": (0.85, 0.0), "Pavement": (0.9, 0.0),
    "CorrodedMetal": (0.75, 0.6), "DiamondPlate": (0.35, 1.0), "Foil": (0.2, 1.0),
    "Metal": (0.35, 1.0), "Grass": (0.95, 0.0), "LeafyGrass": (0.95, 0.0),
    "Sand": (0.95, 0.0), "Fabric": (0.95, 0.0), "Snow": (0.7, 0.0),
    "Mud": (0.9, 0.0), "Ground": (0.95, 0.0), "Asphalt": (0.9, 0.0),
    "Salt": (0.8, 0.0), "Ice": (0.1, 0.0), "Glacier": (0.15, 0.0),
    "Glass": (0.05, 0.0), "ForceField": (0.5, 0.0), "Cardboard": (0.9, 0.0),
    "Carpet": (1.0, 0.0), "CeramicTiles": (0.3, 0.0), "ClayRoofTiles": (0.8, 0.0),
    "RoofShingles": (0.9, 0.0), "Leather": (0.6, 0.0), "Plaster": (0.9, 0.0),
    "Rubber": (0.8, 0.0),
}


def srgb_to_linear(c):
    return tuple((x / 12.92) if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


# ----------------------------------------------------------------- pixels
def load_pixels(path):
    """-> (w, h, array('f') RGBA, Blender row order). Values are the file's
    stored values (sRGB-encoded for color images), straight alpha."""
    img = bpy.data.images.load(path, check_existing=False)
    try:
        img.alpha_mode = "STRAIGHT"
        w, h = img.size
        if w == 0 or h == 0:
            raise ValueError("could not read image %s" % path)
        px = array("f", [0.0]) * (w * h * 4)
        img.pixels.foreach_get(px)
        return w, h, px
    finally:
        bpy.data.images.remove(img)


def save_png(path, w, h, px, non_color=False):
    img = bpy.data.images.new("__rbx_tmp", w, h, alpha=True)
    try:
        img.alpha_mode = "STRAIGHT"
        if non_color:
            img.colorspace_settings.name = "Non-Color"
        img.pixels.foreach_set(px)
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return path


def crop_letterbox(src, dst):
    """Thumbnails letterbox non-square images into a square. Trim fully transparent
    bands only when they're symmetric (top == bottom or left == right), so genuine
    transparency at one edge is left alone."""
    w, h, px = load_pixels(src)
    a = px[3::4]

    def clear_row(y):
        return all(v == 0.0 for v in a[y * w:(y + 1) * w])

    def clear_col(x):
        return all(a[y * w + x] == 0.0 for y in range(h))

    top = 0
    while top < h // 2 and clear_row(h - 1 - top):  # Blender rows are bottom-up
        top += 1
    bot = 0
    while bot < h // 2 and clear_row(bot):
        bot += 1
    left = 0
    while left < w // 2 and clear_col(left):
        left += 1
    right = 0
    while right < w // 2 and clear_col(w - 1 - right):
        right += 1
    y0 = y1 = x0 = x1 = 0
    if top and abs(top - bot) <= 1 and top + bot < h - 2:
        y0, y1 = bot, top
    if left and abs(left - right) <= 1 and left + right < w - 2:
        x0, x1 = left, right
    if not (y0 or y1 or x0 or x1):
        return src
    nw, nh = w - x0 - x1, h - y0 - y1
    out = array("f", [0.0]) * (nw * nh * 4)
    for y in range(nh):
        s0 = ((y + y0) * w + x0) * 4
        out[y * nw * 4:(y + 1) * nw * 4] = px[s0:s0 + nw * 4]
    return save_png(dst, nw, nh, out)


def _alpha_kind(a):
    """'OPAQUE', 'CLIP' (only ~0/1 alpha) or 'BLEND' (real gradients)."""
    n = len(a)
    if n == 0:
        return "OPAQUE"
    lo = sum(1 for x in a if x < 0.98)
    if lo == 0:
        return "OPAQUE"
    mid = sum(1 for x in a if 0.02 < x < 0.98)
    return "BLEND" if mid > n * 0.01 else "CLIP"


def composite(src_path, out_path, mode, part_rgb=(1, 1, 1), tint=(1, 1, 1), alpha_mul=1.0):
    """mode:
        'overlay'     rgb = lerp(part_rgb, tex*tint, a); alpha = alpha_mul
                      (SurfaceAppearance AlphaMode=Overlay - part Color shows through)
        'opaque'      rgb = tex*tint; alpha ignored; alpha = alpha_mul
                      (SurfaceAppearance AlphaMode=Opaque, MeshPart.TextureID at Transparency 0)
        'tintmask'    rgb = tex * lerp(1, tint, a);    alpha = alpha_mul
        'transparent' rgb = tex*tint;  alpha = a*alpha_mul
                      (SurfaceAppearance AlphaMode=Transparency, decals, SpecialMesh)
    Returns (path, alpha_kind)."""
    w, h, px = load_pixels(src_path)
    r, g, b, a = px[0::4], px[1::4], px[2::4], px[3::4]
    tr, tg, tb = tint
    if mode == "overlay":
        pr, pg, pb = part_rgb
        px[0::4] = array("f", [pr + (x * tr - pr) * al for x, al in zip(r, a)])
        px[1::4] = array("f", [pg + (x * tg - pg) * al for x, al in zip(g, a)])
        px[2::4] = array("f", [pb + (x * tb - pb) * al for x, al in zip(b, a)])
        px[3::4] = array("f", [alpha_mul]) * len(a)
        kind = "OPAQUE" if alpha_mul >= 0.999 else "BLEND"
    elif mode == "opaque":
        if (tr, tg, tb) != (1, 1, 1):
            px[0::4] = array("f", [x * tr for x in r])
            px[1::4] = array("f", [x * tg for x in g])
            px[2::4] = array("f", [x * tb for x in b])
        px[3::4] = array("f", [alpha_mul]) * len(a)
        kind = "OPAQUE" if alpha_mul >= 0.999 else "BLEND"
    elif mode == "tintmask":
        px[0::4] = array("f", [x * (1 + (tr - 1) * al) for x, al in zip(r, a)])
        px[1::4] = array("f", [x * (1 + (tg - 1) * al) for x, al in zip(g, a)])
        px[2::4] = array("f", [x * (1 + (tb - 1) * al) for x, al in zip(b, a)])
        px[3::4] = array("f", [alpha_mul]) * len(a)
        kind = "OPAQUE" if alpha_mul >= 0.999 else "BLEND"
    else:
        if (tr, tg, tb) != (1, 1, 1):
            px[0::4] = array("f", [x * tr for x in r])
            px[1::4] = array("f", [x * tg for x in g])
            px[2::4] = array("f", [x * tb for x in b])
        if alpha_mul < 0.999:
            px[3::4] = array("f", [x * alpha_mul for x in a])
            kind = "BLEND"
        else:
            kind = _alpha_kind(a)
    save_png(out_path, w, h, px)
    return out_path, kind


def emission_map(src_path, out_path, tint=(1, 1, 1)):
    """Roblox EmissiveMask (grayscale) * EmissiveTint -> RGB emission colour map."""
    w, h, px = load_pixels(src_path)
    m = px[0::4]
    px[0::4] = array("f", [x * tint[0] for x in m])
    px[1::4] = array("f", [x * tint[1] for x in m])
    px[2::4] = array("f", [x * tint[2] for x in m])
    px[3::4] = array("f", [1.0]) * (w * h)
    return save_png(out_path, w, h, px)


def materialize_tint(desc, out_dir):
    """Bake a shared-texture tint into its own image (needed before rebaking)."""
    if not desc.get("base_tint") or not desc.get("base_image"):
        return desc
    d = dict(desc)
    out = os.path.join(out_dir, "%s_BaseColor.png" % desc["name"])
    d["base_image"], _k = composite(desc["base_image"], out, "opaque", tuple(desc["base_tint"]),
                                    tuple(desc["base_tint"]), float(desc.get("alpha", 1.0)))
    d.pop("base_tint")
    d["alpha"] = 1.0
    return d


def copy_data_map(src_path, out_path):
    """Normal/roughness/metalness maps: copy under a descriptive name."""
    w, h, px = load_pixels(src_path)
    return save_png(out_path, w, h, px, non_color=True)


# -------------------------------------------------------------- descriptors
def _sig(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:8]


def _q(c):
    return [round(float(x), 4) for x in c]


class Resolver:
    """Turns part/decal records into material descriptors (cached)."""

    def __init__(self, session_dir, notes):
        self.dir = session_dir
        self.baked = os.path.join(session_dir, "baked")
        os.makedirs(self.baked, exist_ok=True)
        self.cache = {}
        self.notes = notes
        self.thumbs, self.cropped = set(), set()
        tp = os.path.join(session_dir, "textures", "thumbnails.json")
        if os.path.exists(tp):
            try:
                with open(tp) as f:
                    self.thumbs = set(json.load(f))
            except Exception:
                pass

    def tex(self, key):
        if not key:
            return None
        for ext in (".png", ".jpg", ".jpeg", ".tga", ".bmp"):
            p = os.path.join(self.dir, "textures", key + ext)
            if os.path.exists(p):
                if key in self.thumbs and key not in self.cropped:
                    self.cropped.add(key)
                    p = crop_letterbox(p, os.path.join(self.dir, "textures", key + "_crop.png"))
                elif key in self.cropped:
                    c = os.path.join(self.dir, "textures", key + "_crop.png")
                    p = c if os.path.exists(c) else p
                return p
        self.notes.append("texture %s missing (Studio couldn't read it and download failed) - color only" % key)
        return None

    def _desc(self, name, sig_src, build):
        sig = _sig(sig_src)
        if sig in self.cache:
            return self.cache[sig]
        safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(name)).strip("_") or "Mat"
        mname = "%s_%s" % (safe[:40], sig)  # also used in file names -> filesystem/FBX/Unity safe
        d = build(mname)
        d["name"] = mname
        d["sig"] = sig
        self.cache[sig] = d
        return d

    def part(self, rec):
        color = tuple(rec["color"])
        transp = float(rec.get("transparency", 0) or 0)
        alpha = max(0.0, min(1.0, 1.0 - transp))
        rmat = rec.get("material", "Plastic")
        rough, metal = PBR.get(rmat, (0.6, 0.0))
        sa = rec.get("surface")
        tex = rec.get("texture")
        special = rec.get("special")
        variant = rec.get("variant") if not (sa or tex) else None
        sig_src = {"c": _q(color), "a": round(alpha, 4), "m": rmat, "sa": sa, "tex": tex, "mv": variant,
                   "vc": _q(special["vertexColor"]) if special else None, "kind": rec.get("meshKind")}
        base_name = (rmat if not (sa or tex) else "Tex")
        if variant:
            base_name = "%s_%s" % (rmat, variant.get("name", "Variant"))

        def build(mname):
            d = {"base_rgb": list(color), "base_image": None, "alpha": alpha,
                 "alpha_mode": "OPAQUE" if alpha >= 0.999 else "BLEND",
                 "normal_image": None, "rough_image": None, "metal_image": None,
                 "roughness": rough, "metallic": metal, "emission": None,
                 "roblox_material": rmat}
            if rmat == "Neon":
                d["emission"] = list(color)
            out = lambda suffix: os.path.join(self.baked, "%s_%s.png" % (mname, suffix))
            if sa:
                cm = self.tex(sa.get("color"))
                tint = tuple(sa.get("tint") or (1, 1, 1))
                if cm:
                    am = sa.get("alphaMode") or "Overlay"
                    if am == "Transparency":
                        img, kind = composite(cm, out("BaseColor"), "transparent", color, tint, alpha)
                        # Roblox: at part Transparency 0 this is a hard cutout (foliage);
                        # >= 0.02 switches to smooth blending
                        if kind != "OPAQUE":
                            kind = "CLIP" if transp < 0.02 else "BLEND"
                    else:
                        mode = {"Overlay": "overlay", "TintMask": "tintmask", "Opaque": "opaque"}.get(am, "overlay")
                        img, kind = composite(cm, out("BaseColor"), mode, color, tint, alpha)
                    d["base_image"], d["alpha_mode"] = img, kind
                    d["alpha"] = 1.0
                for k, slot in (("normal", "normal_image"), ("roughness", "rough_image"), ("metalness", "metal_image")):
                    p = self.tex(sa.get(k))
                    if p:
                        d[slot] = copy_data_map(p, out({"normal": "Normal", "roughness": "Roughness", "metalness": "Metallic"}[k]))
                if d["rough_image"]:
                    d["roughness"] = 1.0
                if d["metal_image"]:
                    d["metallic"] = 1.0
                elif sa.get("color"):
                    d["metallic"] = 0.0  # SurfaceAppearance without metal map = non-metal
                self._emissive(d, sa, out)
            elif variant:
                # MaterialVariant / built-in material: ONE shared texture per map, the part
                # Color is a material tint (colour map x tint), tiled every StudsPerTile studs
                cm = self.tex(variant.get("color"))
                if cm:
                    d["base_image"] = cm
                    d["base_tint"] = list(color)
                    d["alpha_mode"] = "OPAQUE" if alpha >= 0.999 else "BLEND"
                for k, slot in (("normal", "normal_image"), ("roughness", "rough_image"), ("metalness", "metal_image")):
                    pp = self.tex(variant.get(k))
                    if pp:
                        d[slot] = pp
                if d["rough_image"]:
                    d["roughness"] = 1.0
                if d["metal_image"]:
                    d["metallic"] = 1.0
                if any(d.get(k) for k in ("base_image", "normal_image", "rough_image", "metal_image")):
                    d["tile_studs"] = float(variant.get("studsPerTile") or 10)
            elif tex:
                p = self.tex(tex)
                if p:
                    if rec.get("meshKind") == "SpecialMesh":
                        vc = tuple(special["vertexColor"]) if special else (1, 1, 1)
                        d["base_image"], d["alpha_mode"] = composite(p, out("BaseColor"), "transparent", color, vc, alpha)
                    elif transp < 0.01:
                        # MeshPart.TextureID: alpha is ignored at Transparency 0 and the
                        # part Color does NOT show through (devforum-confirmed)
                        d["base_image"], d["alpha_mode"] = composite(p, out("BaseColor"), "opaque", color, (1, 1, 1), 1.0)
                    else:
                        # Transparency > 0.01: texture alpha becomes see-through
                        d["base_image"], _k = composite(p, out("BaseColor"), "transparent", color, (1, 1, 1), alpha)
                        d["alpha_mode"] = "BLEND"
                    d["alpha"] = 1.0
            return d

        return self._desc(rec.get("name", "Part") if (sa or tex) else base_name, sig_src, build)

    def _emissive(self, desc, src, out):
        p = self.tex(src.get("emissive"))
        strength = float(src.get("emissiveStrength", 1) or 0)
        if p and strength > 0:
            desc["emission_image"] = emission_map(p, out("Emission"), tuple(src.get("emissiveTint") or (1, 1, 1)))
            desc["emission_strength"] = strength

    def decal(self, d, part_name):
        p = self.tex(d.get("texture"))
        if not p:
            return None
        alpha = max(0.0, min(1.0, 1.0 - float(d.get("transparency", 0) or 0)))
        sig_src = {"decal": d.get("texture"), "c": _q(d.get("color", (1, 1, 1))), "a": round(alpha, 4),
                   "pbr": [d.get(k) for k in ("normal", "roughness", "metalness", "emissive")],
                   "e": [_q(d.get("emissiveTint") or (1, 1, 1)), d.get("emissiveStrength", 1)]}

        def build(mname):
            img, kind = composite(p, os.path.join(self.baked, "%s_BaseColor.png" % mname), "transparent",
                                  (1, 1, 1), tuple(d.get("color", (1, 1, 1))), alpha)
            out = lambda suffix: os.path.join(self.baked, "%s_%s.png" % (mname, suffix))
            desc = {"base_rgb": [1, 1, 1], "base_image": img, "alpha": 1.0,
                    "alpha_mode": "CLIP" if kind == "OPAQUE" else kind,
                    "normal_image": None, "rough_image": None, "metal_image": None,
                    "roughness": 0.6, "metallic": 0.0, "emission": None, "roblox_material": "Decal"}
            for k, slot, sfx in (("normal", "normal_image", "Normal"), ("roughness", "rough_image", "Roughness"),
                                 ("metalness", "metal_image", "Metallic")):
                pp = self.tex(d.get(k))
                if pp:
                    desc[slot] = copy_data_map(pp, out(sfx))
            if desc["rough_image"]:
                desc["roughness"] = 1.0
            if desc["metal_image"]:
                desc["metallic"] = 1.0
            self._emissive(desc, d, out)
            return desc

        return self._desc("Decal_" + part_name, sig_src, build)


# ------------------------------------------------------------ bpy material
def _inp(node, *names):
    for n in names:
        s = node.inputs.get(n)
        if s is not None:
            return s
    return None


def _image(path, non_color=False):
    img = bpy.data.images.load(path, check_existing=True)
    img.alpha_mode = "STRAIGHT"
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    return img


def make_material(desc, mat=None):
    """Build (or rebuild) a Principled BSDF material from a descriptor.
    Uses only node setups the FBX exporter understands."""
    if mat is None:
        mat = bpy.data.materials.get(desc["name"]) or bpy.data.materials.new(desc["name"])
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (100, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    lin = srgb_to_linear(desc["base_rgb"])
    _inp(bsdf, "Base Color").default_value = (*lin, 1.0)
    tint = desc.get("base_tint")
    _inp(bsdf, "Roughness").default_value = desc["roughness"]
    _inp(bsdf, "Metallic").default_value = desc["metallic"]
    _inp(bsdf, "Alpha").default_value = desc["alpha"] if (not desc["base_image"] or desc.get("base_tint")) else 1.0
    mat.diffuse_color = (*lin, desc["alpha"])

    y = 300
    if desc["base_image"]:
        t = nt.nodes.new("ShaderNodeTexImage"); t.location = (-400, y); y -= 300
        t.image = _image(desc["base_image"])
        t.interpolation = "Linear"
        t.extension = "REPEAT"
        if tint:
            mx = nt.nodes.new("ShaderNodeMix"); mx.location = (-150, y + 300)
            mx.data_type = "RGBA"; mx.blend_type = "MULTIPLY"
            mx.inputs["Factor"].default_value = 1.0
            nt.links.new(t.outputs["Color"], mx.inputs[6])
            mx.inputs[7].default_value = (*srgb_to_linear(tint), 1.0)
            nt.links.new(mx.outputs[2], _inp(bsdf, "Base Color"))
        else:
            nt.links.new(t.outputs["Color"], _inp(bsdf, "Base Color"))
        if desc["alpha_mode"] != "OPAQUE" and not tint:
            nt.links.new(t.outputs["Alpha"], _inp(bsdf, "Alpha"))
    if desc.get("normal_image"):
        t = nt.nodes.new("ShaderNodeTexImage"); t.location = (-400, y); y -= 300
        t.image = _image(desc["normal_image"], True)
        nm = nt.nodes.new("ShaderNodeNormalMap"); nm.location = (-150, y + 150)
        nt.links.new(t.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], _inp(bsdf, "Normal"))
    for key, sock in (("rough_image", "Roughness"), ("metal_image", "Metallic")):
        if desc.get(key):
            t = nt.nodes.new("ShaderNodeTexImage"); t.location = (-400, y); y -= 300
            t.image = _image(desc[key], True)
            nt.links.new(t.outputs["Color"], _inp(bsdf, sock))
    if desc.get("emission_image"):
        t = nt.nodes.new("ShaderNodeTexImage"); t.location = (-400, y); y -= 300
        t.image = _image(desc["emission_image"])
        nt.links.new(t.outputs["Color"], _inp(bsdf, "Emission Color", "Emission"))
        es = _inp(bsdf, "Emission Strength")
        if es:
            es.default_value = float(desc.get("emission_strength", 1.0))
    elif desc.get("emission"):
        _inp(bsdf, "Emission Color", "Emission").default_value = (*srgb_to_linear(desc["emission"]), 1.0)
        s = _inp(bsdf, "Emission Strength")
        if s:
            s.default_value = 1.0

    mode = desc["alpha_mode"] if (desc["base_image"] or desc["alpha"] < 0.999) else "OPAQUE"
    if hasattr(mat, "surface_render_method"):  # 4.2+
        mat.surface_render_method = "BLENDED" if mode == "BLEND" else "DITHERED"
    if hasattr(mat, "blend_method"):
        mat.blend_method = {"BLEND": "BLEND", "CLIP": "CLIP", "OPAQUE": "OPAQUE"}[mode]
    if hasattr(mat, "shadow_method"):
        mat.shadow_method = "HASHED" if mode != "OPAQUE" else "OPAQUE"
    if hasattr(mat, "alpha_threshold"):
        mat.alpha_threshold = 0.5
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = desc.get("roblox_material") != "Decal" and mode == "OPAQUE"
    mat["rbx_desc"] = json.dumps(desc)
    return mat
