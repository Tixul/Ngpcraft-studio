"""Adventure scene type (GBVM states/adventure.c) in native ROMs, every tick
against the original C (tools/genre_oracle.py, kind "adventure").

Sample Town, actors removed unless a case adds some; counters for script
starts. Compared each tick: player position, camera, adv_state, adv_vel_x/y.

  python -B -m tools.lab_adventure --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> --clang <clang.exe> [--case NAME]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from tools.demo_gbs_scene import write_json
from tools.genre_oracle import A, B, DOWN, LEFT, RIGHT, UP, run

MOVES = [(10, 40, UP | LEFT), (50, 80, DOWN | RIGHT), (90, 120, UP | RIGHT), (130, 160, DOWN | LEFT),
         (170, 200, LEFT), (210, 240, RIGHT), (250, 280, UP), (290, 320, DOWN)]
# Overlapping presses: the most recent direction (input.c recent_joy) wins in 4-way.
OVERLAP = [(10, 60, RIGHT), (30, 90, DOWN), (70, 120, LEFT), (100, 140, UP), (150, 200, RIGHT | UP)]
BASE = dict(kind="adventure", keep_actors=False)
CASES = {
    "adv-8way": dict(BASE, frames=340, schedule=MOVES),
    "adv-4way": dict(BASE, frames=220, schedule=OVERLAP + MOVES[:2],
                     fields={"INPUT_ADVENTURE_MOVE_TYPE": "MOVE_TYPE_4_WAY"}),
    "adv-horizontal": dict(BASE, frames=340, schedule=MOVES,
                           fields={"INPUT_ADVENTURE_DIRECTION_TYPE": "DIRECTION_TYPE_HORIZONTAL"}),
    "adv-4way-horizontal": dict(BASE, frames=220, schedule=OVERLAP,
                                fields={"INPUT_ADVENTURE_MOVE_TYPE": "MOVE_TYPE_4_WAY",
                                        "INPUT_ADVENTURE_DIRECTION_TYPE": "DIRECTION_TYPE_HORIZONTAL"}),
    "adv-run": dict(BASE, frames=200, schedule=[(10, 90, RIGHT), (30, 70, B), (100, 180, DOWN | LEFT), (120, 190, B)],
                    fields={"FEAT_ADVENTURE_RUN": 1}),
    "adv-dash-a": dict(BASE, frames=160, schedule=[(10, 30, RIGHT), (20, 21, A), (60, 80, DOWN), (70, 71, A),
                                                   (90, 91, A), (120, 140, LEFT), (130, 131, A)],
                       fields={"FEAT_ADVENTURE_DASH": 1, "INPUT_ADVENTURE_DASH": "DASH_INPUT_A"}),
    "adv-dash-double-tap": dict(BASE, frames=140, schedule=[(10, 14, LEFT), (18, 30, LEFT), (60, 63, UP), (66, 80, UP)],
                                fields={"FEAT_ADVENTURE_DASH": 1}),
    "adv-dash-through-walls": dict(BASE, frames=120, schedule=[(10, 20, RIGHT), (14, 15, A)],
                                   walls=[(18, y, 15) for y in range(17, 24)],
                                   fields={"FEAT_ADVENTURE_DASH": 1, "INPUT_ADVENTURE_DASH": "DASH_INPUT_A",
                                           "adv_dash_mask": 15}),
    "adv-dash-blocked": dict(BASE, frames=120, schedule=[(10, 20, RIGHT), (14, 15, A)],
                             walls=[(18, y, 15) for y in range(17, 24)],
                             fields={"FEAT_ADVENTURE_DASH": 1, "INPUT_ADVENTURE_DASH": "DASH_INPUT_A",
                                     "adv_dash_dist": 4096}),
    "adv-push": dict(BASE, frames=140, schedule=[(10, 120, LEFT), (125, 135, RIGHT)],
                     fields={"FEAT_ADVENTURE_PUSH": 1}),
    "adv-slopes": dict(BASE, frames=200, schedule=[(10, 60, RIGHT), (70, 100, UP), (110, 160, LEFT), (165, 190, DOWN)],
                       walls=[(16, 20, 21), (17, 20, 25), (16, 19, 22), (14, 21, 26)]),
    "adv-trigger": dict(BASE, frames=160, schedule=[(10, 60, RIGHT), (80, 130, LEFT)],
                        triggers=[(16, 19, 2, 3, "1", "2")]),
    "adv-interact": dict(BASE, frames=80, schedule=[(10, 12, RIGHT), (20, 21, A), (40, 41, A), (50, 52, LEFT),
                                                    (60, 61, A)],
                         actors=[(112, 160, "", "3")]),
    "adv-actor-group": dict(BASE, frames=120, player_hits=True, schedule=[(10, 60, RIGHT)],
                            actors=[(112, 160, "1", "3")]),
}


def run_case(out, name, toolchain, emulator, bios, clang, mutate=None):
    case = dict(CASES[name])
    case.pop("kind")
    return run(Path(out) / name, "adventure", toolchain, emulator, bios, clang, mutate=mutate, **case)


def double_step(export):
    """Witness: the export's velocity-to-subpixel step doubled (the reference
    keeps GBVM's adventure.c), the export manifest updated as for a real export."""
    path = export / "src/scene_actors.c"
    text = path.read_text(encoding="utf-8")
    old = "((v) >> 8)) << 1)"
    if text.count(old) != 1:
        raise ValueError("witness: VEL_TO_SUBPX not found")
    path.write_text(text.replace(old, "((v) >> 8)) << 2)"), encoding="utf-8")
    data = json.loads((export / "export.json").read_text(encoding="utf-8"))
    data["sha256"]["src/scene_actors.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(export / "export.json", data)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    for option in ("toolchain", "emulator", "bios"):
        p.add_argument("--" + option, type=Path, required=True)
    p.add_argument("--clang", default=os.environ.get("NGC_TEST_CLANG"))
    p.add_argument("--case", action="append", choices=sorted(CASES))
    p.add_argument("--witness", action="store_true")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    results = {}
    for name in args.case or sorted(CASES):
        r = run_case(args.out, name, args.toolchain, args.emulator, args.bios, args.clang,
                     mutate=double_step if args.witness else None)
        results[name] = {k: r[k] for k in ("ticks", "mismatches", "first", "script_starts", "seen",
                                           "counter_sequence_matches", "delays", "passed")}
        print(name, results[name]["passed"], results[name]["mismatches"], results[name]["script_starts"], flush=True)
    write_json(args.out / "summary.json", results)
    return 0 if all(r["passed"] for r in results.values()) != args.witness else 1


if __name__ == "__main__":
    raise SystemExit(main())
