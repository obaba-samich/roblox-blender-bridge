"""Builds the installable files into dist/ (any OS, Python 3, no Blender needed):

    python3 tools/build_release.py

Part 1, Roblox -> Blender:
    RobloxBlenderBridge.lua         Roblox Studio plugin (copy into Studio's Plugins folder)
    roblox_bridge.zip               Blender add-on (Edit > Preferences > Add-ons > Install from Disk)
Part 2, Blender -> Unity:
    roblox_bridge_unity.zip         Blender add-on (needs part 1's add-on)
    com.robloxbridge.importer.tgz   Unity package (Package Manager > + > Install package from tarball)
"""
import os
import shutil
import sys
import tarfile
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST = os.path.join(ROOT, "dist")


def skip(path):
    return "__pycache__" in path or path.endswith(".pyc")


def zip_addon(src_parent, name):
    out = os.path.join(DIST, name + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _, fs in os.walk(os.path.join(src_parent, name)):
            for f in fs:
                full = os.path.join(r, f)
                if not skip(full):
                    z.write(full, os.path.relpath(full, src_parent))
    return out


def main():
    os.makedirs(DIST, exist_ok=True)
    made = [shutil.copy(os.path.join(ROOT, "roblox-to-blender", "studio-plugin", "RobloxBlenderBridge.lua"), DIST),
            zip_addon(os.path.join(ROOT, "roblox-to-blender", "blender-addon"), "roblox_bridge"),
            zip_addon(os.path.join(ROOT, "blender-to-unity", "blender-addon"), "roblox_bridge_unity")]
    # Unity's tarball format: the package's files under package/
    pkg = os.path.join(ROOT, "blender-to-unity", "unity-package")
    out = os.path.join(DIST, "com.robloxbridge.importer.tgz")
    with tarfile.open(out, "w:gz") as t:
        t.add(pkg, arcname="package", filter=lambda ti: None if skip(ti.name) else ti)
    made.append(out)
    for m in made:
        print("built", os.path.relpath(m, ROOT))


if __name__ == "__main__":
    sys.exit(main())
