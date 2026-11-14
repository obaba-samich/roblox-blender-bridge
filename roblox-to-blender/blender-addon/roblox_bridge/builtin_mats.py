"""Roblox's built-in material textures (Wood, Brick, Metal, ...).

Roblox publishes the real asset ids of every base material's ColorMap / Normal /
Metalness / Roughness (creator-docs: parts/materials, "Asset and property reference").
They are Roblox-owned images and download at full resolution without a login.
Downloads are cached once per machine in <export root>/_materials/.
"""

import json
import os
import shutil

SKIP = {"Glass", "Plastic", "SmoothPlastic", "Neon", "ForceField"}  # no texture / see-through shaders

_data = None


def data():
    global _data
    if _data is None:
        with open(os.path.join(os.path.dirname(__file__), "builtin_materials.json")) as f:
            _data = json.load(f)
    return _data


def maps_for(material, use2022=True):
    """-> {"color": id, "normal": id, ...} or None."""
    if material in SKIP:
        return None
    d = data()
    t = d["current"] if use2022 else d["pre2022"]
    return t.get(material) or (d["current"].get(material) if not use2022 else None)


def needs_builtin(rec):
    return not (rec.get("texture") or rec.get("surface") or rec.get("variant"))


def prepare(session_dir, scene, fetch_image, progress=lambda m: None):
    """Download the maps of every built-in material the scene uses into
    session/textures/a<id>.png (via the per-machine cache). Returns notes."""
    use2022 = scene.get("use2022Materials", True) is not False
    mats = sorted(m for m in {p.get("material") for p in scene.get("parts") or [] if needs_builtin(p)} - {None}
                  if maps_for(m, use2022))
    cache = os.path.join(os.path.dirname(session_dir.rstrip("/\\")), "_materials", "2022" if use2022 else "legacy")
    os.makedirs(cache, exist_ok=True)
    tex_dir = os.path.join(session_dir, "textures")
    notes = []
    for m in mats:
        maps = maps_for(m, use2022)
        if not maps:
            continue
        for kind, aid in maps.items():
            dst = os.path.join(tex_dir, "a%s.png" % aid)
            if os.path.exists(dst):
                continue
            cp = os.path.join(cache, "%s.png" % aid)
            if not os.path.exists(cp):
                progress("downloading %s %s map" % (m, kind))
                try:
                    raw, how = fetch_image(aid)
                    if how != "full":
                        notes.append("material %s %s: only a thumbnail was available" % (m, kind))
                    with open(cp + ".part", "wb") as f:
                        f.write(raw)
                    os.replace(cp + ".part", cp)
                except Exception as e:
                    notes.append("material %s %s map: download FAILED (%s)" % (m, kind, e))
                    continue
            try:
                os.link(cp, dst)   # same file as the cache, no second copy on disk
            except OSError:
                shutil.copyfile(cp, dst)
    if mats:
        notes.append("built-in materials: %s (%s set)" % (", ".join(mats), "2022" if use2022 else "pre-2022"))
    return notes


def variant_for(rec, scene, session_dir):
    """Give a plain part its built-in material as a tiled 'variant' (same path as MaterialVariants)."""
    if not needs_builtin(rec):
        return None
    maps = maps_for(rec.get("material"), scene.get("use2022Materials", True) is not False)
    if not maps:
        return None
    v = {"name": "Builtin", "studsPerTile": data()["studs_per_tile"], "pattern": "Regular", "builtin": True}
    for kind, aid in maps.items():
        if os.path.exists(os.path.join(session_dir, "textures", "a%s.png" % aid)):
            v[kind] = "a%s" % aid
    return v if v.get("color") else None
