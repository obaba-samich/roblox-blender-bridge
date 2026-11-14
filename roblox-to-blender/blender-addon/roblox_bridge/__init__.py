bl_info = {
    "name": "Roblox Bridge (Studio -> Blender)",
    "author": "obaba-samich + Claude",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Roblox",
    "description": "Receive exact Roblox Studio exports (geometry, UVs, textures, lights). For Unity, also install Roblox Bridge -> Unity",
    "category": "Import-Export",
}

import os
import traceback

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty

from . import builder, fetch, server, uvtools

ADDON = __package__
bridge = server.Bridge()
_last = {"summary": "", "notes": []}


def prefs():
    return bpy.context.preferences.addons[ADDON].preferences


def build_opts():
    p = prefs()
    return {"stud_scale": p.stud_scale, "flip_v": p.flip_v, "rebake": p.rebake,
            "bake_samples": p.bake_samples, "rebake_tiled": p.rebake_tiled, "max_bake_res": p.max_bake_res,
            "lightmap_margin": p.lightmap_margin}


def _sync_bridge():
    p = prefs()
    bridge.export_root = bpy.path.abspath(os.path.expanduser(p.export_root))
    bridge.addon_version = ".".join(map(str, bl_info["version"]))
    bridge.blender_version = bpy.app.version_string
    bridge.creds = lambda: (prefs().cookie.strip(), prefs().api_key.strip())


def _redraw():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else []):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _append_report(session_dir, notes):
    if not notes:
        return
    try:
        with open(os.path.join(session_dir, "report.txt"), "a", encoding="utf-8") as f:
            f.write("\nDownloads:\n" + "\n".join(notes) + "\n")
    except OSError:
        pass


def _set_last(summary, notes):
    _last["summary"] = summary
    _last["notes"] = list(notes)
    _redraw()


# ----------------------------------------------------------------- timer
def _tick():
    try:
        sid, d = bridge.jobs.get_nowait()
    except Exception:
        return 0.25
    try:
        bridge.set_status(sid, "building", "building scene in Blender")
        rep = builder.build(d, build_opts(), lambda m: bridge.set_status(sid, message=m))
        prep = bridge.sessions.get(sid, {}).get("prep_notes") or []
        _append_report(d, prep)
        notes = [n for n in prep if "FAILED" in n] + rep["notes"]
        bridge.set_status(sid, "done", rep["summary"] + "  [" + d + "]", notes)
        _set_last(rep["summary"], notes)
    except Exception as e:
        traceback.print_exc()
        bridge.set_status(sid, "error", "build failed: %s" % e)
        _set_last("build failed: %s" % e, [])
    return 0.05


def start_server():
    _sync_bridge()
    if not bridge.running:
        os.makedirs(bridge.export_root, exist_ok=True)
        bridge.start(prefs().port)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.25, persistent=True)


# ----------------------------------------------------------------- prefs
class RBX_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON

    port: IntProperty(name="Port", default=34873, min=1024, max=65535)
    auto_start: BoolProperty(name="Start server when Blender opens", default=True)
    export_root: StringProperty(name="Export folder", default="~/RobloxExports", subtype="DIR_PATH")
    stud_scale: FloatProperty(name="Metres per stud", default=0.28, min=0.001, max=100, precision=3,
                              description="0.28 matches Roblox's avatar scale; 1.0 keeps 1 stud = 1 unit")
    flip_v: BoolProperty(name="Flip V (Roblox UV origin is top-left)", default=True)
    rebake: BoolProperty(name="Rebake textures onto non-overlapping UVs", default=True,
                         description="When a mesh's Roblox UVs overlap (mirrored) or tile, bake its textures onto a unique layout so UV0 never overlaps")
    rebake_tiled: BoolProperty(name="Also rebake tiling textures (MaterialVariants, Texture objects)", default=False,
                               description="Off: repeating textures keep tiling UVs (Unity repeats them; small files). On: bake every tiled surface into its own unique texture (very large files)")
    bake_samples: IntProperty(name="Bake samples", default=8, min=1, max=256)
    max_bake_res: IntProperty(name="Max bake resolution", default=4096, min=256, max=8192)
    lightmap_margin: FloatProperty(name="UV island margin", default=0.01, min=0.001, max=0.1)
    api_key: StringProperty(name="Open Cloud API key (optional)", subtype="PASSWORD",
                            description="Personal-account key with the legacy-asset:manage scope. Used only to download assets Studio refuses to open")
    cookie: StringProperty(name=".ROBLOSECURITY cookie (optional)", subtype="PASSWORD",
                           description="Alternative to the API key. Treat it like your password")

    def draw(self, context):
        l = self.layout
        l.prop(self, "port"); l.prop(self, "auto_start"); l.prop(self, "export_root")
        l.prop(self, "stud_scale"); l.prop(self, "flip_v")
        box = l.box(); box.label(text="UVs / baking")
        box.prop(self, "rebake"); box.prop(self, "rebake_tiled"); box.prop(self, "bake_samples"); box.prop(self, "max_bake_res"); box.prop(self, "lightmap_margin")
        box = l.box(); box.label(text="Downloading assets Studio can't read (not owned by you)")
        box.prop(self, "api_key"); box.prop(self, "cookie")
        box.operator("rbx.test_credentials", icon="CHECKMARK")
        box.label(text="API key: create.roblox.com > Open Cloud > API Keys, personal account, scope legacy-asset:manage")
        box.label(text="Stored only in Blender's preferences on this computer.", icon="LOCKED")


