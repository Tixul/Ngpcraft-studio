"""Cost of each kind of actor in the exported engine, cycles per frame.

The R-TD fixture (`tools.lab_rtd.project_fixture`, unchanged) is cut into
configurations that differ by one kind of actor; each is exported, built and
profiled from outside the ROM by `tools.profile_tick` on the same input
schedule as R-TD. A kind's cost is the difference of median work between two
configurations, divided by the number of actors added:

  moving visible       OnUpdate Move To loop, on screen (slope over 0/1/5/10)
  static visible       no script, on screen
  offscreen            no script, outside the screen: GB Studio deactivates it
  offscreen persistent the same, "Keep Running While Offscreen"

Ordinary GB Studio resources and events only; no profiling code in the ROM.

  python -B tools/lab_cost_table.py --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> [--frames N]
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
from tools.lab_rtd import project_fixture  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.profile_tick import measure  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402

# name: (R-TD fixture size, actors kept, scripts kept, offscreen persistent)
CONFIGS = {
    "player_only": (10, 0, True, False),
    "moving_1": (10, 1, True, False),
    "moving_5": (10, 5, True, False),
    "moving_10": (10, 10, True, False),
    "static_10": (10, 10, False, False),
    "moving_10_offscreen_20": (30, 30, True, False),
    "moving_10_offscreen_20_persistent": (30, 30, True, True),
}


def schedule():
    # tools.lab_rtd.build: small loop around three camera positions.
    rows = []
    for first in range(40, 2000, 128):
        for offset, mask in ((0, 8), (24, 2), (48, 4), (72, 1)):
            rows.append([first + offset, first + offset + 16, mask])
    return rows


def fixture(root, name):
    size, kept, scripts, persistent = CONFIGS[name]
    project = project_fixture(root, size)
    folder = next(p.parent for p in (project / "project/scenes").rglob("scene.gbsres"))
    for path in sorted(folder.glob("actors/rtd_actor_*.gbsres")):
        k = int(path.stem.rsplit("_", 1)[1])
        if k >= kept:
            path.unlink()
            continue
        actor = json.loads(path.read_text(encoding="utf-8"))
        if not scripts:
            actor["updateScript"] = []
        if persistent and k >= 10:
            actor["persistent"] = True
        write_json(path, actor)
    return project


WITNESS_ANCHOR = "    unsigned short cam;\n"
# Profiler witness: known extra work in one module, as compiled source only.
WITNESS_LOOP = "    unsigned short cam;\n    { volatile unsigned short w; for (w = 0u; w < 400u; ++w) ; }\n"


def build_config(root, name, toolchain, witness=False):
    """Export and build one configuration in `root` (new); returns the export dir."""
    root.mkdir(parents=True)
    project = fixture(root / "fixture", name)
    manifest = export_project(project, root / "export")
    if witness:
        path = root / "export/src/scene_actors.c"
        source = path.read_text(encoding="utf-8")
        start = source.index("void ngc_camera_update(void)")
        assert source.count(WITNESS_ANCHOR, start) >= 1
        at = source.index(WITNESS_ANCHOR, start)
        source = source[:at] + WITNESS_LOOP + source[at + len(WITNESS_ANCHOR):]
        temp = path.with_suffix(".tmp")
        temp.write_text(source, encoding="utf-8")
        temp.replace(path)
        manifest["sha256"]["src/scene_actors.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(root / "export/export.json", manifest)
    build_rom(root / "export", Path(toolchain))
    return root / "export"


def run(out, toolchain, emulator, bios, frames):
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    write_json(out / "inputs.json", schedule())
    results = {}
    for name in CONFIGS:
        root = out / name
        export = build_config(root, name, toolchain)
        profile = measure(export, emulator, bios, frames, 2, schedule())
        profile.pop("rows")
        write_json(root / "profile.json", profile)
        results[name] = profile
        print(name, profile["work"]["median"], profile["late_ticks"], flush=True)
    return results


def table(results):
    work = {n: r["work"]["median"] for n, r in results.items()}
    points = [(0, work["player_only"]), (1, work["moving_1"]), (5, work["moving_5"]), (10, work["moving_10"])]
    mx = sum(x for x, _ in points) / len(points)
    my = sum(y for _, y in points) / len(points)
    slope = sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x, _ in points)
    kinds = {
        "moving_visible": round(slope),
        "static_visible": round((work["static_10"] - work["player_only"]) / 10),
        "offscreen": round((work["moving_10_offscreen_20"] - work["moving_10"]) / 20),
        "offscreen_persistent": round((work["moving_10_offscreen_20_persistent"] - work["moving_10"]) / 20),
    }
    modules = {}
    for module in results["player_only"]["modules"]:
        modules[module] = {n: r["modules"][module]["median"] for n, r in results.items()}
    return {"base_work": work["player_only"], "per_actor": kinds, "work": work,
            "max_work": {n: r["work"]["max"] for n, r in results.items()},
            "late_ticks": {n: r["late_ticks"] for n, r in results.items()},
            "modules": modules, "frame_cycles": results["player_only"]["frame_cycles"]}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--frames", type=int, default=300)
    args = p.parse_args()
    results = run(args.out, args.toolchain, args.emulator, args.bios, args.frames)
    summary = table(results)
    write_json(args.out / "cost_table.json", summary)
    print(json.dumps({k: summary[k] for k in ("base_work", "per_actor", "late_ticks")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
