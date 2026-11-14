"""Build the Blender scene from a session/dump folder. Main thread only."""

import json
import math
import os
import time
import traceback
from array import array

import bpy
from mathutils import Matrix, Vector

from . import builtin_mats, geometry, materials, meshio, uvtools

# Roblox (x, y, z) [Y up, right-handed]  ->  Blender (x, -z, y) [Z up, right-handed]
C = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))

DEFAULTS = {
    "stud_scale": 0.28,    # metres per stud
    "flip_v": True,        # Roblox UV origin is top-left
    "rebake": True,        # rebake textures onto non-overlapping UVs when needed
    "rebake_tiled": False, # also rebake repeating (tiled) textures - huge images, usually unwanted
    "bake_samples": 8,
    "max_bake_res": 4096,
    "lightmap_margin": 0.01,
}


def cf_matrix(cf, S):
    x, y, z = cf[0], cf[1], cf[2]
    r = cf[3:12]
    R = Matrix(((r[0], r[1], r[2]), (r[3], r[4], r[5]), (r[6], r[7], r[8])))
    Rb = (C @ R @ C.transposed()).to_quaternion().to_matrix()  # re-orthonormalise
    M = Rb.to_4x4()
    M.translation = Vector((x * S, -z * S, y * S))
    return M


def mesh_from_geo(name, g, S):
    P = [(x * S, -z * S, y * S) for (x, y, z) in g["P"]]
    me = bpy.data.meshes.new(name)
    me.from_pydata(P, [], g["T"])
    if g["UV"] is not None:
        uvl = me.uv_layers.new(name=uvtools.UV0, do_init=False)
        uvl.data.foreach_set("uv", array("f", [c for uv in g["UV"] for c in uv]))
    if g["N"] is not None:
        a = me.attributes.new("__rbx_n", "FLOAT_VECTOR", "CORNER")
        a.data.foreach_set("vector", array("f", [c for (x, y, z) in g["N"] for c in (x, -z, y)]))
    me.validate(clean_customdata=False)
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    a = me.attributes.get("__rbx_n")
    if a is not None:
        buf = array("f", [0.0]) * (len(me.loops) * 3)
        a.data.foreach_get("vector", buf)
        me.attributes.remove(a)
        if hasattr(me, "use_auto_smooth"):  # Blender < 4.1
            me.use_auto_smooth = True
        me.normals_split_custom_set([(buf[i], buf[i + 1], buf[i + 2]) for i in range(0, len(buf), 3)])
    me.update()
    return me


def _clean_name(s):
    s = "".join(ch for ch in str(s) if ch >= " " and ch not in ':"\\/<>|?*')
    return (s.strip() or "Part")[:58]


