bl_info = {
    "name": "Roblox Bridge -> Unity",
    "author": "obaba-samich + Claude",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Roblox",
    "description": "Validate and export models imported by Roblox Bridge as an FBX (+ textures, lights, materials) for the Unity package",
    "category": "Import-Export",
}

import os

import bpy
from bpy.props import BoolProperty, StringProperty

from . import _base, unity_export

_last = {"summary": "", "notes": []}


def _set_last(summary, notes):
    _last["summary"], _last["notes"] = summary, list(notes)
    for win in bpy.context.window_manager.windows if bpy.context.window_manager else []:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def export_root():
    # the same export folder as part 1, so a model's Studio export and its Unity export sit together
    addon = bpy.context.preferences.addons.get(_base.base.__name__)
    root = addon.preferences.export_root if addon else "~/RobloxExports"
    return bpy.path.abspath(os.path.expanduser(root))


class RBX_OT_validate(bpy.types.Operator):
    bl_idname = "rbx.validate"
    bl_label = "Validate for Unity"
    bl_description = "Check the selection (or scene) for anything that would break a Unity import"

    use_selection: BoolProperty(name="Selection only", default=True)

    def execute(self, context):
        objs = unity_export._collect(self.use_selection)
        errors, warnings = unity_export.validate(objs)
        _set_last("Validation: %d error(s), %d warning(s)" % (len(errors), len(warnings)),
                  ["ERROR: " + e for e in errors] + ["warning: " + w for w in warnings] or ["All good ✔"])
        self.report({"ERROR" if errors else "INFO"}, _last["summary"])
        return {"FINISHED"}


class RBX_OT_export_unity(bpy.types.Operator):
    bl_idname = "rbx.export_unity"
    bl_label = "Export FBX for Unity"
    bl_description = "Validate then export an FBX (+ sidecar + textures) ready for Unity"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})
    use_selection: BoolProperty(name="Selection only (+ children)", default=True)
    force: BoolProperty(name="Export even if validation finds errors", default=False)

    def invoke(self, context, event):
        root = context.active_object
        while root is not None and root.parent is not None and not root.get("rbx_root"):
            root = root.parent
        if root is None or not root.get("rbx_root"):
            roots = [o for o in context.view_layer.objects if o.get("rbx_root")]
            root = roots[-1] if roots else None
        name = bpy.path.clean_name(root.name if root else "RobloxExport")
        # one folder per model: this is the folder you open in Unity's Roblox Bridge window
        self.filepath = os.path.join(export_root(), "Unity", name, name + ".fbx")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        fp = bpy.path.ensure_ext(self.filepath, ".fbx")
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        ok, msgs = unity_export.export(fp, self.use_selection, self.force)
        _set_last(msgs[0] if ok else "Export blocked by validation errors", msgs)
        self.report({"INFO" if ok else "ERROR"}, _last["summary"])
        return {"FINISHED"} if ok else {"CANCELLED"}


class RBX_PT_unity(bpy.types.Panel):
    bl_label = "Export to Unity"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"

    def draw(self, context):
        l = self.layout
        l.operator("rbx.validate", icon="CHECKBOX_HLT")
        l.operator("rbx.export_unity", icon="EXPORT")
        if _last["summary"]:
            box = l.box()
            box.label(text=_last["summary"])
            for n in _last["notes"][:40]:
                box.label(text=n[:120])
            if len(_last["notes"]) > 40:
                box.label(text="… %d more (see report.txt in the export folder)" % (len(_last["notes"]) - 40))


classes = (RBX_OT_validate, RBX_OT_export_unity, RBX_PT_unity)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
