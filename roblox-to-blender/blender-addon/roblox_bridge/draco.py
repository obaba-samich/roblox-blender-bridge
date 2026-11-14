"""Decode Draco streams (Roblox .mesh v7 COREMESH v2) with the Draco bridge
library that ships inside Blender's own glTF add-on (no extra installs).

Roblox attribute ids (FileMesh spec): 0 position (f32 x3), 1 normal (f32 x3),
2 uv (f32 x2), 3 tangent (u8 x4), 4 color (u8 x4).
"""

import ctypes
import glob
import os
import sys
from array import array

_FLOAT = 5126
_UINT = 5125
_dll = None


def _find():
    names = {"win32": "bf_intern_draco_bridge.dll", "darwin": "libbf_intern_draco_bridge.dylib"}
    name = names.get(sys.platform, "libbf_intern_draco_bridge.so")
    roots = []
    try:
        import bpy
        for kind in ("SYSTEM_LIBS",):
            try:
                p = bpy.utils.resource_path(kind)
                if p:
                    roots.append(os.path.join(p, "scripts", "addons_core", "io_scene_gltf2"))
            except Exception:
                pass
        roots.append(os.path.join(bpy.utils.system_resource("SCRIPTS"), "addons_core", "io_scene_gltf2"))
        roots.append(os.path.join(bpy.utils.system_resource("SCRIPTS"), "addons", "io_scene_gltf2"))
    except Exception:
        pass
    mod = sys.modules.get("io_scene_gltf2")
    if mod is not None and getattr(mod, "__file__", None):
        roots.append(os.path.dirname(mod.__file__))
    for r in roots:
        p = os.path.join(r, name)
        if os.path.exists(p):
            return p
    for r in roots:
        hits = glob.glob(os.path.join(os.path.dirname(r), "**", name), recursive=True)
        if hits:
            return hits[0]
    return None


def _preload_draco_core():
    """The bridge links against libdraco, which official Blender keeps in <blender>/lib
    (not on the loader path). Load it globally first so the bridge resolves."""
    try:
        import bpy
        base = os.path.dirname(bpy.app.binary_path)
    except Exception:
        return
    for pat in ("lib/libdraco.so*", "lib/libdraco*.dylib", "draco*.dll", "lib/draco*.dll"):
        for p in sorted(glob.glob(os.path.join(base, pat)), key=len):
            try:
                ctypes.CDLL(p, mode=getattr(ctypes, "RTLD_GLOBAL", 0))
                return
            except OSError:
                continue


def library():
    global _dll
    if _dll is not None:
        return _dll
    path = _find()
    if not path:
        raise RuntimeError("Blender's Draco library was not found (needed for Roblox mesh v7)")
    _preload_draco_core()
    d = ctypes.cdll.LoadLibrary(path)
    sig = {
        "decoderCreate": (ctypes.c_void_p, []),
        "decoderRelease": (None, [ctypes.c_void_p]),
        "decoderDecode": (ctypes.c_bool, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]),
        "decoderReadAttribute": (ctypes.c_bool, [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_char_p]),
        "decoderGetVertexCount": (ctypes.c_uint32, [ctypes.c_void_p]),
        "decoderGetIndexCount": (ctypes.c_uint32, [ctypes.c_void_p]),
        "decoderGetAttributeByteLength": (ctypes.c_size_t, [ctypes.c_void_p, ctypes.c_uint32]),
        "decoderCopyAttribute": (None, [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]),
        "decoderReadIndices": (ctypes.c_bool, [ctypes.c_void_p, ctypes.c_size_t]),
        "decoderGetIndicesByteLength": (ctypes.c_size_t, [ctypes.c_void_p]),
        "decoderCopyIndices": (None, [ctypes.c_void_p, ctypes.c_void_p]),
    }
    for fn, (res, args) in sig.items():
        f = getattr(d, fn)
        f.restype = res
        f.argtypes = args
    _dll = d
    return d


def _attr(d, dec, uid, gl_type):
    if not d.decoderReadAttribute(dec, uid, _FLOAT, gl_type):
        return None
    n = d.decoderGetAttributeByteLength(dec, uid)
    buf = (ctypes.c_float * (n // 4))()
    d.decoderCopyAttribute(dec, uid, buf)
    return array("f", buf)


def decode(stream):
    """-> (positions, normals|None, uvs|None, indices) as flat arrays."""
    d = library()
    dec = d.decoderCreate()
    try:
        raw = ctypes.create_string_buffer(bytes(stream), len(stream))
        if not d.decoderDecode(dec, raw, len(stream)):
            raise ValueError("Draco stream could not be decoded")
        if not d.decoderReadIndices(dec, _UINT):
            raise ValueError("Draco stream has no triangle indices")
        n = d.decoderGetIndicesByteLength(dec)
        ib = (ctypes.c_uint32 * (n // 4))()
        d.decoderCopyIndices(dec, ib)
        indices = array("I", ib)
        pos = _attr(d, dec, 0, b"VEC3")
        if pos is None:
            raise ValueError("Draco stream has no position attribute (id 0)")
        nrm = _attr(d, dec, 1, b"VEC3")
        uv = _attr(d, dec, 2, b"VEC2")
        nv = len(pos) // 3
        if nrm is not None and len(nrm) != nv * 3:
            nrm = None
        if uv is not None and len(uv) != nv * 2:
            uv = None
        return pos, nrm, uv, indices
    finally:
        d.decoderRelease(dec)
