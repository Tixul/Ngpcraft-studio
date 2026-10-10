"""Scene change proof: two GB Studio scenes that hand over to each other.

A copy of gbs2 keeps Sample Town (56x56 tiles, streamed, 152 px view) and
Cave (20x18 tiles, 144 px between black bands). Their scripts are emptied
(as tools/demo_gbs_scene.py does), then:
  Sample Town On Init: VAR_0 += 1, wait W frames, Switch Scene -> Cave
  Cave On Init:        VAR_1 += 1, wait W frames, Switch Scene -> Sample Town
Both scenes keep GB Studio's automatic fade in; each switch fades out first
(fadeSpeed). The probe reads the two variables, the scene index and the
player in the ROM: variables persist, the player arrives where the event says.

  python -B -m tools.demo_scene_switch --out <new dir> --toolchain C:\\t900
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from ngpcraft.background_stream import view_of
from ngpcraft.gbvm.emit import engine_field
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import SCRIPT_KEYS, write_json
from tools.gbvm_actors import op_idle, op_wait, plain
from tools.gbvm_scenes import op_switch, scene_switch_frames
from tools.reference import REFERENCE

GBS2 = REFERENCE / "appData/templates/gbs2"
TOWN, CAVE = "sample_town", "cave"
TOWN_START = (12, 20)          # settings start tile (Sample Town)
CAVE_ARRIVAL = (9, 12, "up")   # where the switch puts the player in Cave
TOWN_ARRIVAL = (12, 20, "down")


def ev(eid, command, **args):
    return {"command": command, "args": args, "id": eid}


def num(value):
    return {"type": "number", "value": value}


def derive_scenes(source, destination, folders, start_folder, start):
    """gbs2 with only `folders` (scene folder names); every script emptied."""
    shutil.copytree(source, destination)
    scenes = [p.parent for p in (destination / "project/scenes").rglob("scene.gbsres")]
    keep = {p.name: p for p in scenes if p.name in folders}
    if sorted(keep) != sorted(folders):
        raise SystemExit(f"scene folders {folders}: found {sorted(keep)}")
    for folder in scenes:
        if folder.name not in folders:
            shutil.rmtree(folder)
    ids = {}
    for name, folder in keep.items():
        for res in [folder / "scene.gbsres", *sorted(folder.rglob("actors/*.gbsres")),
                    *sorted(folder.rglob("triggers/*.gbsres"))]:
            data = json.loads(res.read_text(encoding="utf-8"))
            for key in SCRIPT_KEYS:
                if data.get(key):
                    data[key] = []
            write_json(res, data)
        ids[name] = json.loads((folder / "scene.gbsres").read_text(encoding="utf-8"))["id"]
    for res in sorted((destination / "project/prefabs").rglob("*.gbsres")):
        data = json.loads(res.read_text(encoding="utf-8"))
        for key in SCRIPT_KEYS:
            if data.get(key):
                data[key] = []
        write_json(res, data)
    path = destination / "project/settings.gbsres"
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["startSceneId"] = ids[start_folder]
    settings["startX"], settings["startY"] = start
    write_json(path, settings)
    return keep, ids


def set_script(folder, script):
    path = folder / "scene.gbsres"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["script"] = script
    write_json(path, data)


def switch(eid, scene_id, arrival, fade):
    x, y, direction = arrival
    return ev(eid, "EVENT_SWITCH_SCENE", sceneId=scene_id, x=num(x), y=num(y), direction=direction,
              fadeSpeed=fade)


# Town -> Cave fades out first (speed 2); Cave -> Town cuts (no fade): the new
# colours must then show at once, not the previous scene's staged palette.
FADE_TO_CAVE, FADE_TO_TOWN = "2", "0"


def project_case(root, wait=60, extra=()):
    """extra: more gbs2 scene folders kept (unreachable: nothing switches to them)."""
    keep, ids = derive_scenes(GBS2, root / "project", [TOWN, CAVE, *extra], TOWN, TOWN_START)
    set_script(keep[TOWN], [ev("t-count", "EVENT_INC_VALUE", variable="0"),
                            ev("t-wait", "EVENT_WAIT", units="frames", frames=num(wait)),
                            switch("t-go", ids[CAVE], CAVE_ARRIVAL, FADE_TO_CAVE)])
    set_script(keep[CAVE], [ev("c-count", "EVENT_INC_VALUE", variable="1"),
                            ev("c-wait", "EVENT_WAIT", units="frames", frames=num(wait)),
                            switch("c-go", ids[TOWN], TOWN_ARRIVAL, FADE_TO_TOWN)])
    return root / "project", ids


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--wait", type=int, default=60)
    parser.add_argument("--ticks", type=int, default=300, help="ticks the probe compares")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    args.out.mkdir(parents=True)
    manifest, build = build_case(args.out, args.toolchain, args.wait, args.ticks)
    rom = args.out / f"ngpcraft_m2_scene_switch_{args.wait}f.ngc"
    write_new(rom, (args.out / "export/rom/game.ngc").read_bytes())
    print(json.dumps({"rom": str(rom), "bytes": build["header"]["size_bytes"],
                      "scenes": [(s["index"], s["scene"]) for s in manifest["scene_engine"]["scenes"]],
                      "link": manifest["scene_engine"]["link"]}, indent=1))


def build_case(root, toolchain, wait=60, ticks=300, mutate=None):
    """Project, export, ROM and probe case in `root`. mutate: (file, old, new)
    replaced once in the exported C before the build (witness tests)."""
    project, ids = project_case(root, wait)
    manifest = export_project(project, root / "export")
    if mutate:
        rel, old, new = mutate
        path = root / "export" / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"mutation target found {text.count(old)} times in {rel}")
        text = text.replace(old, new)
        write_new(path.with_suffix(".mutated"), text.encode("utf-8"))
        path.with_suffix(".mutated").replace(path)
        manifest["sha256"][rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        write_new(root / "export/export.json.tmp", json.dumps(manifest, ensure_ascii=False, indent=2,
                                                              sort_keys=True).encode("utf-8") + b"\n")
        (root / "export/export.json.tmp").replace(root / "export/export.json")
    build = build_rom(root / "export", toolchain)
    write_case(root, project, manifest, ids, wait, ticks)
    return manifest, build


def init_ops(wait, target, arrival, fade):
    """The init thread of each scene as compiled (export.json scripts): VM_LOCK,
    VM_RESERVE, VM_SET_SPRITE_MODE, VM_RPN (+1); VM_IDLE (automatic fade in);
    fade speed, VM_FADE_IN modal, wait arguments; VM_INVOKE wait_frames; fade
    speed, VM_FADE_OUT modal, VM_RPN (position), .LOCAL_ACTOR, SET_POS,
    SET_DIR, camera_settings; VM_RAISE."""
    direction = {"down": 0, "right": 1, "up": 2, "left": 3}[arrival[2]]
    # Without a fade (fadeSpeed 0) no fade speed and no VM_FADE_OUT.
    return (plain(4) + [op_idle()] + plain(3) + [op_wait(wait)] + plain(7 if fade else 5)
            + [op_switch(target, arrival[0], arrival[1], direction)])


def write_case(out, project_dir, manifest, ids, wait, ticks, scripts_for=None):
    """source.json and the scenes' pixels for tools/probe_scene_switch.py.
    scripts_for(town, cave): the oracle's init threads by scene index (default:
    this demo's two switches)."""
    project = load_project(project_dir)
    resources = {s["id"]: s for s in project.scenes}
    reports = manifest["scene_engine"]["scenes"]
    index = {r["id"]: r["index"] for r in reports}
    engines = [build_engine_scene(project, resources[r["id"]]) for r in reports]
    grids = [r["topdown"]["grid_px"] if "topdown" in r else None for r in reports]
    town, cave = index[ids[TOWN]], index[ids[CAVE]]
    scripts = scripts_for(town, cave) if scripts_for else {
        town: lambda: init_ops(wait, cave, CAVE_ARRIVAL, FADE_TO_CAVE != "0"),
        cave: lambda: init_ops(wait, town, TOWN_ARRIVAL, FADE_TO_TOWN != "0")}
    tick_frames = 2 if "frame_rate_30" in manifest["features"] else 1
    frames, loads = scene_switch_frames(engines, grids, manifest["scene_engine"]["start"], ticks * tick_frames + 10, scripts)
    scenes = []
    for r, engine in zip(reports, engines):
        scene = resources[r["id"]]
        width, height, pixels = background_pixels(project, scene)
        folder = out / f"scene_{r['index']}"
        folder.mkdir()
        write_new(folder / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
        scenes.append({"name": scene["name"], "size_px": [width, height],
                       "view": list(view_of(engine.background))[::-1],
                       "sprite_palettes": [list(p) for p in sprite_palettes(project, scene)]})
    write_new(out / "source.json", json.dumps({
        "scenes": scenes, "probe_frames": ticks, "fade_style": engine_field(project, "fade_style"),
        "tick_frames": tick_frames, "fade_enabled": "fades" in manifest["features"],
        "expected_scene": [k for k, _, _ in frames], "expected_scroll": [list(p) for _, p, _ in frames],
        # Draws of every tick, then of every load picture (index in "loads").
        "actors": [d for _, _, d in frames] + [d for _, _, _, d in loads],
        "loads": [[t, k, list(p), len(frames) + i] for i, (t, k, p, _) in enumerate(loads)],
        "project": str(project_dir),
        "default_sprite_mode": project.settings["spriteMode"], "wait": wait,
        "oracle": "tools/gbvm_scenes.scene_switch_frames"}, indent=1).encode("utf-8"))


if __name__ == "__main__":
    main()
