"""M2 scene transitions at 60/30: source pixels, variables and scene stack.

Builds new fixtures from the released gbs2. With --fades, replays the existing
scene-switch fixture; otherwise removes automatic and scripted fades only
in the disposable fixture. The independent GBVM scene oracle predicts the
load ticks and pictures. No measurement code is added to the ROM.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.demo_scene_switch import (CAVE, CAVE_ARRIVAL, TOWN, TOWN_ARRIVAL,
                                    project_case as switches, write_case)  # noqa: E402
from tools.gbvm_actors import op_wait, plain  # noqa: E402
from tools.gbvm_scenes import op_pop, op_push, op_switch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def cut_scripts(wait, stack=False):
    """No auto fade: LOCK, RESERVE, sprite mode, increment, wait value,
    INVOKE. Switch: RPN, actor local, position, direction, camera, RAISE.
    Pop: camera, SCENE_POP. Each plain entry is one GBVM instruction."""
    def scripts(town, cave):
        saved = []
        head = lambda: plain(5) + [op_wait(wait)]
        cave_dir = {"down": 0, "right": 1, "up": 2, "left": 3}[CAVE_ARRIVAL[2]]
        town_dir = {"down": 0, "right": 1, "up": 2, "left": 3}[TOWN_ARRIVAL[2]]
        return {town: lambda: head() + ([op_push(saved, town)] if stack else []) + plain(5)
                + [op_switch(cave, *CAVE_ARRIVAL[:2], cave_dir)],
                cave: (lambda: head() + plain(1) + [op_pop(saved)]) if stack else
                (lambda: head() + plain(5) + [op_switch(town, *TOWN_ARRIVAL[:2], town_dir)])}
    return scripts


def project_case(root, wait=36, rate=60, stack=False, fades=False):
    if stack:
        from tools.demo_scene_stack import project_case as pushes
        project, ids = pushes(root, wait)
    else:
        project, ids = switches(root, wait)
    if not fades:
        for path in (project / "project/scenes").rglob("scene.gbsres"):
            scene = json.loads(path.read_text(encoding="utf-8"))
            scene["autoFadeSpeed"] = None
            for event in scene["script"]:
                if event["command"] in ("EVENT_SWITCH_SCENE", "EVENT_SCENE_POP_STATE"):
                    event["args"]["fadeSpeed"] = "0"
            write_json(path, scene)
    if rate != 60:
        write_json(project / "ngpcraft.json", {"schema": "ngpcraft-project/1",
                                               "ngpc": {"frame_rate": rate}, "resources": {}})
    return project, ids


def build_case(root, toolchain, wait=36, ticks=180, rate=60, stack=False, fades=False, witness=None):
    project, ids = project_case(root, wait, rate, stack, fades)
    manifest = export_project(project, root / "export")
    if witness:
        if witness == "palette":
            rel, old, new = "src/game.c", "    ngc_palette_show();", "    /* witness: palette publication omitted */"
        elif witness == "restore":
            rel, old, new = "src/scene_actors.c", "act_x[0] = stack_x[stack_n]; act_y[0] = stack_y[stack_n];", ";"
        elif witness == "logic":
            rel, old, new = "src/game.c", "    ++ngc_game_time;", "    /* witness: logical clock stopped */"
        else:
            raise ValueError("unknown witness")
        path = root / "export" / rel
        source = path.read_text(encoding="utf-8")
        assert source.count(old) == (2 if witness == "logic" and rate == 30 else 1)
        source = source.replace(old, new, 1)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(source.encode("utf-8"))
        temp.replace(path)
        manifest["sha256"][rel] = hashlib.sha256(source.encode("utf-8")).hexdigest()
        write_json(root / "export/export.json", manifest)
    build = build_rom(root / "export", toolchain)
    oracle = cut_scripts(wait, stack) if not fades else None
    if fades and stack:
        from tools.demo_scene_stack import scripts_for
        oracle = scripts_for(wait)
    write_case(root, project, manifest, ids, wait, ticks, oracle)
    return manifest, build


def observe(root, emulator, bios):
    from tools.probe_scene_switch import observe as pixels
    report, pictures = pixels(root, emulator, bios)
    source = json.loads((root / "source.json").read_text(encoding="utf-8"))
    rate = source["tick_frames"]
    captures = [(t + 20 + rate - 1) // rate for t, _, _, _ in source["loads"][:4]]
    subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(root / "export"),
                    "--emulator", str(emulator), "--bios", str(bios), "--out", str(root / "variables"),
                    "--frames", str(source["probe_frames"]), "--frame-timing",
                    "--shots", ",".join(map(str, captures))],
                   cwd=ROOT, check=True, capture_output=True, timeout=900)
    observed = json.loads((root / "variables/probe.json").read_text(encoding="utf-8"))
    loads = source["loads"]
    town = loads[0][1]
    expected = {}
    for key, scene in (("VAR_QUEST1", town), ("VAR_QUEST2", 1 - town)):
        ticks = [max(t, 1) for t, k, _, _ in loads if k == scene and max(t, 1) <= source["probe_frames"] * rate]
        expected[key] = [{"frame": 0, "value": 0}] + [
            {"frame": (t + rate - 1) // rate, "value": i + 1} for i, t in enumerate(ticks)]
    report["capture_frames"] = captures
    report["variables_expected"] = expected
    report["variables_observed"] = observed["changes"]
    report["variables_passed"] = observed["changes"] == expected
    # At entry, the framebuffer is one published tick behind the absolute step.
    # The interval measured from here runs the NEXT logical frames: exclude
    # only intervals which actually execute a predicted (blocking) load.
    load_ticks = [t for t, _, _, _ in loads if t]
    loading = lambda row: any(row["step"] + rate < t <= row["step"] + 2 * rate
                              or (source["fade_enabled"] and row["step"] + rate == t)
                              for t in load_ticks)
    # With automatic fades, a load at the last logical frame queues the
    # new init thread; its modal fade-in runs at the next tick's entry.
    # Count that explicit transition separately, never a stable game tick.
    report["load_intervals"] = [{"step": r["step"], "physical_frames": r["frames_between"]}
                                for r in report["rows"] if loading(r)]
    report["stable_cadence_errors"] = [r["step"] for r in report["rows"]
                                       if not loading(r) and r["frames_between"] != rate]
    report["accepted"] = report["passed"] and report["variables_passed"] and not report["stable_cadence_errors"]
    write_json(root / "comparison.json", report)
    from tools.probe_rom import write_frame
    shots = root / "pictures"
    shots.mkdir()
    for row, picture in zip(report["rows"], pictures):
        if row.get("mismatch") or row["step"] in (20, 50, 90, 130):
            write_frame(shots / f"step-{row['step']:04d}.ppm", picture)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--frame-rate", type=int, choices=(60, 30), default=60)
    p.add_argument("--stack", action="store_true")
    p.add_argument("--fades", action="store_true")
    p.add_argument("--wait", type=int, default=36)
    p.add_argument("--ticks", type=int, default=180)
    p.add_argument("--witness", choices=("palette", "restore", "logic"))
    a = p.parse_args()
    if a.out.exists():
        p.error("output must be new")
    if a.wait < 5 or a.ticks < 140:
        p.error("wait must be at least 5 and ticks at least 140")
    a.out.mkdir(parents=True)
    build_case(a.out, a.toolchain, a.wait, a.ticks, a.frame_rate, a.stack, a.fades, a.witness)
    report = observe(a.out, a.emulator, a.bios)
    print(json.dumps({k: report[k] for k in ("accepted", "frames_with_mismatch", "variables_passed",
                                           "stable_cadence_errors", "load_ticks_seen")}, indent=1))
    return 0 if (not report["accepted"] if a.witness else report["accepted"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
