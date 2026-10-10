"""Export a real GB Studio scene through the scene engine and check it frame by frame.

A copy of gbs2 is reduced to one scene (the reference stays untouched): the
other scenes are removed, the start scene is set, and the scripts that use
events not delivered yet are emptied (listed in source.json). Everything else
(background, actors, prefabs, sheets, palettes, positions, flags) comes from the
GB Studio files through `ngpcraft export`, then the ROM is built and the
expected frames come from the GBVM oracle (tools/gbvm_actors.engine_frames).
"""
import argparse
import json
from pathlib import Path
import shutil

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new
from tools.gbvm_actors import engine_frames
from tools.reference import REFERENCE

SCRIPT_KEYS = ("script", "startScript", "updateScript", "hit1Script", "hit2Script", "hit3Script",
               "playerHit1Script", "playerHit2Script", "playerHit3Script", "leaveScript")


def write_json(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)


def derive(source, destination, scene_folder, start, fade):
    shutil.copytree(source, destination)
    scenes = [p.parent for p in (destination / "project/scenes").rglob("scene.gbsres")]
    keep = [p for p in scenes if p.name == scene_folder]
    if len(keep) != 1:
        raise SystemExit(f"scene folder {scene_folder} must exist exactly once")
    for folder in scenes:
        if folder != keep[0]:
            shutil.rmtree(folder)
    emptied = []
    for res in [keep[0] / "scene.gbsres", *sorted(keep[0].rglob("actors/*.gbsres")), *sorted(keep[0].rglob("triggers/*.gbsres")),
                *sorted((destination / "project/prefabs").rglob("*.gbsres"))]:
        data = json.loads(res.read_text(encoding="utf-8"))
        for key in SCRIPT_KEYS:
            if data.get(key):
                emptied.append(f"{res.relative_to(destination).as_posix()}:{key}")
                data[key] = []
        if res.name == "scene.gbsres" and not fade:
            data["autoFadeSpeed"] = None
        write_json(res, data)
    settings_path = destination / "project/settings.gbsres"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["startSceneId"] = json.loads((keep[0] / "scene.gbsres").read_text(encoding="utf-8"))["id"]
    settings["startX"], settings["startY"] = start
    write_json(settings_path, settings)
    return emptied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="sample_town", help="scene folder name in gbs2")
    parser.add_argument("--start", type=int, nargs=2, default=(12, 20), help="player start tile")
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    args.out.mkdir(parents=True)
    emptied = derive(REFERENCE / "appData/templates/gbs2", args.out / "project", args.scene,
                     tuple(args.start), fade=False)
    manifest = export_project(args.out / "project", args.out / "export")
    if not manifest["scene_engine"]["built"]:
        raise SystemExit(manifest["scene_engine"]["reason"])
    build = build_rom(args.out / "export", args.toolchain)
    project = load_project(args.out / "project")
    scene = next(s for s in project.scenes if s["id"] == project.settings["startSceneId"])
    engine = build_engine_scene(project, scene)
    width, height, pixels = background_pixels(project, scene)
    frames = engine_frames(engine, args.ticks + 10)
    write_new(args.out / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
    priority = {}
    if manifest["scene_engine"].get("background_priority", {}).get("status") == "emitted_dynamic":
        source = engine.background_source
        mask = bytes(bool(source.attributes[(y//8)*(width//8)+x//8]&128) and bool(source.indices[y*width+x])
                     for y in range(height) for x in range(width))
        write_new(args.out / "scene.gb-priority", mask)
        priority["gb_priority_mask"] = "scene.gb-priority"
    rom_name = f"ngpcraft_m2_gbs_{args.scene}.ngc"
    write_new(args.out / rom_name, (args.out / "export/rom/game.ngc").read_bytes())
    write_new(args.out / "source.json", json.dumps({
        **priority, "scene": args.scene, "size_px": [width, height], "loop": False, "probe_frames": args.ticks,
        "step_from_game_time": True, "view": list(view_of(engine.background))[::-1],
        "expected_scroll": [list(p) for p, _ in frames], "actors": [d for _, d in frames],
        "project": str(args.out / "project"), "default_sprite_mode": project.settings["spriteMode"],
        "sprite_palettes": [list(p) for p in sprite_palettes(project, scene)],
        "emptied_scripts": emptied, "oracle": "tools/gbvm_actors.engine_frames"}, indent=1).encode("utf-8"))
    print(json.dumps({"rom": str(args.out / rom_name), "bytes": build["header"]["size_bytes"],
                      "actors": list(engine.names), "emptied_scripts": len(emptied)}, indent=2))


if __name__ == "__main__":
    main()
