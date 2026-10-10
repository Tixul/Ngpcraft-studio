"""EVENT_ADVENTURE_STATE_SET in a native ROM.

Adventure Sample Town (tools/genre_oracle.project_case), the right of the
D-pad held all along. The scene script (unlocked, as an init script waiting
would otherwise lock the state update, core.c) waits, sets BLANK, waits, sets
GROUND. Expected from adventure.c: the player walks right; once adv_state is
BLANK (3) the velocity is zeroed and x stays fixed (blank has no update);
back in GROUND (0) it walks again. Witness (--witness): no state change, x
never stops.
"""
import argparse
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.genre_oracle import RIGHT, project_case as genre_project, rom_trace

FRAMES = 150


def project_case(root, witness=False):
    project, _ = genre_project(root, "adventure", keep_actors=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf8"))
    wait = lambda key, n: ev(key, "EVENT_WAIT", units="frames", frames=num(n))
    state = lambda key, s: [] if witness else [ev(key, "EVENT_ADVENTURE_STATE_SET", state=s)]
    scene["script"] = [ev("unlock", "EVENT_SCRIPT_UNLOCK"), wait("w1", 20), *state("blank", "blank"),
                       wait("w2", 40), *state("ground", "ground")]
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project = project_case(out, witness)
    export_project(project, out / "export")
    build_rom(out / "export", Path(toolchain))
    rows = rom_trace(out / "export", emulator, bios, FRAMES, [(1, FRAMES + 1, RIGHT)], kind="adventure",
                     variable_symbols={})
    blank = [k for k, r in enumerate(rows) if r["adv_state"] == 3]
    xs = [r["x"] for r in rows]
    report = {"schema": "ngpcraft-adventure-state-lab/1", "witness": witness,
              "blank_ticks": [blank[0], blank[-1]] if blank else None,
              "x_during_blank": sorted({xs[k] for k in blank}),
              "vel_during_blank": sorted({rows[k]["adv_vel_x"] for k in blank}),
              "x_before": xs[blank[0] - 1] if blank else None, "x_end": xs[-1],
              "states": sorted({r["adv_state"] for r in rows})}
    if witness:
        report["passed"] = not blank and xs[-1] > xs[60]
    else:
        report["passed"] = (bool(blank) and len(report["x_during_blank"]) == 1
                            and report["vel_during_blank"] == [0] and rows[-1]["adv_state"] == 0
                            and xs[-1] > xs[blank[-1]] and report["x_before"] < report["x_during_blank"][0] + 64)
    write_json(out / "comparison.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--witness", action="store_true")
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.witness)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
