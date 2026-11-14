"""Reads screenshots of tools/calibrate_materials.lua: the real average albedo of each Roblox base
material, compared with what the Unity side computes from the same colour map.

    blender -b --factory-startup --python tools/fit_materials.py -- shot1.png ... [-o material_albedo.json]

Writes {material: scale} (Roblox albedo / Unity's albedo, linear, white part colour). The Unity
importer multiplies built-in material colours by it (unity/.../Editor/material_albedo.json).
"""
import glob
import importlib.util
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fit = mod("fit_lighting", os.path.join(HERE, "fit_lighting.py"))
amb = mod("fit_ambient", os.path.join(HERE, "fit_ambient.py"))

MATERIALS = [
    "SmoothPlastic", "Plastic", "Wood", "WoodPlanks", "Brick", "Cobblestone", "Concrete", "Metal",
    "CorrodedMetal", "DiamondPlate", "Foil", "Granite", "Marble", "Slate", "Sand", "Fabric",
    "Glass", "Ice", "Grass", "Pebble", "Asphalt", "Basalt", "CrackedLava", "Glacier",
    "Ground", "LeafyGrass", "Limestone", "Mud", "Pavement", "Rock", "Salt", "Sandstone",
    "Snow", "Cardboard", "Carpet", "CeramicTiles", "ClayRoofTiles", "RoofShingles", "Leather", "Plaster",
    "Rubber", "Neon", "ForceField",
]
COLOURS = {13: (1.0, 1.0, 1.0), 14: (109 / 255, 70 / 255, 70 / 255), 15: (128 / 255, 128 / 255, 128 / 255)}
EC = -1.0


def colour_map(material):
    """The 2022 colour map the export downloaded for this material, as float RGBA (or None)."""
    import bpy
    data = json.load(open(os.path.join(HERE, "..", "roblox-to-blender", "blender-addon", "roblox_bridge", "builtin_materials.json")))
    aid = (data["current"].get(material) or {}).get("color")
    if not aid:
        return None
    for p in glob.glob(os.path.expanduser("~/RobloxExports/*/textures/a%s.png" % aid)):
        im = bpy.data.images.load(p)
        im.colorspace_settings.name = "Non-Color"
        a = np.array(im.pixels[:], dtype=np.float32).reshape(-1, 4)
        bpy.data.images.remove(im)
        return a
    return None


def unity_albedo(tex, col):
    """Unity Roblox Lit: (tex * lerp(1, colour, alpha))^2 averaged (tint-mask rule, gamma 2)."""
    if tex is None:
        return np.array(col) ** 2
    c = tex[:, :3] * (1 + (np.array(col)[None, :] - 1) * tex[:, 3:4])
    return (c * c).mean(axis=0)


def main(argv):
    out = "material_albedo.json"
    if "-o" in argv:
        i = argv.index("-o"); out = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    shots = {}
    for p in argv:
        try:
            sid, cells = fit.read_shot(p)
        except Exception as e:
            print("skip %s: %s" % (os.path.basename(p), e))
            continue
        if sid in COLOURS:
            shots.setdefault(sid, []).append(cells)
    k = 2.0 ** EC
    res = {}
    for i, m in enumerate(MATERIALS):
        c, r = i % fit.COLS, i // fit.COLS
        tex = colour_map(m)
        row = {}
        for sid, col in COLOURS.items():
            if sid not in shots:
                continue
            px = np.median(np.array(shots[sid]), axis=0)[r, c]
            meas = np.array([amb.untonemap(v) for v in px]) / k   # Ambient = 1, so this is the albedo
            pred = unity_albedo(tex, col)
            row[sid] = (meas, pred)
        if not row:
            continue
        ratios = [float(np.mean(mv) / max(np.mean(pv), 1e-4)) for mv, pv in row.values()]
        res[m] = {"scale": float(np.median(ratios)), "measured": {str(s): v[0].round(4).tolist() for s, v in row.items()},
                  "unity": {str(s): v[1].round(4).tolist() for s, v in row.items()}, "texture": tex is not None}
        print("%-14s scale %.2f   white roblox %s unity %s" % (m, res[m]["scale"],
              res[m]["measured"].get("13"), res[m]["unity"].get("13")))
    with open(out, "w") as f:
        json.dump({m: round(v["scale"], 4) for m, v in res.items()}, f, indent=1)
    with open(os.path.splitext(out)[0] + "_detail.json", "w") as f:
        json.dump(res, f, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
