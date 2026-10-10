"""EVENT_SET / REMOVE_ADVENTURE_CALLBACK_SCRIPT in a native ROM.

Adventure Sample Town (tools/genre_oracle.project_case). The scene script
(unlocked) attaches blankStart (V30 += 1) and groundStart (V31 += 1), then
sets BLANK, GROUND, removes blankStart, sets BLANK again and GROUND. Expected
from adventure.c (callbacks run on state entry, adv_callback_execute):
V30 = 1 (the second BLANK entry has no script any more), V31 = 2 (two GROUND
entries; adventure_init enters GROUND without the callback). Witness
(--witness): nothing attached, both 0.
"""
import argparse
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.genre_oracle import project_case as genre_project, rom_trace

FRAMES = 120
VARS = ("30", "31", "40")


def project_case(root, witness=False):
    project, _ = genre_project(root, "adventure", keep_actors=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf8"))
    wait = lambda key, n: ev(key, "EVENT_WAIT", units="frames", frames=num(n))
    state = lambda key, s: ev(key, "EVENT_ADVENTURE_STATE_SET", state=s)
    inc = lambda key, var: ev(key, "EVENT_INC_VALUE", variable=var)

    def attach(key, event, var):
        return [] if witness else [{**ev(key, "EVENT_SET_ADVENTURE_CALLBACK_SCRIPT", event=event),
                                    "children": {"script": [inc(key + "-inc", var)]}}]
    remove = [] if witness else [ev("remove", "EVENT_REMOVE_ADVENTURE_CALLBACK_SCRIPT", event="blankStart")]
    scene["script"] = [ev("unlock", "EVENT_SCRIPT_UNLOCK"), *attach("cb-blank", "blankStart", "30"),
                       *attach("cb-ground", "groundStart", "31"), wait("w1", 10), state("s1", "blank"),
                       wait("w2", 10), state("s2", "ground"), wait("w3", 10), *remove, state("s3", "blank"),
                       wait("w4", 10), state("s4", "ground"), wait("w5", 10),
                       ev("done", "EVENT_SET_VALUE", variable="40", value=num(1))]
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    project = project_case(out, witness)
    manifest = export_project(project, out / "export")
    build_rom(out / "export", Path(toolchain))
    # Variables the export allocates (the witness never names 30 / 31): absent = 0.
    present = [v for v in VARS if f"VAR_VARIABLE_{v}" in manifest["variables"]]
    rows = rom_trace(out / "export", emulator, bios, FRAMES, [], present, kind="adventure",
                     variable_symbols={v: f"VAR_VARIABLE_{v}" for v in present})
    final = {v: rows[-1]["vars"][v] if v in present else 0 for v in VARS}
    states = [r["adv_state"] for r in rows]
    entries = sum(1 for a, b in zip(states, states[1:]) if a != b)
    report = {"schema": "ngpcraft-adventure-callbacks-lab/1", "witness": witness, "final": final,
              "state_changes": entries, "features": manifest["features"]}
    expected = {"30": 0, "31": 0, "40": 1} if witness else {"30": 1, "31": 2, "40": 1}
    report["expected"] = expected
    report["passed"] = final == expected and entries == 4
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
