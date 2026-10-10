"""Scene stack proof (EVENT_SCENE_PUSH_STATE, EVENT_SCENE_POP_STATE): pixel exact.

The two-scene copy of gbs2 of tools/demo_scene_switch.py, with:
  Sample Town On Init: VAR_0 += 1, wait W frames, Push Scene State,
                       Switch Scene -> Cave (9, 12, up)
  Cave On Init:        VAR_1 += 1, wait W frames, Pop Scene State (fade)
Each pop must bring the player back to Sample Town where and as it was when
pushed (start tile, start direction), not where the Cave left it: a missing
restore shows another picture. The probe is tools/probe_scene_switch.py
(oracle tools/gbvm_scenes.py with op_push / op_pop).

  python -B -m tools.demo_scene_stack --out <new dir> --toolchain C:\\t900
"""
import argparse
import hashlib
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new
from tools.demo_scene_switch import (CAVE, CAVE_ARRIVAL, FADE_TO_CAVE, GBS2, TOWN, TOWN_START, derive_scenes, ev,
                                     num, set_script, switch, write_case)
from tools.gbvm_actors import op_idle, op_wait, plain
from tools.gbvm_scenes import op_pop, op_push, op_switch


def project_case(root, wait=60):
    keep, ids = derive_scenes(GBS2, root / "project", [TOWN, CAVE], TOWN, TOWN_START)
    set_script(keep[TOWN], [ev("t-count", "EVENT_INC_VALUE", variable="0"),
                            ev("t-wait", "EVENT_WAIT", units="frames", frames=num(wait)),
                            ev("t-push", "EVENT_SCENE_PUSH_STATE"),
                            switch("t-go", ids[CAVE], CAVE_ARRIVAL, FADE_TO_CAVE)])
    set_script(keep[CAVE], [ev("c-count", "EVENT_INC_VALUE", variable="1"),
                            ev("c-wait", "EVENT_WAIT", units="frames", frames=num(wait)),
                            ev("c-pop", "EVENT_SCENE_POP_STATE", fadeSpeed="2")])
    return root / "project", ids


def scripts_for(wait):
    """Init threads as compiled: VM_LOCK, VM_RESERVE, VM_SET_SPRITE_MODE, VM_RPN
    (+1); VM_IDLE (automatic fade in); fade speed, VM_FADE_IN, wait arguments;
    VM_INVOKE wait_frames. Town: VM_SCENE_PUSH, then the switch (fade speed,
    VM_FADE_OUT, RPN, .LOCAL_ACTOR, SET_POS, SET_DIR, camera_settings, raise).
    Cave: fade speed, VM_FADE_OUT, camera_settings, VM_SCENE_POP."""
    stack = []
    direction = {"down": 0, "right": 1, "up": 2, "left": 3}[CAVE_ARRIVAL[2]]

    def scripts(town, cave):
        head = lambda: plain(4) + [op_idle()] + plain(3) + [op_wait(wait)]
        return {town: lambda: head() + [op_push(stack, town)] + plain(7)
                + [op_switch(cave, CAVE_ARRIVAL[0], CAVE_ARRIVAL[1], direction)],
                cave: lambda: head() + plain(3) + [op_pop(stack)]}
    return scripts


def build_case(root, toolchain, wait=60, ticks=400, mutate=None):
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
    write_case(root, project, manifest, ids, wait, ticks, scripts_for(wait))
    return manifest, build


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--wait", type=int, default=60)
    parser.add_argument("--ticks", type=int, default=400)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    args.out.mkdir(parents=True)
    manifest, build = build_case(args.out, args.toolchain, args.wait, args.ticks)
    rom = args.out / f"ngpcraft_m2_scene_stack_{args.wait}f.ngc"
    write_new(rom, (args.out / "export/rom/game.ngc").read_bytes())
    print(json.dumps({"rom": str(rom), "bytes": build["header"]["size_bytes"],
                      "sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
                      "features": manifest["features"]}, indent=1))


if __name__ == "__main__":
    main()
