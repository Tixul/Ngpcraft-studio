"""Small GB Studio projects for back-end tests, cut from the released gbs2.

The result is a real GB Studio 4.3.2 project directory (it loads in GB Studio):
gbs2 with only its start scene (logo), without actors, whose On Init is the
given script. Other resources (sprites, backgrounds, variables...) are kept.
"""
import json
from pathlib import Path
import shutil

from ngpcraft.project import encode_resource
from tools.reference import REFERENCE

GBS2 = REFERENCE / "appData/templates/gbs2"
START_SCENE = "project/scenes/ui/logo"


def make_project(dest, script, auto_fade=None, actors=None):
    dest = Path(dest)
    shutil.copytree(GBS2, dest)
    for scene in list((dest / "project/scenes").rglob("scene.gbsres")):
        folder = scene.parent
        if folder.relative_to(dest).as_posix() != START_SCENE:
            shutil.rmtree(folder)
    keep = dest / START_SCENE
    shutil.rmtree(keep / "actors", ignore_errors=True)
    path = keep / "scene.gbsres"
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["script"] = script
    scene["autoFadeSpeed"] = auto_fade
    path.write_text(encode_resource(scene), encoding="utf-8", newline="")
    for i, actor in enumerate(actors or []):
        (keep / "actors").mkdir(exist_ok=True)
        (keep / "actors" / f"actor_{i}.gbsres").write_text(encode_resource(actor), encoding="utf-8", newline="")
    return dest


def event(eid, command, **args):
    return {"command": command, "args": args, "id": eid}


def number(value):
    return {"type": "number", "value": value}
