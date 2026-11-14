"""Geometry in ROBLOX part-local space (studs). Pure python, no bpy.

A Geo is a dict:
    P  : list[(x, y, z)]          vertex positions
    T  : list[(a, b, c)]          triangles (counter-clockwise = front, right-handed)
    UV : list[(u, v)] | None      per corner, BLENDER convention (v=0 bottom)
    N  : list[(x, y, z)] | None   per corner normals
Conversion to Blender happens in builder.to_blender().
"""

import math

EPS_AREA = 1e-14


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    l = math.sqrt(_dot(a, a))
    return (a[0] / l, a[1] / l, a[2] / l) if l > 1e-20 else (0.0, 0.0, 0.0)


# ------------------------------------------------------------------ meshes
def from_mesh(m, size=None, special=None, flip_v=True):
    """Roblox mesh dict (meshio) -> Geo scaled like the engine does.

    MeshPart  : the mesh's bounding box is stretched to fill part.Size, centred.
    SpecialMesh (FileMesh): vertices * Scale + Offset (part size is ignored).
    """
    pos, cor = m["positions"], m["corners"]
    nv = len(pos) // 3
    P = [(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]) for i in range(nv)]
    T = [(cor[i], cor[i + 1], cor[i + 2]) for i in range(0, len(cor), 3)]

    if special is not None:
        s, o = special["scale"], special["offset"]
        scl = (s[0], s[1], s[2])
        ctr = (0.0, 0.0, 0.0)
        off = (o[0], o[1], o[2])
    else:
        used = set(cor)
        if not used:
            raise ValueError("mesh has no faces")
        xs = [P[i][0] for i in used]; ys = [P[i][1] for i in used]; zs = [P[i][2] for i in used]
        lo = (min(xs), min(ys), min(zs)); hi = (max(xs), max(ys), max(zs))
        ctr = tuple((lo[k] + hi[k]) * 0.5 for k in range(3))
        scl = []
        for k in range(3):
            ext = hi[k] - lo[k]
            scl.append(size[k] / ext if ext > 1e-6 else 1.0)
        scl = tuple(scl)
        off = (0.0, 0.0, 0.0)

    P = [((p[0] - ctr[0]) * scl[0] + off[0], (p[1] - ctr[1]) * scl[1] + off[1], (p[2] - ctr[2]) * scl[2] + off[2]) for p in P]

    UV = None
    if m["has_uv"]:
        uv = m["uvs"]
        if flip_v:
            UV = [(uv[2 * i], 1.0 - uv[2 * i + 1]) for i in range(len(uv) // 2)]
        else:
            UV = [(uv[2 * i], uv[2 * i + 1]) for i in range(len(uv) // 2)]

    N = None
    if m["has_normal"]:
        nr = m["normals"]
        # normals transform with the inverse-transpose of the (diagonal) scale
        inv = tuple(1.0 / s if abs(s) > 1e-12 else 0.0 for s in scl)
        N = [_norm((nr[3 * i] * inv[0], nr[3 * i + 1] * inv[1], nr[3 * i + 2] * inv[2])) for i in range(len(nr) // 3)]
        if all(n == (0.0, 0.0, 0.0) for n in N[:64]) and all(n == (0.0, 0.0, 0.0) for n in N):
            N = None
    # mirrored scale flips handedness -> flip winding to keep faces outward
    if scl[0] * scl[1] * scl[2] < 0:
        T = [(c, b, a) for (a, b, c) in T]
        if UV is not None:
            UV = [x for i in range(0, len(UV), 3) for x in (UV[i + 2], UV[i + 1], UV[i])]
        if N is not None:
            N = [x for i in range(0, len(N), 3) for x in (N[i + 2], N[i + 1], N[i])]
    return {"P": P, "T": T, "UV": UV, "N": N}


def weld(g, digits=6):
    """Merge vertices at identical positions. UVs and normals are per corner, so
    shading and texture seams are unchanged; topology just becomes connected
    (better lightmap islands, smaller files)."""
    remap, P = {}, []
    idx = []
    for p in g["P"]:
        k = (round(p[0], digits), round(p[1], digits), round(p[2], digits))
        i = remap.get(k)
        if i is None:
            i = remap[k] = len(P)
            P.append(p)
        idx.append(i)
    g = dict(g)
    g["P"] = P
    g["T"] = [(idx[a], idx[b], idx[c]) for (a, b, c) in g["T"]]
    return g


def clean(g):
    """Weld, drop degenerate tris, make duplicate faces (double-sided geometry)
    valid, and fix inverted winding using the authored normals.
    Returns (geo, notes)."""
    g = weld(g)
    P, T, UV, N = g["P"], g["T"], g["UV"], g["N"]
    notes = []
    keepT, keepUV, keepN = [], [] if UV is not None else None, [] if N is not None else None
    seen = set()
    P = list(P)
    dropped = dup = 0
    for f, (a, b, c) in enumerate(T):
        if a == b or b == c or a == c:
            dropped += 1
            continue
        cr = _cross(_sub(P[b], P[a]), _sub(P[c], P[a]))
        if _dot(cr, cr) < EPS_AREA:
            dropped += 1
            continue
        key = tuple(sorted((a, b, c)))
        if key in seen:
            # same three vertices used twice (e.g. back face): give it its own vertices
            base = len(P)
            P.extend((P[a], P[b], P[c]))
            a, b, c = base, base + 1, base + 2
            dup += 1
        seen.add(key)
        keepT.append((a, b, c))
        if UV is not None:
            keepUV.extend(UV[3 * f:3 * f + 3])
        if N is not None:
            keepN.extend(N[3 * f:3 * f + 3])
    if dropped:
        notes.append("%d degenerate triangle(s) removed" % dropped)
    if dup:
        notes.append("%d double-sided triangle(s) split" % dup)

    # winding vs authored normals
    if N is not None and keepT:
        agree = 0.0
        for f, (a, b, c) in enumerate(keepT):
            fn = _cross(_sub(P[b], P[a]), _sub(P[c], P[a]))
            n = keepN[3 * f]; n2 = keepN[3 * f + 1]; n3 = keepN[3 * f + 2]
            s = (n[0] + n2[0] + n3[0], n[1] + n2[1] + n3[1], n[2] + n2[2] + n3[2])
            agree += 1.0 if _dot(fn, s) >= 0 else -1.0
        if agree < 0:
            keepT = [(c, b, a) for (a, b, c) in keepT]
            if keepUV is not None:
                keepUV = [x for i in range(0, len(keepUV), 3) for x in (keepUV[i + 2], keepUV[i + 1], keepUV[i])]
            keepN = [x for i in range(0, len(keepN), 3) for x in (keepN[i + 2], keepN[i + 1], keepN[i])]
            notes.append("winding flipped to match authored normals")
    return {"P": P, "T": keepT, "UV": keepUV, "N": keepN}, notes


# --------------------------------------------------------------- primitives
def _convex(polys, smooth_normals=None):
    """polys: list of lists of points (any winding). Orients each polygon outward
    from the centroid (all Roblox primitives are convex) and fan-triangulates."""
    pts = [p for poly in polys for p in poly]
    cen = tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))
    P, T, N = [], [], []
    for pi, poly in enumerate(polys):
        fn = (0.0, 0.0, 0.0)
        for i in range(1, len(poly) - 1):
            c = _cross(_sub(poly[i], poly[0]), _sub(poly[i + 1], poly[0]))
            fn = (fn[0] + c[0], fn[1] + c[1], fn[2] + c[2])
        pc = tuple(sum(p[k] for p in poly) / len(poly) for k in range(3))
        if _dot(fn, _sub(pc, cen)) < 0:
            poly = poly[::-1]
            fn = (-fn[0], -fn[1], -fn[2])
        fn = _norm(fn)
        base = len(P)
        P.extend(poly)
        for i in range(1, len(poly) - 1):
            T.append((base, base + i, base + i + 1))
            for vi in (0, i, i + 1):
                N.append(smooth_normals(poly[vi], pi) if smooth_normals else fn)
    return {"P": P, "T": T, "UV": None, "N": N}


def block(size):
    hx, hy, hz = size[0] / 2, size[1] / 2, size[2] / 2
    v = [(x, y, z) for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)]
    def q(*idx):
        return [v[i] for i in idx]
    return _convex([q(0, 1, 3, 2), q(4, 5, 7, 6), q(0, 1, 5, 4), q(2, 3, 7, 6), q(0, 2, 6, 4), q(1, 3, 7, 5)])


def wedge(size):
    # slope faces -Z (front) and rises toward +Z (back); vertical face at back
    hx, hy, hz = size[0] / 2, size[1] / 2, size[2] / 2
    b0, b1, b2, b3 = (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)
    t2, t3 = (hx, hy, hz), (-hx, hy, hz)
    return _convex([[b0, b1, b2, b3], [b3, b2, t2, t3], [b0, b1, t2, t3], [b1, b2, t2], [b0, b3, t3]])


def corner_wedge(size):
    hx, hy, hz = size[0] / 2, size[1] / 2, size[2] / 2
    b0, b1, b2, b3 = (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)
    apex = (hx, hy, -hz)
    return _convex([[b0, b1, b2, b3], [b1, b2, apex], [b0, b1, apex], [b2, b3, apex], [b3, b0, apex]])


def ball(size, seg=48, rings=24):
    rx, ry, rz = size[0] / 2, size[1] / 2, size[2] / 2
    P, T, N = [], [], []
    def pt(i, j):
        th = math.pi * i / rings
        ph = 2 * math.pi * j / seg
        d = (math.sin(th) * math.cos(ph), math.cos(th), math.sin(th) * math.sin(ph))
        return (d[0] * rx, d[1] * ry, d[2] * rz), _norm((d[0] / rx, d[1] / ry, d[2] / rz))
    for i in range(rings):
        for j in range(seg):
            quad = [pt(i, j), pt(i + 1, j), pt(i + 1, j + 1), pt(i, j + 1)]
            # pole triangles collapse to zero area; clean() removes them
            for t in ((0, 1, 2), (0, 2, 3)):
                base = len(P)
                for k in t:
                    P.append(quad[k][0]); N.append(quad[k][1])
                T.append((base, base + 1, base + 2))
    g = {"P": P, "T": T, "UV": None, "N": N}
    return _outward(g)


def cylinder(size, seg=48):
    # Part.Shape = Cylinder: axis along X, diameter = min(Y, Z)
    r = min(size[1], size[2]) / 2
    hx = size[0] / 2
    ring = [(math.cos(2 * math.pi * j / seg), math.sin(2 * math.pi * j / seg)) for j in range(seg)]
    polys = [[(-hx, r * c, r * s) for c, s in ring], [(hx, r * c, r * s) for c, s in ring]]
    caps = _convex(polys)
    P, T, N = caps["P"], caps["T"], caps["N"]
    for j in range(seg):
        c0, s0 = ring[j]; c1, s1 = ring[(j + 1) % seg]
        quad = [(-hx, r * c0, r * s0), (hx, r * c0, r * s0), (hx, r * c1, r * s1), (-hx, r * c1, r * s1)]
        nq = [(0, c0, s0), (0, c0, s0), (0, c1, s1), (0, c1, s1)]
        for t in ((0, 1, 2), (0, 2, 3)):
            base = len(P)
            for k in t:
                P.append(quad[k]); N.append(nq[k])
            T.append((base, base + 1, base + 2))
    return _outward({"P": P, "T": T, "UV": None, "N": N})


def _outward(g):
    """Make each triangle's winding agree with its per-corner normals."""
    P, T, N = g["P"], g["T"], g["N"]
    for f, (a, b, c) in enumerate(T):
        fn = _cross(_sub(P[b], P[a]), _sub(P[c], P[a]))
        if _dot(fn, N[3 * f]) < 0:
            T[f] = (a, c, b)
            N[3 * f + 1], N[3 * f + 2] = N[3 * f + 2], N[3 * f + 1]
    return g


def primitive(shape, size, special=None):
    """Roblox shape name -> Geo. SpecialMesh primitive types multiply size by Scale."""
    if special is not None:
        s = special["scale"]
        size = (size[0] * s[0], size[1] * s[1], size[2] * s[2])
        t = special["type"]
        shape = {"Brick": "Block", "Sphere": "Ball", "Cylinder": "CylinderY", "Wedge": "Wedge"}.get(t, "Block")
    if shape == "Ball":
        g = ball(size)
    elif shape == "Cylinder":
        g = cylinder(size)
    elif shape == "CylinderY":  # SpecialMesh cylinder stands upright (Y axis)
        g = cylinder((size[1], size[0], size[2]))
        g["P"] = [(y, x, z) for (x, y, z) in g["P"]]
        g["N"] = [(y, x, z) for (x, y, z) in g["N"]]
        g["T"] = [(a, c, b) for (a, b, c) in g["T"]]  # axis swap mirrors -> fix winding
        g["N"] = [x for i in range(0, len(g["N"]), 3) for x in (g["N"][i], g["N"][i + 2], g["N"][i + 1])]
    elif shape == "Wedge":
        g = wedge(size)
    elif shape == "CornerWedge":
        g = corner_wedge(size)
    else:
        g = block(size)
    if special is not None:
        o = special["offset"]
        g["P"] = [(p[0] + o[0], p[1] + o[1], p[2] + o[2]) for p in g["P"]]
    return g


# ------------------------------------------------------------------- decals
# Roblox face -> (outward normal, texture right, texture up) in part space.
FACES = {
    "Front":  ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
    "Back":   ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    "Right":  ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    "Left":   ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    "Top":    ((0, 1, 0), (1, 0, 0), (0, 0, -1)),
    "Bottom": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
}


def _decal_uv_transform(rv, d):
    """Decal.Rotation (degrees, clockwise), UVScale (multiply), UVOffset (add), applied in
    Roblox UV space (V down). Docs don't state pivot/order: rotate about the centre,
    then scale, then offset. Identity when the properties are at their defaults."""
    rot = float(d.get("rotation") or 0.0)
    sc = d.get("uvScale") or (1, 1)
    of = d.get("uvOffset") or (0, 0)
    if rot == 0 and tuple(sc) == (1, 1) and tuple(of) == (0, 0):
        return rv
    a = math.radians(rot)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for u, v in rv:
        x, y = u - 0.5, v - 0.5
        # clockwise on screen with V pointing down
        u2, v2 = 0.5 + x * ca - y * sa, 0.5 + x * sa + y * ca
        out.append((u2 * sc[0] + of[0], v2 * sc[1] + of[1]))
    return out


def box_uv(g, studs_per_tile):
    """Tiled material mapping like Roblox MaterialVariants: each triangle is projected
    along its dominant part-local axis, 1 UV unit = StudsPerTile studs."""
    k = 1.0 / max(1e-4, float(studs_per_tile))
    P, T = g["P"], g["T"]
    UV = []
    for (a, b, c) in T:
        n = _cross(_sub(P[b], P[a]), _sub(P[c], P[a]))
        ax = max(range(3), key=lambda i: abs(n[i]))
        sgn = 1.0 if n[ax] >= 0 else -1.0
        for v in (a, b, c):
            x, y, z = P[v]
            if ax == 0:
                UV.append((-z * sgn * k, y * k))
            elif ax == 1:
                UV.append((x * k, -z * sgn * k))
            else:
                UV.append((x * sgn * k, y * k))
    g = dict(g)
    g["UV"] = UV
    return g


def double_sided(g):
    """MeshPart.DoubleSided: add a reversed copy of every triangle (normals negated).
    clean() later gives the copies their own vertices so the mesh stays valid."""
    T = g["T"]
    g = dict(g)
    g["T"] = T + [(a, c, b) for (a, b, c) in T]
    if g["UV"] is not None:
        UV = g["UV"]
        g["UV"] = UV + [x for i in range(0, len(UV), 3) for x in (UV[i], UV[i + 2], UV[i + 1])]
    if g["N"] is not None:
        N = g["N"]
        neg = [(-n[0], -n[1], -n[2]) for n in N]
        g["N"] = N + [x for i in range(0, len(neg), 3) for x in (neg[i], neg[i + 2], neg[i + 1])]
    return g


def decal_plane(size, d, lift):
    """Quad covering the part's bounding-box face, lifted `lift` studs off it."""
    n, r, u = FACES.get(d["face"], FACES["Front"])
    half = [s / 2 for s in size]
    w = abs(_dot(r, size))
    h = abs(_dot(u, size))
    depth = abs(_dot(n, half))
    c = tuple(n[k] * (depth + lift) for k in range(3))
    def at(x, y):  # x,y in [-0.5, 0.5]
        return tuple(c[k] + r[k] * x * w + u[k] * y * h for k in range(3))
    corners = [at(-0.5, 0.5), at(-0.5, -0.5), at(0.5, -0.5), at(0.5, 0.5)]  # TL, BL, BR, TR
    if d.get("kind") == "Texture":
        su = d.get("studsU") or 2.0; sv = d.get("studsV") or 2.0
        ou = d.get("offsetU") or 0.0; ov = d.get("offsetV") or 0.0
        # Roblox: U grows to the right from the left edge, V grows downward from the top edge
        rv = [((0 + ou) / su, (0 + ov) / sv), ((0 + ou) / su, (h + ov) / sv), ((w + ou) / su, (h + ov) / sv), ((w + ou) / su, (0 + ov) / sv)]
    else:
        rv = [(0, 0), (0, 1), (1, 1), (1, 0)]
    rv = _decal_uv_transform(rv, d)
    uvq = [(a, 1.0 - b) for a, b in rv]  # -> Blender convention
    P = corners
    T = [(0, 1, 2), (0, 2, 3)]
    # make sure the quad faces outward
    fn = _cross(_sub(P[1], P[0]), _sub(P[2], P[0]))
    if _dot(fn, n) < 0:
        T = [(0, 2, 1), (0, 3, 2)]
    UV = [uvq[i] for t in T for i in t]
    N = [n] * 6
    return {"P": P, "T": T, "UV": UV, "N": N}, (w, h)


def decal_projected(part_geo, size, d, lift, min_facing=0.05):
    """Decal on a non-box part (MeshPart, wedge, cylinder...), like Roblox: the image is projected
    along the face's normal onto the part's own triangles that face that way, so it follows the
    real shape instead of covering the bounding-box square. None if no triangle faces that way."""
    n, r, u = FACES.get(d["face"], FACES["Front"])
    w = abs(_dot(r, size)) or 1e-4
    h = abs(_dot(u, size)) or 1e-4
    texture = d.get("kind") == "Texture"
    su = d.get("studsU") or 2.0; sv = d.get("studsV") or 2.0
    ou = d.get("offsetU") or 0.0; ov = d.get("offsetV") or 0.0
    P0, T0 = part_geo["P"], part_geo["T"]
    N0 = part_geo.get("N")
    per_vertex = N0 is not None and len(N0) == len(P0) and len(N0) != 3 * len(T0)
    per_corner = N0 is not None and len(N0) == 3 * len(T0)
    P, T, UV, N = [], [], [], []
    for f, (a, b, c) in enumerate(T0):
        fn = _norm(_cross(_sub(P0[b], P0[a]), _sub(P0[c], P0[a])))
        # trust the mesh's own normals for which way a triangle faces (winding can be either way)
        sn = None
        if per_vertex:
            sn = [N0[a], N0[b], N0[c]]
        elif per_corner:
            sn = N0[3 * f:3 * f + 3]
        if sn is not None:
            avg = tuple(sn[0][k] + sn[1][k] + sn[2][k] for k in range(3))
            if _dot(avg, fn) < 0:
                fn = (-fn[0], -fn[1], -fn[2])
                b, c = c, b
        if _dot(fn, n) <= min_facing:
            continue
        rv = []
        for v in (a, b, c):
            x = _dot(P0[v], r) / w            # -0.5 (left) .. 0.5 (right), as decal_plane
            y = _dot(P0[v], u) / h            # -0.5 (bottom) .. 0.5 (top)
            if texture:
                rv.append((((x + 0.5) * w + ou) / su, ((0.5 - y) * h + ov) / sv))
            else:
                rv.append((x + 0.5, 0.5 - y))
        rv = _decal_uv_transform(rv, d)
        i = len(P)
        for v in (a, b, c):
            P.append(tuple(P0[v][k] + n[k] * lift for k in range(3)))
            N.append(fn)
        T.append((i, i + 1, i + 2))
        UV.extend((p, 1.0 - q) for p, q in rv)   # -> Blender convention
    if not T:
        return None
    return {"P": P, "T": T, "UV": UV, "N": N}
