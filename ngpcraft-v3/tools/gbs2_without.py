"""gbs2 without the events not delivered yet (audio): the whole sample
project for the M4 gate checks (docs/M5_SAVE.md, docs/REPRISE.md). Save,
Load and If Data Saved are delivered since 2026-10-07 and kept; SAVE_EVENTS
removes them too for the step 3 audits made without them.

A refused event is removed; EVENT_IF_SAVED_DATA keeps its false branch (no
save exists in a fresh project). The copy is a real GB Studio project.

  python -B -m tools.gbs2_without --out NEW_DIRECTORY [--export]
"""
import argparse
import json
import shutil
from pathlib import Path

from ngpcraft.project import encode_resource
from tools.reference import REFERENCE

GBS2 = REFERENCE / "appData/templates/gbs2"
NOT_DELIVERED = ("EVENT_MUSIC_PLAY", "EVENT_MUSIC_STOP", "EVENT_SOUND_PLAY_EFFECT")
SAVE_EVENTS = ("EVENT_IF_SAVED_DATA", "EVENT_LOAD_DATA", "EVENT_SAVE_DATA")


def _strip(value, removed, commands):
    if isinstance(value, list):
        out = []
        for e in value:
            if isinstance(e, dict) and e.get("command") in commands:
                removed.append(e["command"])
                if e["command"] == "EVENT_IF_SAVED_DATA":
                    out += _strip(((e.get("children") or {}).get("false")) or [], removed, commands)
                continue
            out.append(_strip(e, removed, commands))
        return out
    if isinstance(value, dict):
        return {k: _strip(v, removed, commands) for k, v in value.items()}
    return value


SCENE_TYPES_NOT_DELIVERED = ("SHMUP", "POINTNCLICK")   # genre engines of M5


def _drop_scenes(project, types, removed):
    """Remove the scenes of `types` (their folder) and every switch to them."""
    gone = set()
    for path in sorted(project.rglob("scene.gbsres")):
        scene = json.loads(path.read_text(encoding="utf-8"))
        if scene["type"] in types:
            gone.add(scene["id"])
            removed.append(f"scene {scene['name']} ({scene['type']})")
            shutil.rmtree(path.parent)

    def strip(value):
        if isinstance(value, list):
            out = []
            for e in value:
                if isinstance(e, dict) and e.get("command") == "EVENT_SWITCH_SCENE" \
                        and (e.get("args") or {}).get("sceneId") in gone:
                    removed.append("EVENT_SWITCH_SCENE")
                    continue
                out.append(strip(e))
            return out
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items()}
        return value
    for path in sorted(project.rglob("*.gbsres")):
        data = json.loads(path.read_text(encoding="utf-8"))
        new = strip(data)
        if new != data:
            path.write_text(encode_resource(new), encoding="utf-8", newline="")


def make(destination, commands=NOT_DELIVERED, scene_types=()):
    """Copy gbs2 to destination/project without `commands` and the scenes of
    `scene_types`; returns the project path and what was removed."""
    project = Path(destination) / "project"
    shutil.copytree(GBS2, project)
    removed = []
    if scene_types:
        _drop_scenes(project, set(scene_types), removed)
    for path in sorted(project.rglob("*.gbsres")):
        data = json.loads(path.read_text(encoding="utf-8"))
        new = _strip(data, removed, set(commands))
        if new != data:
            path.write_text(encode_resource(new), encoding="utf-8", newline="")
    return project, removed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True)
    p.add_argument("--export", action="store_true")
    p.add_argument("--without-genres", action="store_true", help="also drop the SHMUP and POINTNCLICK scenes")
    a = p.parse_args()
    project, removed = make(a.out, scene_types=SCENE_TYPES_NOT_DELIVERED if a.without_genres else ())
    print(f"{len(removed)} events removed: " + ", ".join(sorted(set(removed))))
    if a.export:
        from ngpcraft.pipeline import export_project
        manifest = export_project(project, Path(a.out) / "export")
        print(json.dumps({"features": manifest["features"], "warnings": manifest.get("warnings", [])}, indent=1))


if __name__ == "__main__":
    main()
