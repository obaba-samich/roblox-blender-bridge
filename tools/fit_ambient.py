"""Reads screenshots of tools/calibrate_ambient.lua: how much light Roblox's sun, sky and ambient give.

    blender -b --factory-startup --python tools/fit_ambient.py -- shot1.png ... [-o <model>.ambient.json] [--ec 0]
        [--sidecar <model>.roblox.json]   (its sun direction: splits direct sun from sky, stages 5-7)

Put the output next to the model's FBX in Unity (e.g. Assets/Roblox/Lobby/Lobby.ambient.json).

Values are scene-linear light on a white (albedo 1) surface, undoing Roblox's measured tonemapper,
so they compare directly with Unity: diffuse = albedo x this.
"""
import importlib.util
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fit_lighting", os.path.join(HERE, "fit_lighting.py"))
fit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fit)

NAMES = {9: "open, sun on", 10: "open, sun off", 11: "closed, sun off", 12: "closed, sun on",
         17: "open, sun on, +X", 18: "open, sun on, -Z", 19: "open, sun on, -X"}


def untonemap(y):
    """inverse of Roblox's curve 1.049*x(x+0.654)/(x(x+0.775)+0.2035) (measured, quality 21)"""
    a, b, c, d, e = 1.0, 0.654, 0.775, 0.2035, 1.049
    u = min(float(y) / e, 0.999)
    A, B, C = a * (1 - u), b - c * u, -d * u
    return max(0.0, (-B + math.sqrt(max(B * B - 4 * A * C, 0.0))) / (2 * A))


def sun_incidence(yaw, sun, aspect, fov=70.0, pitch=-40.0):
    """Mean max(0, N.L) of the white camera-facing plates of a chart turned by `yaw` degrees
    (as calibrate_ambient.lua builds them: each faces the camera horizontally, so they fan out)."""
    th = math.tan(math.radians(fov) / 2)
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    vals = []
    for r in range(fit.ROWS):
        for c in range(6, 12, 2):
            fx = fit.GRID[0] + (c + 0.5) / fit.COLS * (fit.GRID[1] - fit.GRID[0])
            fy = fit.GRID[2] + (r + 0.5) / fit.ROWS * (fit.GRID[3] - fit.GRID[2])
            d = np.array([(2 * fx - 1) * th * aspect, -(2 * fy - 1) * th, -1.0])
            d = np.array([d[0], d[1] * cp - d[2] * sp, d[1] * sp + d[2] * cp])      # pitch (about X)
            d = np.array([d[0] * cy + d[2] * sy, d[1], -d[0] * sy + d[2] * cy])     # yaw (about Y)
            n = -np.array([d[0], 0.0, d[2]])
            n /= np.linalg.norm(n)
            vals.append(max(0.0, float(n @ sun)))
    return float(np.mean(vals))


def main(argv):
    out, ec, sidecar = "roblox_ambient_fit.json", 0.0, None
    if "--sidecar" in argv:
        i = argv.index("--sidecar"); sidecar = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if "-o" in argv:
        i = argv.index("-o"); out = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if "--ec" in argv:
        i = argv.index("--ec"); ec = float(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    shots, aspect = {}, None
    for p in argv:
        try:
            sid, cells = fit.read_shot(p, 5)
            if sid not in NAMES and sid & 15 in NAMES:
                sid &= 15   # older shots have no 5th square
        except Exception as e:
            print("skip %s: %s" % (os.path.basename(p), e))
            continue
        if sid not in NAMES:
            print("skip %s: stage bits read %d" % (os.path.basename(p), sid))
            continue
        shots.setdefault(sid, []).append(cells)
        if aspect is None:
            to_px, _ = fit.frame(fit.load(p))
            (x0, y0), (x1, _), (_, y1) = to_px(0, 0), to_px(1, 0), to_px(0, 1)
            aspect = abs(x1 - x0) / abs(y1 - y0)
    res = {}
    k = 2.0 ** ec
    for sid in sorted(shots):
        cells = np.median(np.array(shots[sid]), axis=0)  # rows x cols x rgb
        lin = np.vectorize(untonemap)(cells) / k
        up_w, up_b = lin[:, 0:6:2].mean(axis=(0, 1)), lin[:, 1:6:2].mean(axis=(0, 1))
        sd_w, sd_b = lin[:, 6:12:2].mean(axis=(0, 1)), lin[:, 7:12:2].mean(axis=(0, 1))
        res[NAMES[sid]] = {"up": (up_w - up_b).tolist(), "side": (sd_w - sd_b).tolist(),
                           "reflect_up": up_b.tolist(), "reflect_side": sd_b.tolist(), "shots": len(shots[sid])}
        print("%-16s up %s  side %s   (reflection up %s side %s)" % (
            NAMES[sid], np.round(up_w - up_b, 3), np.round(sd_w - sd_b, 3), np.round(up_b, 3), np.round(sd_b, 3)))
    # the keys the Unity importer reads (<model>.ambient.json next to the FBX)
    keys = {"closed, sun off": ("indoorUp", "indoorSide"), "open, sun off": ("openUp", "openSide"),
            "open, sun on": ("openUpSun", "openSideSun")}
    # walls facing each Roblox direction (stage 1 faces +Z)
    for name, key in (("open, sun on", "openSidePZ"), ("open, sun on, +X", "openSidePX"),
                      ("open, sun on, -Z", "openSideNZ"), ("open, sun on, -X", "openSideNX")):
        if name in res:
            res[key] = res[name]["side"]
    for name, (ku, ks) in keys.items():
        if name in res:
            res[ku], res[ks] = res[name]["up"], res[name]["side"]
    # direct sun: walls facing each way = sky + sun x incidence (the sky doesn't brighten with the sun:
    # in the Lobby the wall facing away read the same with the sun on as off). Least squares per channel.
    dirs = [(0, "openSidePZ"), (90, "openSidePX"), (180, "openSideNZ"), (270, "openSideNX")]
    if sidecar and all(k in res for _, k in dirs):
        sun = np.array(json.load(open(sidecar))["lighting"]["sunDirection"], dtype=float)
        g = np.array([sun_incidence(y, sun, aspect or 16 / 9) for y, _ in dirs])
        A = np.stack([np.ones(4), g], axis=1)
        W = np.array([res[k] for _, k in dirs])
        sol = np.linalg.lstsq(A, W, rcond=None)[0]
        res["skySide"], res["sunDirect"] = np.maximum(sol[0], 0).tolist(), np.maximum(sol[1], 0).tolist()
        res["sunIncidence"] = g.round(3).tolist()
        print("sun incidence per stage %s -> sky side %s, direct sun %s (x N.L)" % (
            np.round(g, 3), np.round(sol[0], 3), np.round(sol[1], 3)))
        print("   fit error per stage:", np.round(W - A @ sol, 3).tolist())
    with open(out, "w") as f:
        json.dump(res, f, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
