"""Mesh containers.

Internal mesh dict (Roblox mesh space, Roblox UV convention, i.e. V=0 at top):
    positions : list[float]  3 per vertex
    corners   : list[int]    3 per triangle, vertex indices
    uvs       : list[float]  2 per corner
    normals   : list[float]  3 per corner
    has_uv, has_normal : bool

Two sources:
  * .rbm  - written by the Studio plugin from EditableMesh (exact engine data)
  * Roblox .mesh files v1.x - v6.x downloaded from assetdelivery (fallback)
"""

import struct
import sys
from array import array

_LE = sys.byteorder == "little"


def _arr(typecode, data, offset, count):
    a = array(typecode)
    size = a.itemsize * count
    a.frombytes(data[offset:offset + size])
    if len(a) != count:
        raise ValueError("mesh data truncated")
    if not _LE:
        a.byteswap()
    return a


def read_rbm(data):
    if data[:4] != b"RBM1":
        raise ValueError("not an RBM1 mesh blob")
    nv, nc = struct.unpack_from("<II", data, 4)
    has_uv, has_n = data[12], data[13]
    if nc % 3:
        raise ValueError("corner count not a multiple of 3")
    o = 16
    pos = _arr("f", data, o, nv * 3); o += nv * 12
    cor = _arr("I", data, o, nc); o += nc * 4
    uvs = _arr("f", data, o, nc * 2); o += nc * 8
    nrm = _arr("f", data, o, nc * 3); o += nc * 12
    if o != len(data):
        raise ValueError("RBM size mismatch")
    if nc and max(cor) >= nv:
        raise ValueError("vertex index out of range")
    return {"positions": pos, "corners": cor, "uvs": uvs, "normals": nrm,
            "has_uv": bool(has_uv), "has_normal": bool(has_n), "version": "rbm"}


