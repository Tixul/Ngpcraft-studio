"""Actor movement events of GB Studio (Move To, Move Relative) on the scene
engine: a proof ROM and its oracle.

sample_town (derived copy of gbs2): a scene script of movement groups, each
preceded by a marker variable. The oracle runs the same movements as GBVM's
vm_actor.c does (tools/gbvm_actors.py Movement), inside a model of the
scheduler (ScriptThread: one waitable instruction a frame, PC rewound while
not arrived, VM locked until the init thread ends); it PREDICTS the tick of
every marker. The markers measured on the VM (tools/probe_gbvm.py) must be
those ticks, then the probe compares every frame.

The plan (PLAN) is written once; its GB Studio events and its expected GBVM
instruction sequence (sequence(), from _actorMoveToOps) are derived separately.

Console version (--wait N): the same plan inside EVENT_LOOP, N frames of rest
after each movement, then the three actors put back where they started.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import derive, write_json
from tools.gbvm_actors import (ATTR_ACTORS, ATTR_DIAGONAL, ATTR_H_FIRST, ATTR_SNAP_PX, ATTR_SNAP_TILE,
                               ATTR_WALLS, ScriptThread, engine_frames, move_ops, op_jump, op_marker,
                               op_set_dir, op_set_pos, op_speed, op_wait, plain)
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
GBS2 = REFERENCE / "appData/templates/gbs2"
ANGRY, OBLIVIOUS = "a85c4ba5-9bc0-4daa-94bc-4ef68a690659", "5cf6eb6c-be62-4efc-9cbb-14a8efac21a8"
PLAYER_START = (12, 20)
DIRECTIONS = ("down", "right", "up", "left")

# (event, actor, x, y, units, collideWith, moveType, lockDirection) or ("speed", actor, px per frame)
PLAN = [
    # 1. left into the wall of column 9: stops at x = 10 (check_collision_horizontal)
    ("EVENT_ACTOR_MOVE_TO", "player", 5, 20, "tiles", ["walls"], "horizontal", []),
    # 2. vertical first, blocked by the wall below (the move only yields), then right
    ("EVENT_ACTOR_MOVE_RELATIVE", "player", 4, 3, "tiles", ["walls"], "vertical", []),
    # 3. up three tiles, then down onto the duck: stopped by actor_overlapping_bb,
    #    one step back (vm_actor_move_to_y)
    ("EVENT_ACTOR_MOVE_TO", OBLIVIOUS, 18, 13, "tiles", [], "vertical", []),
    ("EVENT_ACTOR_MOVE_TO", OBLIVIOUS, 18, 22, "tiles", ["actors"], "horizontal", []),
    # 4. diagonal, relative in pixels, direction set from x (lock on y)
    ("EVENT_ACTOR_MOVE_RELATIVE", ANGRY, -40, -24, "pixels", [], "diagonal", ["y"]),
    # 5. faster, off the screen to the right and back while the init thread holds
    #    the VM lock: only DISABLED (hidden) outside, drawn again when back
    ("speed", ANGRY, 4),
    ("EVENT_ACTOR_MOVE_TO", ANGRY, 36, 21, "tiles", [], "horizontal", []),
    ("EVENT_ACTOR_MOVE_TO", ANGRY, 22, 21, "tiles", [], "horizontal", []),
    # 6. vertical first with the direction locked on y (walking animation, facing
    #    kept), blocked by the wall above; then right, facing right
    ("EVENT_ACTOR_MOVE_TO", "player", 20, 18, "tiles", ["walls"], "vertical", ["y"]),
    # 7. down then up again: the camera follows vertically
    ("EVENT_ACTOR_MOVE_RELATIVE", "player", 0, 3, "tiles", ["walls"], "horizontal", []),
    ("EVENT_ACTOR_MOVE_TO", "player", 23, 18, "tiles", ["walls"], "horizontal", []),
    # 8. diagonal in pixels, walls and actors: up blocked by the wall, left until
    #    the duck (actor collision in vm_actor_move_to_xy)
    ("EVENT_ACTOR_MOVE_TO", "player", 48, 120, "pixels", ["walls", "actors"], "diagonal", []),
]
RESTORED = ("player", OBLIVIOUS, ANGRY)       # the actors the plan moves


def ev(eid, command, **args):
    return {"command": command, "args": args, "id": eid}


def num(v):
    return {"type": "number", "value": v}


def plan_events(plan, wait=None):
    """Marker k before step k, marker len + 1 at the end; a wait after each
    movement in the console version."""
    events = []
    for k, step in enumerate(plan, 1):
        events.append(ev(f"m{k}", "EVENT_SET_VALUE", variable="0", value=num(k)))
        if step[0] == "speed":
            events.append(ev(f"e{k}", "EVENT_ACTOR_SET_MOVEMENT_SPEED", actorId=step[1], speed=step[2]))
            continue
        command, actor, x, y, units, collide, move_type, lock = step
        events.append(ev(f"e{k}", command, actorId=actor, x=num(x), y=num(y), units=units,
                         collideWith=collide, moveType=move_type, lockDirection=lock))
        if wait:
            events.append(ev(f"w{k}", "EVENT_WAIT", units="frames", frames=num(wait)))
    events.append(ev("end", "EVENT_SET_VALUE", variable="0", value=num(len(plan) + 1)))
    return events


def sequence(relative, x, y, move_type, lock):
    """The GBVM instructions of one movement (GB Studio _actorMoveToOps)."""
    move_x, move_y = (x != 0, y != 0) if relative else (True, True)
    if not move_x and not move_y:
        return []
    dir_x = "moving" if "x" in lock else "dir_x"
    dir_y = "moving" if "y" in lock else "dir_y"
    if move_x and not move_y:
        return ["init", dir_x, "x"]
    if move_y and not move_x:
        return ["init", dir_y, "y"]
    if move_type == "horizontal":
        return ["init", dir_x, "x", dir_y, "y"]
    if move_type == "vertical":
        return ["init", dir_y, "y", dir_x, "x"]
    first = "dir_y" if "y" not in lock else "dir_x" if "x" not in lock else "moving"
    return ["init", first, "xy"]


PROLOGUE = 3    # VM_LOCK, VM_RESERVE, VM_SET_SPRITE_MODE (toScriptString, scene init thread)


def plan_ops(plan, index, wait=None):
    """The oracle's instructions for the plan (vm_actor.c semantics), one per
    VM instruction as GB Studio writes them (the scheduler's quantum counts them):
    SET_VALUE 1 (VM_SET_CONST); SET_MOVEMENT_SPEED 2 (actor, speed); MOVE_TO
    2 + moves (VM_RPN, actor); MOVE_RELATIVE 1 + moves + 1 (VM_RPN, VM_POP);
    WAIT >= 5 frames 2 (VM_SET_CONST, VM_INVOKE wait_frames)."""
    ops = plain(PROLOGUE)
    for k, step in enumerate(plan, 1):
        ops.append(op_marker(k))
        if step[0] == "speed":
            ops += plain(1) + [op_speed(index[step[1]], step[2] * 32)]
            continue
        command, actor, x, y, units, collide, move_type, lock = step
        relative = command == "EVENT_ACTOR_MOVE_RELATIVE"
        shift = 8 if units == "tiles" else 5
        attr = ((ATTR_WALLS if "walls" in collide else 0) | (ATTR_ACTORS if "actors" in collide else 0) |
                (ATTR_H_FIRST if move_type == "horizontal" else 0) |
                (ATTR_DIAGONAL if move_type == "diagonal" else 0) |
                ((ATTR_SNAP_TILE if units == "tiles" else ATTR_SNAP_PX) if relative else 0))
        moves = move_ops(index[actor], x << shift, y << shift, attr, sequence(relative, x, y, move_type, lock))
        ops += plain(1) + moves + plain(1) if relative else plain(2) + moves
        if wait:
            ops += plain(1) + [op_wait(wait)]
    ops.append(op_marker(len(plan) + 1))
    return ops


def restore(start, wait, index):
    """Console loop: the moved actors back to their start (position, direction,
    speed), as events and as oracle instructions (SET_POSITION 3: VM_RPN, actor,
    VM_ACTOR_SET_POS; SET_DIRECTION 2; SET_MOVEMENT_SPEED 2; WAIT 2), then
    VM_JUMP back to the loop label. start: {id: (x, y, dir, speed)}."""
    events, ops = [], []
    for k, aid in enumerate(RESTORED):
        x, y, direction, speed = start[aid]
        events += [ev(f"r{k}p", "EVENT_ACTOR_SET_POSITION", actorId=aid, x=num(x), y=num(y), units="tiles"),
                   ev(f"r{k}d", "EVENT_ACTOR_SET_DIRECTION", actorId=aid,
                      direction={"type": "direction", "value": DIRECTIONS[direction]}),
                   ev(f"r{k}s", "EVENT_ACTOR_SET_MOVEMENT_SPEED", actorId=aid, speed=speed)]
        ops += (plain(2) + [op_set_pos(index[aid], x << 8, y << 8)] +
                plain(1) + [op_set_dir(index[aid], direction)] +
                plain(1) + [op_speed(index[aid], math.floor(speed * 32))])
    events.append(ev("rw", "EVENT_WAIT", units="frames", frames=num(wait)))
    ops += plain(1) + [op_wait(wait), op_jump(PROLOGUE)]
    return events, ops


def build_case(root, toolchain, emulator, bios, mutate=None, frames_total=None, plan=PLAN, wait=None):
    derive(GBS2, root / "project", "sample_town", PLAYER_START, fade=False)
    scene_file = next((root / "project/project/scenes").rglob("scene.gbsres"))
    scene = json.loads(scene_file.read_text(encoding="utf-8"))
    project = load_project(root / "project")
    source_scene = next(s for s in project.scenes if s["id"] == project.settings["startSceneId"])
    engine = build_engine_scene(project, source_scene)
    index = {"player": 0, **{a["id"]: i + 1 for i, a in enumerate(source_scene["actors"])}}
    events, ops = plan_events(plan, wait), plan_ops(plan, index, wait)
    if wait:
        start = {aid: (engine.actors[index[aid]]["x"] >> 8, engine.actors[index[aid]]["y"] >> 8,
                       engine.actors[index[aid]]["dir"], engine.actors[index[aid]]["move_speed"] / 32)
                 for aid in RESTORED}
        back_events, back_ops = restore(start, wait, index)
        events = [{"command": "EVENT_LOOP", "args": {}, "children": {"true": events + back_events}, "id": "loop"}]
        ops = ops + back_ops
    scene["script"] = events
    write_json(scene_file, scene)
    export_project(root / "project", root / "export")
    if mutate:
        for rel, fn in mutate.items():
            path = root / "export" / rel
            text = fn(path.read_text(encoding="utf-8"))
            path.write_bytes(text.encode("utf-8"))
            manifest = json.loads((root / "export/export.json").read_text(encoding="utf-8"))
            manifest["sha256"][rel] = hashlib.sha256(text.encode()).hexdigest()
            (root / "export/export.json").write_text(json.dumps(manifest), encoding="utf-8")
    build_rom(root / "export", toolchain)
    # The oracle predicts when each marker is set; the VM is then measured.
    horizon = 4000
    thread = ScriptThread(ops, start_tick=1)
    frames = engine_frames(engine, horizon, thread=thread)
    if wait:
        starts = [t for t, v in thread.mark_log if v == 1]
        if len(starts) < 2:
            raise SystemExit(f"the loop does not come back within {horizon} frames")
        end_tick = starts[1] + wait          # one lap, then the second lap has begun
    else:
        if not thread.done:
            raise SystemExit(f"the plan does not end within {horizon} frames")
        end_tick = thread.marks[len(plan) + 1]
    total = frames_total or end_tick + 20
    subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(root / "export"),
                    "--emulator", str(emulator), "--bios", str(bios),
                    "--out", str(root / "markers"), "--frames", str(end_tick + 5)],
                   capture_output=True, check=True, timeout=600)
    # The probe sees one value a tick: markers set in the same tick show the last one.
    last = {}
    for tick, value in thread.mark_log:
        if tick <= end_tick + 5:
            last[tick] = value
    predicted = [[tick, value] for tick, value in sorted(last.items())]
    changes = json.loads((root / "markers/probe.json").read_text())["changes"]
    marks = [v for v in changes.values() if len(v) > 1 and v[0]["value"] == 0 and
             all(1 <= c["value"] <= len(plan) + 1 for c in v[1:])]
    if len(marks) != 1:
        raise SystemExit(f"marker variable not identified: {sorted(changes)}")
    measured = [[c["frame"], c["value"]] for c in marks[0][1:]]
    width, height, pixels = background_pixels(project, source_scene)
    (root / "scene.rgb444").write_bytes(b"".join(v.to_bytes(2, "little") for v in pixels))
    (root / "source.json").write_text(json.dumps({
        "size_px": [width, height], "probe_frames": total, "step_from_game_time": True,
        "view": list(view_of(engine.background))[::-1],
        "expected_scroll": [list(p) for p, _ in frames[:total + 10]],
        "actors": [d for _, d in frames[:total + 10]],
        "project": str(root / "project"), "default_sprite_mode": project.settings["spriteMode"],
        "sprite_palettes": [list(p) for p in sprite_palettes(project, source_scene)]}))
    return predicted, measured, frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--bios", type=Path, required=True)
    parser.add_argument("--wait", type=int, help="console version: frames of rest after each movement")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    args.out.mkdir(parents=True)
    predicted, measured, _ = build_case(args.out, args.toolchain, args.emulator, args.bios, wait=args.wait)
    if measured != predicted:
        raise SystemExit(f"chronology differs: predicted {predicted}, measured {measured}")
    name = (f"ngpcraft_m2_actor_moves_loop_{args.wait}f.ngc" if args.wait else
            "ngpcraft_m2_actor_moves_sample_town.ngc")
    write_new(args.out / name, (args.out / "export/rom/game.ngc").read_bytes())
    print(json.dumps({"rom": str(args.out / name), "marker_ticks": measured}))


if __name__ == "__main__":
    main()
