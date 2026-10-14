"""ngpc.engine_speedups on against off: the same game, image for image (docs/ETUDE_CADENCE.md).

One project (copied, never modified) is exported twice per scene measured, the
scene as start scene: engine_speedups true (the default) and false (the engine
as before the frame-rate work). Both ROMs run on the native core (silicon
timing, noisy power-on RAM) with the same held buttons; every game tick compares
the screen, every GBVM variable and the scene (tools/lab_sprite_stream.compare:
buttons also written to the BIOS copy 0x6F82, VBlank clock copied from the
reference, so that a faster ROM plays the same game). Any difference is counted
and the first ones saved.

  python -B -m tools.lab_speedups --project <dir> --out <new dir> --scene NAME[=inputs.json] [...]
         [--ticks 900] --toolchain C:/t900 --emulator <Ngpcraft_emulator> --bios <bios>
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.lab_sprite_stream import compare  # noqa: E402


def set_speedups(project, value):
    path = project / "ngpcraft.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema": "ngpcraft-project/1"}
    data.setdefault("ngpc", {})["engine_speedups"] = value
    write_json(path, data)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("project", "out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--scene", action="append", required=True)
    p.add_argument("--ticks", type=int, default=900)
    p.add_argument("--inputs", type=Path)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out}: output must be new")
    default_inputs = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else []
    project = a.out / "project"
    shutil.copytree(a.project, project, ignore=shutil.ignore_patterns("build", "build_export"))
    settings_path = project / "project/settings.gbsres"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    ids = {}
    for scene in (project / "project/scenes").rglob("*.gbsres"):
        data = json.loads(scene.read_text(encoding="utf-8"))
        if data.get("_resourceType") == "scene":
            ids[data["name"]] = data["id"]
    result = {"schema": "ngpcraft-speedups-lab/1", "project": str(a.project), "ticks": a.ticks,
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False, "scenes": {}}
    for n, name in enumerate(a.scene):
        inputs = default_inputs
        if "=" in name:
            name, own = name.rsplit("=", 1)
            inputs = json.loads(Path(own).read_text(encoding="utf-8"))
        if name not in ids:
            raise SystemExit(f"unknown scene {name!r}; known: {sorted(ids)}")
        settings["startSceneId"] = ids[name]
        write_json(settings_path, settings)
        exports = {}
        for value in (False, True):
            set_speedups(project, value)
            exports[value] = a.out / f"scene{n}/{'on' if value else 'off'}/export"
            export_project(project, exports[value])
            build_rom(exports[value], a.toolchain)
        seen = compare(exports[False], exports[True], a.ticks, inputs, a.out / f"scene{n}/shots",
                       a.emulator, a.bios)
        result["scenes"][name] = seen
        write_json(a.out / "result.json", result)
        work = seen.get("work_cycles", {})
        print(name, {"identical": seen["identical"], "differing_ticks": seen["differing_ticks"],
                     "median_off": work.get("resident", {}).get("median"),
                     "median_on": work.get("streamed", {}).get("median")}, flush=True)


if __name__ == "__main__":
    main()
