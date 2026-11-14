"""Fits Roblox's tonemapper and light falloff from screenshots of tools/calibrate_lighting.lua.

    blender -b --factory-startup --python tools/fit_lighting.py -- shot1.png shot2.png ... [-o out.json]

Every screenshot is identified by the stage bits on screen, so order and extra shots don't matter.
Writes roblox_lighting_fit.json (tonemap curve, colour model, falloff, light scale) and prints a report.
Must mirror the chart layout in calibrate_lighting.lua.
"""
import json
import math
import os
import sys

import numpy as np

COLS, ROWS = 12, 6
GRID = (0.06, 0.94, 0.12, 0.90)
CORNERS = [(0.02, 0.03), (0.98, 0.03), (0.02, 0.97), (0.98, 0.97)]
BITS_Y, BITS_X = 0.05, [0.40 + i * 0.0667 for i in range(4)]
GREYS = [0, 4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 255]
COLOURS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 128, 0), (200, 0, 255), (0, 255, 255),
           (255, 255, 0), (128, 64, 32), (64, 128, 255), (255, 64, 64), (32, 128, 32), (255, 200, 150)]
DISTS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 20, 28]
STAGES = {1: dict(ec=-2), 2: dict(ec=0), 3: dict(ec=2), 4: dict(ec=4),
          5: dict(b=1, r=16), 6: dict(b=4, r=16), 7: dict(b=1, r=32)}


def lin(c):
    """Roblox decodes colours with gamma 2 (its surface shaders square the part colour)."""
    c = np.asarray(c, dtype=float)
    return c * c


def rational(x, p):
    """Roblox's tonemapper (ImageProcessFS): e * x(ax+b) / (x(ax+c)+d), per channel, display output."""
    a, b, c, d, e = p
    return e * x * (a * x + b) / (x * (a * x + c) + d)


def nelder_mead(f, x0, steps, iters=6000):
    pts = [np.array(x0, dtype=float)]
    for i, s in enumerate(steps):
        q = np.array(x0, dtype=float)
        q[i] += s
        pts.append(q)
    vals = [f(q) for q in pts]
    for _ in range(iters):
        o = np.argsort(vals)
        pts, vals = [pts[i] for i in o], [vals[i] for i in o]
        c = np.mean(pts[:-1], axis=0)
        r = c + (c - pts[-1])
        fr = f(r)
        if fr < vals[0]:
            e = c + 2 * (c - pts[-1])
            fe = f(e)
            pts[-1], vals[-1] = (e, fe) if fe < fr else (r, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = r, fr
        else:
            k = c + 0.5 * (pts[-1] - c)
            fk = f(k)
            if fk < vals[-1]:
                pts[-1], vals[-1] = k, fk
            else:
                pts = [pts[0] + 0.5 * (q - pts[0]) for q in pts]
                vals = [f(q) for q in pts]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def load(path):
    import bpy
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)[::-1, :, :3]  # top row first
    bpy.data.images.remove(img)
    return px


def blobs(mask):
    """Centroids of 4-connected blobs (small images of markers; fine in pure numpy+python)."""
    ys, xs = np.nonzero(mask)
    seen = set()
    pts = set(zip(ys.tolist(), xs.tolist()))
    out = []
    for p in list(pts):
        if p in seen:
            continue
        stack, acc = [p], []
        seen.add(p)
        while stack:
            y, x = stack.pop()
            acc.append((y, x))
            for q in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if q in pts and q not in seen:
                    seen.add(q)
                    stack.append(q)
        if len(acc) >= 30:
            a = np.array(acc, dtype=float)
            out.append((a[:, 1].mean(), a[:, 0].mean(), len(acc)))
    return out


def frame(px):
    """Maps viewport fractions -> image pixels using the 4 magenta corner squares."""
    m = (px[:, :, 0] > 0.97) & (px[:, :, 1] < 0.03) & (px[:, :, 2] > 0.97)
    b = blobs(m)
    if len(b) < 4:
        raise ValueError("found %d of the 4 magenta corner markers" % len(b))
    b.sort(key=lambda t: -t[2])
    b = b[:8]
    tl = min(b, key=lambda t: t[0] + t[1])
    br = max(b, key=lambda t: t[0] + t[1])
    tr = max(b, key=lambda t: t[0] - t[1])
    bl = min(b, key=lambda t: t[0] - t[1])
    (fx0, fy0), (fx1, fy1) = CORNERS[0], CORNERS[3]

    def to_px(fx, fy):
        u, v = (fx - fx0) / (fx1 - fx0), (fy - fy0) / (fy1 - fy0)
        top = (tl[0] + (tr[0] - tl[0]) * u, tl[1] + (tr[1] - tl[1]) * u)
        bot = (bl[0] + (br[0] - bl[0]) * u, bl[1] + (br[1] - bl[1]) * u)
        return top[0] + (bot[0] - top[0]) * v, top[1] + (bot[1] - top[1]) * v

    cell = abs(tr[0] - tl[0]) / (fx1 - fx0) * (GRID[1] - GRID[0]) / COLS
    return to_px, cell