def write_rbm(m):
    pos, cor, uvs, nrm = (array("f", m["positions"]), array("I", m["corners"]),
                          array("f", m["uvs"]), array("f", m["normals"]))
    if not _LE:
        for a in (pos, cor, uvs, nrm):
            a.byteswap()
    head = b"RBM1" + struct.pack("<IIBBH", len(pos) // 3, len(cor), int(m["has_uv"]), int(m["has_normal"]), 0)
    return head + pos.tobytes() + cor.tobytes() + uvs.tobytes() + nrm.tobytes()


# --------------------------------------------------------------- .mesh files
def _from_vertex_table(verts_pos, verts_nrm, verts_uv, faces, version):
    corners = array("I", faces)
    uvs = array("f")
    nrm = array("f")
    for v in corners:
        uvs.append(verts_uv[2 * v]); uvs.append(verts_uv[2 * v + 1])
        nrm.append(verts_nrm[3 * v]); nrm.append(verts_nrm[3 * v + 1]); nrm.append(verts_nrm[3 * v + 2])
    return {"positions": verts_pos, "corners": corners, "uvs": uvs, "normals": nrm,
            "has_uv": True, "has_normal": True, "version": version}


def _read_vertices(data, off, count, stride):
    pos, nrm, uv = array("f"), array("f"), array("f")
    for i in range(count):
        b = off + i * stride
        px, py, pz, nx, ny, nz, u, v = struct.unpack_from("<8f", data, b)
        pos.extend((px, py, pz)); nrm.extend((nx, ny, nz)); uv.extend((u, v))
    return pos, nrm, uv


def _lod0(faces, nfaces, lods):
    if len(lods) >= 2 and 0 <= lods[0] < lods[1] <= nfaces:
        return faces[lods[0] * 3:lods[1] * 3]
    return faces


def _parse_text_v1(text, version):
    lines = text.splitlines()
    nfaces = int(lines[1].strip())
    import re
    nums = [float(x) for x in re.findall(r"[-+0-9.eE]+", " ".join(lines[2:]))]
    scale = 0.5 if version == "1.00" else 1.0
    pos, nrm, uv, faces = array("f"), array("f"), array("f"), array("I")
    for i in range(nfaces * 3):
        b = i * 9
        px, py, pz, nx, ny, nz, u, v, _w = nums[b:b + 9]
        # FileMesh spec: v1 stores tex_V upside down relative to the binary formats
        pos.extend((px * scale, py * scale, pz * scale)); nrm.extend((nx, ny, nz)); uv.extend((u, 1.0 - v))
        faces.append(i)
    return _from_vertex_table(pos, nrm, uv, faces, version)


def parse_roblox_mesh(data):
    if not data.startswith(b"version "):
        raise ValueError("not a Roblox .mesh file")
    nl = data.index(b"\n")
    version = data[8:nl].decode("ascii", "replace").strip()
    major = version.split(".")[0]
    o = nl + 1

    if major == "1":
        return _parse_text_v1(data.decode("ascii", "replace"), version)

    if major == "2":
        hsize, vsize, fsize, nverts, nfaces = struct.unpack_from("<HBBII", data, o)
        o += hsize
        pos, nrm, uv = _read_vertices(data, o, nverts, vsize); o += nverts * vsize
        faces = _arr("I", data, o, nfaces * 3)
        return _from_vertex_table(pos, nrm, uv, faces, version)

    if major == "3":
        hsize, vsize, fsize, lsize, nlods, nverts, nfaces = struct.unpack_from("<HBBHHII", data, o)
        o += hsize
        pos, nrm, uv = _read_vertices(data, o, nverts, vsize); o += nverts * vsize
        faces = _arr("I", data, o, nfaces * 3); o += nfaces * 12
        lods = _arr("I", data, o, nlods) if nlods else array("I")
        return _from_vertex_table(pos, nrm, uv, _lod0(faces, nfaces, lods), version)

    if major in ("4", "5"):
        (hsize, _lodtype, nverts, nfaces, nlods, nbones, _bnsize, _nsub, _hq, _pad) = struct.unpack_from("<HHIIHHIHBB", data, o)
        o += hsize
        pos, nrm, uv = _read_vertices(data, o, nverts, 40); o += nverts * 40
        if nbones:
            o += nverts * 8  # skinning envelopes
        faces = _arr("I", data, o, nfaces * 3); o += nfaces * 12
        lods = _arr("I", data, o, nlods) if nlods else array("I")
        return _from_vertex_table(pos, nrm, uv, _lod0(faces, nfaces, lods), version)

    if major in ("6", "7"):
        core = None
        lods = array("I")
        while o + 16 <= len(data):
            ctype = data[o:o + 8].rstrip(b"\0").decode("ascii", "replace")
            cver, csize = struct.unpack_from("<II", data, o + 8)
            body = o + 16
            if ctype == "COREMESH" and cver == 2:
                # v7: Draco-compressed geometry, decoded with Blender's bundled Draco
                from . import draco
                size = struct.unpack_from("<I", data, body)[0]
                pos, nrm, uv, faces = draco.decode(data[body + 4:body + 4 + size])
                nv = len(pos) // 3
                if nrm is None:
                    nrm = array("f", [0.0]) * (nv * 3)
                if uv is None:
                    uv = array("f", [0.0]) * (nv * 2)
                core = (pos, nrm, uv, faces, len(faces) // 3)
            elif ctype == "COREMESH":
                if cver != 1:
                    raise ValueError("unknown COREMESH chunk version %d in mesh v%s" % (cver, version))
                nverts = struct.unpack_from("<I", data, body)[0]
                p = body + 4
                pos, nrm, uv = _read_vertices(data, p, nverts, 40); p += nverts * 40
                nfaces = struct.unpack_from("<I", data, p)[0]; p += 4
                faces = _arr("I", data, p, nfaces * 3)
                core = (pos, nrm, uv, faces, nfaces)
            elif ctype == "LODS" and cver == 1:
                _t, _hq, nofs = struct.unpack_from("<HBI", data, body)
                lods = _arr("I", data, body + 7, nofs)
            o = body + csize
        if core is None:
            raise ValueError("no COREMESH chunk in mesh v%s" % version)
        pos, nrm, uv, faces, nfaces = core
        return _from_vertex_table(pos, nrm, uv, _lod0(faces, nfaces, lods), version)

    raise ValueError("unsupported .mesh version %s" % version)