# ------------------------------------------------------------- operators
class RBX_OT_server(bpy.types.Operator):
    bl_idname = "rbx.server"
    bl_label = "Start/Stop Server"
    bl_description = "Start or stop the local receiver for the Roblox Studio plugin"

    def execute(self, context):
        try:
            if bridge.running:
                bridge.stop()
                self.report({"INFO"}, "Roblox Bridge server stopped")
            else:
                start_server()
                self.report({"INFO"}, "Listening on localhost:%d" % bridge.port)
        except OSError as e:
            self.report({"ERROR"}, "Could not open port %d: %s" % (prefs().port, e))
            return {"CANCELLED"}
        return {"FINISHED"}


class RBX_OT_test_credentials(bpy.types.Operator):
    bl_idname = "rbx.test_credentials"
    bl_label = "Test API key / cookie"
    bl_description = "Check that Roblox accepts the key or cookie for downloading assets"

    def execute(self, context):
        ok, msg = fetch.check_credentials(prefs().cookie.strip(), prefs().api_key.strip())
        self.report({"INFO" if ok else "ERROR"}, msg)
        _set_last("Credentials: " + ("OK ✔" if ok else "not working"), [msg])
        return {"FINISHED"}


class RBX_OT_import_folder(bpy.types.Operator):
    bl_idname = "rbx.import_folder"
    bl_label = "Import Saved Export"
    bl_description = "Rebuild a scene from a saved export folder (contains scene.json)"

    directory: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        _sync_bridge()
        self.directory = bridge.export_root + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        d = bpy.path.abspath(self.directory).rstrip("/\\")
        if not os.path.exists(os.path.join(d, "scene.json")):
            self.report({"ERROR"}, "No scene.json in %s" % d)
            return {"CANCELLED"}
        try:
            _sync_bridge()
            notes, _ = server.prepare_downloads(d, bridge.creds(), cache_dir=os.path.join(bridge.export_root, "_cache"))
            rep = builder.build(d, build_opts())
            _append_report(d, notes)
            _set_last(rep["summary"], [n for n in notes if "FAILED" in n] + rep["notes"])
            self.report({"INFO"}, rep["summary"])
        except Exception as e:
            traceback.print_exc()
            self.report({"ERROR"}, "Import failed: %s" % e)
            return {"CANCELLED"}
        return {"FINISHED"}


class RBX_OT_fix_uvs(bpy.types.Operator):
    bl_idname = "rbx.fix_uvs"
    bl_label = "Fix UVs on Selected"
    bl_description = "Generate non-overlapping UVMap + UVLightmap on selected meshes (untextured meshes only for UVMap)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        done, notes = set(), []
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        for o in objs:
            me = o.data
            if me.name in done:
                continue
            done.add(me.name)
            uvtools.make_lightmap(o, prefs().lightmap_margin, notes)
            if me.uv_layers.get(uvtools.UV0) is None or (not uvtools.is_clean(me, uvtools.UV0) and not any(
                    n.type == "TEX_IMAGE" for m in me.materials if m and m.use_nodes for n in m.node_tree.nodes)):
                uvtools.copy_layer(me, uvtools.UV1, uvtools.UV0)
            elif not uvtools.is_clean(me, uvtools.UV0):
                notes.append("%s: textured UVs overlap; re-import with rebake on to fix" % me.name)
            uvtools.order_layers(me)
        _set_last("Fixed UVs on %d mesh(es)" % len(done), notes)
        return {"FINISHED"}


# ------------------------------------------------------------------ panel
class RBX_PT_panel(bpy.types.Panel):
    bl_label = "Roblox Bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"

    def draw(self, context):
        l = self.layout
        col = l.column(align=True)
        if bridge.running:
            col.label(text="Listening on localhost:%d" % bridge.port, icon="CHECKMARK")
            col.operator("rbx.server", text="Stop Server", icon="PAUSE")
        else:
            col.label(text="Server stopped", icon="ERROR")
            col.operator("rbx.server", text="Start Server", icon="PLAY")
        l.separator()
        l.operator("rbx.import_folder", icon="FILE_FOLDER")
        l.operator("rbx.fix_uvs", icon="UV")
        if _last["summary"]:
            box = l.box()
            box.label(text=_last["summary"])
            for n in _last["notes"][:40]:
                box.label(text=n[:120])
            if len(_last["notes"]) > 40:
                box.label(text="… %d more (see report.txt in the export folder)" % (len(_last["notes"]) - 40))


classes = (RBX_Preferences, RBX_OT_server, RBX_OT_test_credentials, RBX_OT_import_folder, RBX_OT_fix_uvs, RBX_PT_panel)


def _autostart():
    try:
        if prefs().auto_start:
            start_server()
    except Exception as e:
        print("[Roblox Bridge] auto-start failed:", e)
    return None


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.app.timers.register(_autostart, first_interval=0.5, persistent=True)


def unregister():
    bridge.stop()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
