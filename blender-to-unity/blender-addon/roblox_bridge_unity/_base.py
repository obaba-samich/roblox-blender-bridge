"""Part 2 builds on part 1: the Roblox Bridge add-on (Studio -> Blender) gives the models, and these
helpers (UV layers, material pixels/colours). Found however Blender installed it (legacy add-on or extension)."""
import importlib
import sys


def _find():
    names = ["roblox_bridge"] + [n for n in list(sys.modules) if n.endswith(".roblox_bridge")]
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            pass
    raise ImportError("Roblox Bridge -> Unity needs part 1, the Roblox Bridge add-on (Studio -> Blender), installed and enabled")


base = _find()
materials = importlib.import_module(base.__name__ + ".materials")
uvtools = importlib.import_module(base.__name__ + ".uvtools")
