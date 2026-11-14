"""Builds a Roblox-style v7 .mesh (COREMESH v2 = Draco) using Blender's own Draco encoder."""
import ctypes
import struct
from array import array


def make_v7(positions, normals, uvs, indices):
    from roblox_bridge import draco
    draco._preload_draco_core()
    path = draco._find()
    d = ctypes.cdll.LoadLibrary(path)
    d.encoderCreate.restype = ctypes.c_void_p; d.encoderCreate.argtypes = [ctypes.c_uint32]
    d.encoderSetAttribute.restype = ctypes.c_uint32
    d.encoderSetAttribute.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_bool]
    d.encoderSetIndices.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_void_p]
    d.encoderSetCompressionLevel.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    d.encoderSetQuantizationBits.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32] * 5
    d.encoderEncode.restype = ctypes.c_bool; d.encoderEncode.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
    d.encoderGetByteLength.restype = ctypes.c_uint64; d.encoderGetByteLength.argtypes = [ctypes.c_void_p]
    d.encoderCopy.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    d.encoderRelease.argtypes = [ctypes.c_void_p]
    nv = len(positions) // 3
    enc = d.encoderCreate(nv)
    bufs = [(ctypes.c_float * len(a))(*a) for a in (positions, normals, uvs)]
    ids = [d.encoderSetAttribute(enc, n, 5126, t, b, False) for n, t, b in
           ((b"POSITION", b"VEC3", bufs[0]), (b"NORMAL", b"VEC3", bufs[1]), (b"TEXCOORD_0", b"VEC2", bufs[2]))]
    ib = (ctypes.c_uint32 * len(indices))(*indices)
    d.encoderSetIndices(enc, 5125, len(indices), ib)
    d.encoderSetCompressionLevel(enc, 7)
    d.encoderSetQuantizationBits(enc, 14, 10, 12, 8, 8)
    assert d.encoderEncode(enc, 1)
    n = d.encoderGetByteLength(enc)
    out = (ctypes.c_uint8 * n)()
    d.encoderCopy(enc, out)
    d.encoderRelease(enc)
    stream = bytes(out)
    core = struct.pack("<I", len(stream)) + stream
    return (b"version 7.00\n" + b"COREMESH" + struct.pack("<II", 2, len(core)) + core), ids