def sample(px, to_px, fx, fy, half):
    x, y = to_px(fx, fy)
    h = max(1, int(half))
    win = px[int(y) - h:int(y) + h + 1, int(x) - h:int(x) + h + 1]
    return np.median(win.reshape(-1, 3), axis=0)


def read_shot(path, nbits=4):
    px = load(path)
    to_px, cell = frame(px)
    sid = 0
    for i, fx in enumerate([0.40 + i * 0.0667 for i in range(nbits)]):
        if sample(px, to_px, fx, BITS_Y, 3).mean() > 0.5:
            sid |= 1 << i
    cells = np.zeros((ROWS, COLS, 3))
    for r in range(ROWS):
        for c in range(COLS):
            fx = GRID[0] + (c + 0.5) / COLS * (GRID[1] - GRID[0])
            fy = GRID[2] + (r + 0.5) / ROWS * (GRID[3] - GRID[2])
            cells[r, c] = sample(px, to_px, fx, fy, cell * 0.2)
    return sid, cells


def main(argv):
    out = "roblox_lighting_fit.json"
    if "-o" in argv:
        i = argv.index("-o")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    shots = {}
    for p in argv:
        try:
            sid, cells = read_shot(p)
        except Exception as e:
            print("skip %s: %s" % (os.path.basename(p), e))
            continue
        if sid not in STAGES:
            print("skip %s: stage bits read %d" % (os.path.basename(p), sid))
            continue
        shots.setdefault(sid, []).append(cells)
        print("%s -> stage %d" % (os.path.basename(p), sid))
    st = {k: np.median(np.array(v), axis=0) for k, v in shots.items()}
    report = {"stages": sorted(st)}

    # ---- tonemapper: grey patches, input x = albedo_lin * 2^EC (Ambient = 1)
    tone_cells = [(i % COLS, i // COLS) for i in range(len(GREYS) + len(COLOURS))]
    xs, ys = [], []
    for sid in (1, 2, 3, 4):
        if sid not in st:
            continue
        e = 2.0 ** STAGES[sid]["ec"]
        for i, g in enumerate(GREYS):
            c, r = tone_cells[i]
            xs.append(float(lin(g / 255.0)) * e)
            ys.append(float(st[sid][r, c].mean()))
    curve = None
    if xs:
        o = np.argsort(xs)
        xs, ys = np.array(xs)[o], np.array(ys)[o]
        keep = xs > 0
        lx = np.log2(xs[keep])
        ly = np.maximum.accumulate(ys[keep])  # monotone
        grid = np.linspace(-12, 6, 73)
        cy = np.interp(grid, lx, ly)
        curve = {"log2x": grid.tolist(), "y": cy.tolist(), "samples": [[float(a), float(b)] for a, b in zip(lx, ys[keep])],
                 "black": float(ys[~keep].mean()) if (~keep).any() else 0.0}
        report["tonemap"] = curve
        sx, sy = xs[keep], ys[keep]
        ok = sy < 0.985  # clipped samples say nothing

        def cost(p):
            if min(p[0], p[3], p[4]) <= 0:
                return 1e9
            with np.errstate(all="ignore"):
                r = rational(sx[ok], p) - sy[ok]
            return float(np.mean(r * r)) if np.all(np.isfinite(r)) else 1e9
        # start from the ACES fit (Narkowicz) written in Roblox's form
        p, err = nelder_mead(cost, [2.43, 0.029, 0.59, 0.14, 1.033], [0.5, 0.02, 0.2, 0.05, 0.1])
        report["tonemap_fit"] = {"a": p[0], "b": p[1], "c": p[2], "d": p[3], "e": p[4], "rmse": err ** 0.5}
        print("\nRoblox tonemapper e*x(ax+b)/(x(ax+c)+d): a=%.5g b=%.5g c=%.5g d=%.5g e=%.5g  (rms error %.4f)"
              % (p[0], p[1], p[2], p[3], p[4], err ** 0.5))
        print("\ntonemap (scene linear -> display sRGB):")
        for v in (0.01, 0.05, 0.1, 0.18, 0.5, 1, 2, 4, 8, 16):
            print("  %6.3f -> %.3f   (plain sRGB %.3f)" % (v, np.interp(math.log2(v), grid, cy), min(1.0, 1.055 * v ** (1 / 2.4) - 0.055)))

    def T(x):
        x = np.asarray(x, dtype=float)
        return np.interp(np.log2(np.maximum(x, 2.0 ** -12)), curve["log2x"], curve["y"])

    def Tinv(y):
        """inverse of the fitted rational curve (smooth, unlike the sampled one)"""
        f = report.get("tonemap_fit")
        if not f:
            return 2.0 ** np.interp(y, curve["y"], curve["log2x"])
        a, b, c, d, e = f["a"], f["b"], f["c"], f["d"], f["e"]
        u = min(float(y) / e, 0.999)
        A, B, C = a * (1 - u), b - c * u, -d * u
        return max(0.0, (-B + math.sqrt(max(B * B - 4 * A * C, 0.0))) / (2 * A))

    # ---- colour handling: per-channel curve vs hue-preserving (curve on max channel)
    if curve:
        err_pc, err_hp, err_lum = [], [], []
        for sid in (1, 2, 3, 4):
            if sid not in st:
                continue
            e = 2.0 ** STAGES[sid]["ec"]
            for j, rgb in enumerate(COLOURS):
                c, r = tone_cells[len(GREYS) + j]
                x = lin(np.array(rgb) / 255.0) * e
                meas = st[sid][r, c]
                if meas.max() > 0.97:
                    continue  # clipped, says nothing about hue
                pc = T(x)
                mx = max(x.max(), 1e-6)
                hp = T(mx) * (x / mx) ** (1 / 2.2)
                lum = max(float(x @ [0.2126, 0.7152, 0.0722]), 1e-6)
                lp = np.clip(lin(T(lum)) * x / lum, 0, 1) ** (1 / 2.2)
                err_pc.append(np.abs(pc - meas).mean())
                err_hp.append(np.abs(hp - meas).mean())
                err_lum.append(np.abs(lp - meas).mean())
        if err_pc:
            report["colour"] = {"per_channel": float(np.mean(err_pc)), "max_channel": float(np.mean(err_hp)),
                                "luminance": float(np.mean(err_lum))}
            print("\ncolour model error (lower = Roblox works that way):", report["colour"])

    # ---- light falloff: white - black (specular cancels), un-tonemapped
    if curve:
        fall = {}
        for sid in (5, 6, 7):
            if sid not in st:
                continue
            b, R = STAGES[sid]["b"], STAGES[sid]["r"]
            rows = []
            for i, d in enumerate(DISTS):
                k = i * 2
                c, r = k % COLS, 3 + k // COLS
                w = float(Tinv(st[sid][r, c].mean()))
                bl = float(Tinv(st[sid][r, c + 1].mean()))
                clipped = st[sid][r, c].mean() > 0.97
                rows.append({"d": d, "x": d / R, "diffuse": (w - bl) / b, "spec": bl / b, "clipped": bool(clipped)})
            fall[sid] = rows
        if fall:
            report["falloff"] = fall
            ref = fall.get(5) or next(iter(fall.values()))
            good = [p for p in ref if not p["clipped"] and p["x"] < 1]
            if good:
                k0 = max(p["diffuse"] for p in good)
                best = None
                R5 = STAGES[5]["r"]
                models = (("(1-x^2)^p", lambda x, p: (1 - x * x) ** p, np.arange(0.25, 8.01, 0.05)),
                          ("(1-x)^p", lambda x, p: (1 - x) ** p, np.arange(0.25, 8.01, 0.05)),
                          # Roblox Future shader: (1 - smoothstep(x)) * K / (K + d^2), d in studs; p = K
                          ("roblox K", lambda x, p: (1 - 3 * x * x + 2 * x ** 3) * p / (p + (x * R5) ** 2),
                           np.exp(np.linspace(math.log(5), math.log(5000), 300))))
                for name, fn, prange in models:
                    for p in prange:
                        sc = [p_["diffuse"] for p_ in good]
                        md = [fn(p_["x"], p) for p_ in good]
                        a = float(np.dot(sc, md) / max(np.dot(md, md), 1e-9))
                        er = float(np.mean([(s - a * m) ** 2 for s, m in zip(sc, md)]))
                        if best is None or er < best[0]:
                            best = (er, name, float(p), a)
                report["falloff_fit"] = {"model": best[1], "power": best[2], "scale": best[3], "rmse": best[0] ** 0.5}
                print("\nbest falloff model: %s, p=%.3g, light scale %.3f (Unity intensity per Roblox Brightness)" % best[1:])
                print("  d/R   diffuse   spec")
                for p_ in ref:
                    print("  %.2f  %.4f   %.4f %s" % (p_["x"], p_["diffuse"], p_["spec"], "clipped" if p_["clipped"] else ""))
                if 6 in fall:
                    r6 = [a["diffuse"] / max(b_["diffuse"], 1e-6) for a, b_ in zip(fall[6], ref)
                          if not a["clipped"] and b_["diffuse"] > 0.01 and a["x"] < 1]
                    if r6:
                        report["brightness_linear"] = float(np.median(r6))
                        print("  Brightness 4 vs 1, per unit Brightness: %.2f (1.00 = linear)" % report["brightness_linear"])
    with open(out, "w") as f:
        json.dump(report, f, indent=1)
    print("\nwrote", out)


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    main(a)