class Builder:
    def __init__(self, session_dir, opts=None, progress=print):
        self.dir = session_dir
        self.o = dict(DEFAULTS, **(opts or {}))
        self.progress = progress
        self.notes = []
        self.mats = {}        # desc sig -> bpy material
        self.meshes = {}      # geometry key -> bpy mesh
        self.mesh_info = {}   # bpy mesh name -> (owner obj, desc)
        self.raw = {}         # mesh asset key -> meshio dict | Exception

    # ----------------------------------------------------------- helpers
    def note(self, msg):
        self.notes.append(msg)

    def raw_mesh(self, key):
        if key not in self.raw:
            p = os.path.join(self.dir, "meshes", key + ".rbm")
            try:
                with open(p, "rb") as f:
                    self.raw[key] = meshio.read_rbm(f.read())
            except Exception as e:
                self.raw[key] = e
        return self.raw[key]

    def material(self, desc):
        m = self.mats.get(desc["sig"])
        if m is None:
            m = self.mats[desc["sig"]] = materials.make_material(desc)
        return m

    def empty(self, name, coll, parent=None):
        e = bpy.data.objects.new(_clean_name(name), None)
        e.empty_display_size = 0.5
        coll.objects.link(e)
        e.parent = parent
        return e

    def get_mesh(self, key, name, make_geo, desc):
        me = self.meshes.get(key)
        if me is not None:
            return me
        g = make_geo()
        g, notes = geometry.clean(g)
        if key[0] in ("M", "S", "U"):  # only report on user meshes, not our own primitives
            for n in notes:
                self.note("%s: %s" % (name, n))
        if not g["T"]:
            raise ValueError("no usable triangles")
        me = mesh_from_geo(_clean_name(name), g, self.o["stud_scale"])
        me.materials.append(self.material(desc))
        self.meshes[key] = me
        return me

    # ------------------------------------------------------------- parts
    def part(self, rec, coll, parent, idx=-1):
        size = tuple(rec["size"])
        shape = rec.get("shape", "Block")
        name = rec.get("name", "Part")
        desc = self.res.part(rec)
        special = rec.get("special")
        rsize = tuple(round(s, 4) for s in size)

        if shape == "Mesh":
            key = rec.get("mesh")
            raw = self.raw_mesh(key) if key else ValueError("no mesh id")
            if isinstance(raw, Exception) and rec.get("meshKind") == "Union":
                self.note("%s: union mesh unavailable (%s) - exported as a UNION_ box" % (rec.get("fullName", name), raw))
                shape = "Union"
            elif isinstance(raw, Exception):
                self.note("%s: mesh %s unavailable (%s) - exported as MISSING_ box"
                          % (rec.get("fullName", name), (rec.get("mesh") or "?"), raw))
                name, shape = "MISSING_" + name, "Block"
            elif rec.get("meshKind") == "Union":
                # read from the union in Studio: already in the part's space at its real size (no stretching)
                gkey = ("U", key, desc["sig"])
                make = lambda: geometry.from_mesh(raw, special={"scale": [1, 1, 1], "offset": [0, 0, 0]}, flip_v=self.o["flip_v"])
            elif rec.get("meshKind") == "SpecialMesh":
                gkey = ("S", key, json.dumps(special), desc["sig"])
                make = lambda: geometry.from_mesh(raw, special=special, flip_v=self.o["flip_v"])
            else:
                ds = bool(rec.get("doubleSided"))
                gkey = ("M", key, rsize, desc["sig"], ds)
                if ds:
                    make = lambda: geometry.double_sided(geometry.from_mesh(raw, size=size, flip_v=self.o["flip_v"]))
                else:
                    make = lambda: geometry.from_mesh(raw, size=size, flip_v=self.o["flip_v"])
        if shape == "Union":
            name, shape = "UNION_" + name, "Block"
        if shape != "Mesh":
            sp = special if (special and special.get("type") != "FileMesh") else None
            gkey = ("P", shape, rsize, json.dumps(sp), desc["sig"])
            make = lambda: geometry.primitive(shape, size, sp)

        if desc.get("tile_studs"):
            base_make, studs = make, desc["tile_studs"]
            make = lambda: geometry.box_uv(base_make(), studs)
        me = self.get_mesh(gkey, name, make, desc)
        obj = bpy.data.objects.new(_clean_name(name), me)
        coll.objects.link(obj)
        obj.parent = parent
        # hierarchy empties all sit at the identity, so world == local
        obj.matrix_world = cf_matrix(rec["cf"], self.o["stud_scale"])
        obj["rbx_class"] = rec.get("class", "")
        obj["rbx_path"] = rec.get("fullName", "")
        obj["rbx_material"] = rec.get("material", "")
        if rec.get("castShadow") is False:
            obj["rbx_no_shadow"] = 1
        if me.name not in self.mesh_info:
            self.mesh_info[me.name] = (obj, desc)

        for k, d in enumerate(rec.get("decals") or []):
            ddesc = self.res.decal(d, name)
            if not ddesc:
                continue
            lift = 0.004 + 0.002 * k
            # boxes: an exact quad; any other shape: projected onto the part's own surface like Roblox
            if shape == "Block":
                dkey = ("D", json.dumps(d, sort_keys=True), rsize, ddesc["sig"], lift)
                dmake = lambda d=d, lift=lift: geometry.decal_plane(size, d, lift)[0]
            else:
                dkey = ("DP", gkey, json.dumps(d, sort_keys=True), ddesc["sig"], lift)
                def dmake(d=d, lift=lift):
                    g = geometry.decal_projected(make(), size, d, lift)
                    return g if g is not None else geometry.decal_plane(size, d, lift)[0]
            dme = self.get_mesh(dkey, "%s_%s_%s" % (d.get("kind", "Decal"), d.get("face", ""), name),
                                dmake, ddesc)
            dobj = bpy.data.objects.new(dme.name, dme)
            coll.objects.link(dobj)
            dobj.parent = obj
            dobj.matrix_parent_inverse = Matrix.Identity(4)
            dobj.matrix_basis = Matrix.Identity(4)
            if dme.name not in self.mesh_info:
                self.mesh_info[dme.name] = (dobj, ddesc)
        return obj

    # --------------------------------------------------------------- UVs
    def uv_pass(self):
        items = list(self.mesh_info.items())
        baked = 0
        for i, (mname, (obj, desc)) in enumerate(items):
            me = obj.data
            if i % 10 == 0:
                self.progress("UVs %d/%d" % (i + 1, len(items)))
            try:
                uvtools.make_lightmap(obj, self.o["lightmap_margin"], self.notes)
                if me.uv_layers.get(uvtools.UV0) is None:
                    uvtools.copy_layer(me, uvtools.UV1, uvtools.UV0)
                elif not uvtools.is_clean(me, uvtools.UV0):
                    textured = any(desc.get(k) for k in ("base_image", "normal_image", "rough_image", "metal_image", "emission_image"))
                    tiled = uvtools.uv_stats(me, uvtools.UV0)["outside"]
                    if not textured:
                        uvtools.copy_layer(me, uvtools.UV1, uvtools.UV0)
                    elif tiled and not self.o["rebake_tiled"]:
                        # repeating texture (MaterialVariant / Texture object): keep tiling UVs,
                        # Unity repeats them natively; lightmap UV2 stays unique
                        me["rbx_tiled"] = True
                    elif self.o["rebake"]:
                        self.progress("rebaking %s (%d/%d)" % (me.name, i + 1, len(items)))
                        desc = materials.materialize_tint(desc, os.path.join(self.dir, "baked"))
                        new = uvtools.rebake(obj, desc, os.path.join(self.dir, "baked"),
                                             self.o["bake_samples"], 8, self.o["max_bake_res"])
                        new["sig"] = "rb_" + me.name
                        mat = materials.make_material(new, bpy.data.materials.new(new["name"]))
                        me.materials[0] = mat
                        self.mesh_info[mname] = (obj, new)
                        baked += 1
                    else:
                        self.note("%s: texture UVs overlap/tile and rebake is off" % me.name)
                uvtools.order_layers(me)
            except Exception as e:
                traceback.print_exc()
                self.note("%s: UV step failed: %s" % (me.name, e))
        return baked

    # ------------------------------------------------------------ lights
    def lights(self, lights, coll, root):
        """Roblox PointLight/SpotLight/SurfaceLight -> Blender lights (preview) carrying the
        original values in rbx_light, which the Unity export turns into Unity Lights."""
        S = self.o["stud_scale"]
        made = 0
        by_path = {}
        for o in coll.objects:
            pth = o.get("rbx_path")
            if pth and pth not in by_path:
                by_path[pth] = o.name
        for i, L in enumerate(lights):
            owner = L.get("owner")
            if not owner and L.get("path"):  # older exports: parent (part, or attachment's part)
                parent = L["path"].rsplit(".", 1)[0]
                owner = parent if parent in by_path else parent.rsplit(".", 1)[0]
            if owner in by_path:
                oo = bpy.data.objects.get(by_path[owner])
                small = False
                if oo is not None:
                    dims = [d / S for d in oo.dimensions]
                    # only bulbs/shades: a ceiling or wall holding a light must keep blocking the sun
                    small = (oo.get("rbx_material") in ("Neon", "Glass", "ForceField")) or max(dims or [0]) <= 3.0
                L = dict(L, ownerObject=by_path[owner] if small else "")
            kind = L.get("kind", "PointLight")
            spot = kind in ("SpotLight", "SurfaceLight")
            ld = bpy.data.lights.new("RBXLight_%d" % i, "SPOT" if spot else "POINT")
            c = L.get("color") or (1, 1, 1)
            ld.color = tuple(materials.srgb_to_linear(c))
            # rough preview energy; Unity uses the original Roblox values
            ld.energy = max(0.0, float(L.get("brightness", 1))) * 40.0 * (float(L.get("range", 8)) * S) ** 2
            ld.use_shadow = bool(L.get("shadows"))
            if hasattr(ld, "use_custom_distance"):
                ld.use_custom_distance = True
                ld.cutoff_distance = float(L.get("range", 8)) * S
            if spot:
                ld.spot_size = math.radians(max(1.0, min(179.0, float(L.get("angle", 90)))))
                ld.spot_blend = 0.15
            ob = bpy.data.objects.new("RBXLight_%d" % i, ld)
            coll.objects.link(ob)
            ob.parent = root
            px, py, pz = L.get("position") or (0, 0, 0)
            dx, dy, dz = L.get("direction") or (0, -1, 0)
            d = Vector((dx, -dz, dy))
            if d.length < 1e-6:
                d = Vector((0, 0, -1))
            ob.matrix_world = Matrix.Translation((px * S, -pz * S, py * S)) @ d.to_track_quat("-Z", "Y").to_matrix().to_4x4()
            ob.hide_render = not L.get("enabled", True)
            ob["rbx_light"] = json.dumps(L)
            made += 1
        if made:
            self.note("%d light(s) created" % made)
        return made

    # -------------------------------------------------------------- main
    def run(self):
        t0 = time.time()
        with open(os.path.join(self.dir, "scene.json"), "rb") as f:
            scene = json.loads(f.read().decode("utf-8"))
        self.notes.extend(scene.get("warnings") or [])
        for k, info in (scene.get("meshes") or {}).items():
            if info.get("ok") is False and not os.path.exists(os.path.join(self.dir, "meshes", k + ".rbm")):
                self.note("mesh %s: Studio couldn't read it (%s)" % (info.get("uri"), (info.get("err") or "")[:160]))
        for rec in scene.get("parts") or []:
            bv = builtin_mats.variant_for(rec, scene, self.dir)
            if bv:
                rec["variant"] = bv
        self.res = materials.Resolver(self.dir, self.notes)
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        name = _clean_name(scene.get("name") or "Roblox")
        coll = bpy.data.collections.new("RBX_" + name)
        bpy.context.scene.collection.children.link(coll)
        root = self.empty(name, coll)
        root["rbx_session"] = self.dir
        root["rbx_root"] = True

        empties = {}
        for a in scene.get("ancestors") or []:
            empties[a["id"]] = self.empty(a["name"], coll, empties.get(a.get("parent"), root))

        parts = scene.get("parts") or []
        made = 0
        for i, rec in enumerate(parts):
            if i % 50 == 0:
                self.progress("building parts %d/%d" % (i + 1, len(parts)))
            try:
                self.part(rec, coll, empties.get(rec.get("parent"), root), i)
                made += 1
            except Exception as e:
                traceback.print_exc()
                self.note("%s: FAILED (%s)" % (rec.get("fullName", rec.get("name")), e))

        nl = self.lights(scene.get("lights") or [], coll, root)
        root["rbx_lighting"] = json.dumps(scene.get("lighting") or {})
        root["rbx_stud_scale"] = self.o["stud_scale"]
        baked = self.uv_pass()

        for o in bpy.context.view_layer.objects.selected:
            o.select_set(False)
        root.select_set(True)
        bpy.context.view_layer.objects.active = root

        summary = ("%d/%d parts, %d meshes, %d materials, %d rebaked, %.1fs"
                   % (made, len(parts), len(self.meshes), len(self.mats), baked, time.time() - t0))
        with open(os.path.join(self.dir, "report.txt"), "w", encoding="utf-8") as f:
            f.write(summary + "\n\n" + "\n".join(self.notes) + "\n")
        return {"summary": summary, "notes": self.notes, "root": root.name}


def build(session_dir, opts=None, progress=print):
    return Builder(session_dir, opts, progress).run()
