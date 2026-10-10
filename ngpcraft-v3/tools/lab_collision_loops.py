"""While / For loops in a collision script, in a native ROM.

tools/demo_player_collisions.project_case: the player walks right into the
"angry" actor (collision group 1). Its collision script disables its own
collisions (one contact), then runs While (V40 < 3) { V40 += 1; wait 2 frames }
and For (V41 = 0; V41 < 4; V41 += 1) { V42 += 1 }, then sets V43 = 1.
The script has no static lifetime: the export warns and sizes the context
pool to the largest the RAM allows (scene_collisions.UNBOUNDED_CONTEXTS,
emit.fit_contexts). Expected (GB Studio semantics): V40 = 3, V42 = 4, V43 = 1.
Witness (--witness): the same script without its loops, V40 = V42 = 0.
"""
import argparse
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ANGRY, ev, num
from tools.demo_gbs_scene import write_json
from tools.demo_player_collisions import SCHEDULE, project_case as collision_project
from tools.genre_oracle import rom_trace

VARS = ("40", "42", "43")


def value(kind, a, b):
    return {"type": kind, "valueA": a, "valueB": b}


def var(v):
    return {"type": "variable", "value": v}


def project_case(root, witness=False):
    project = collision_project(root, repeat=True)
    path = next(p for p in (project / "project/scenes").rglob("actors/*.gbsres")
                if json.loads(p.read_text())["id"] == ANGRY)
    actor = json.loads(path.read_text())
    body = [ev("inc-40", "EVENT_INC_VALUE", variable="40"), ev("w", "EVENT_WAIT", units="frames", frames=num(2))]
    loops = [] if witness else [
        {**ev("while", "EVENT_LOOP_WHILE", condition=value("lt", var("40"), num(3))), "children": {"true": body}},
        {**ev("for", "EVENT_LOOP_FOR", variable="41", **{"from": num(0)}, comparison="<", to=num(4),
              operation="+=", value=num(1)),
         "children": {"true": [ev("inc-42", "EVENT_INC_VALUE", variable="42")]}}]
    actor["script"] = [ev("off", "EVENT_ACTOR_COLLISIONS_DISABLE", actorId="$self$"), *loops,
                       ev("done", "EVENT_SET_VALUE", variable="43", value=num(1))]
    write_json(path, actor)
    return project


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project = project_case(out, witness)
    manifest = export_project(project, out / "export")
    build_rom(out / "export", Path(toolchain))
    present = [v for v in VARS if f"VAR_VARIABLE_{v}" in manifest["variables"]]
    rows = rom_trace(out / "export", emulator, bios, 400, SCHEDULE, present, kind="topdown",
                     variable_symbols={v: f"VAR_VARIABLE_{v}" for v in present})
    final = {v: rows[-1]["vars"][v] if v in present else 0 for v in VARS}
    warned = [w for w in manifest["warnings"] if "unbounded" in w]
    budget = manifest["scene_engine"].get("context_budget")
    report = {"schema": "ngpcraft-collision-loops-lab/1", "witness": witness, "final": final,
              "warnings": warned, "context_budget": budget}
    if witness:
        report["passed"] = final == {"40": 0, "42": 0, "43": 1} and not warned
    else:
        report["passed"] = (final == {"40": 3, "42": 4, "43": 1} and len(warned) == 1
                            and budget is not None and budget["contexts"] >= 16)
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
