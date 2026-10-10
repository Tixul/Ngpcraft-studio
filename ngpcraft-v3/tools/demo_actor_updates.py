"""C13 proof: init lock, concurrent actor loops, termination and restart.

The first actor changes position every phase. A second actor deactivates it,
then reactivates it. The independent scheduler predicts every screen.
--pause 120 gives a slow, looping console version; default 8 is the test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, background_source, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import derive, write_json
from tools.demo_backgrounds import write_new
from tools.demo_actor_moves import ev, num, GBS2, ANGRY, OBLIVIOUS, sequence
from tools.gbvm_actors import (ScriptScheduler, engine_frames, plain, op_set_pos,
                               op_wait, op_idle, op_jump, op_activate, op_stop_update, move_ops, ATTR_H_FIRST)


def project_case(root, pause=8, mode="lifecycle"):
    derive(GBS2, root / "project", "sample_town", (12, 20), fade=False)
    sf = next((root / "project/project/scenes").rglob("scene.gbsres"))
    scene = json.loads(sf.read_text(encoding="utf-8"))
    scene["script"] = [ev("init_wait", "EVENT_WAIT", units="frames", frames=num(5))]
    write_json(sf, scene)
    for path in sorted((root / "project/project/scenes").rglob("actors/*.gbsres")):
        actor = json.loads(path.read_text(encoding="utf-8"))
        if actor["id"] == ANGRY:
            actor["updateScript"] = [
                ev("left", "EVENT_ACTOR_SET_POSITION", actorId="$self$", x=num(14), y=num(21), units="tiles"),
                ev("w_left", "EVENT_WAIT", units="frames", frames=num(pause)),
                ev("right", "EVENT_ACTOR_SET_POSITION", actorId="$self$", x=num(18), y=num(21), units="tiles"),
                ev("w_right", "EVENT_WAIT", units="frames", frames=num(pause))]
        elif actor["id"] == OBLIVIOUS:
            actor["updateScript"] = [
                ev("w_start", "EVENT_WAIT", units="frames", frames=num(2 * pause)),
                ev("disable", "EVENT_ACTOR_DEACTIVATE", actorId=ANGRY),
                ev("w_disable", "EVENT_WAIT", units="frames", frames=num(pause)),
                ev("enable", "EVENT_ACTOR_ACTIVATE", actorId=ANGRY),
                ev("w_enable", "EVENT_WAIT", units="frames", frames=num(pause))]
        if mode == "stop_update" and actor["id"] == OBLIVIOUS:
            actor["updateScript"] = [
                ev("w_start", "EVENT_WAIT", units="frames", frames=num(2 * pause)),
                ev("stop", "EVENT_ACTOR_STOP_UPDATE", actorId=ANGRY),
                ev("stop_again", "EVENT_ACTOR_STOP_UPDATE", actorId=ANGRY),
                ev("stop_player", "EVENT_ACTOR_STOP_UPDATE", actorId="player"),
                ev("w_stopped", "EVENT_WAIT", units="frames", frames=num(2 * pause)),
                ev("disable", "EVENT_ACTOR_DEACTIVATE", actorId=ANGRY),
                ev("w_disable", "EVENT_WAIT", units="frames", frames=num(pause)),
                ev("enable", "EVENT_ACTOR_ACTIVATE", actorId=ANGRY),
                ev("w_enable", "EVENT_WAIT", units="frames", frames=num(pause))]
        if mode == "self_stop" and actor["id"] == ANGRY:
            actor["updateScript"].insert(2, ev("stop_self", "EVENT_ACTOR_STOP_UPDATE", actorId="$self$"))
        if mode == "offscreen" and actor["id"] == OBLIVIOUS:
            actor["persistent"] = True
            for i, y in ((1, 50), (3, 20)):
                actor["updateScript"][i] = ev(f"camera_{i}", "EVENT_ACTOR_SET_POSITION", actorId="player",
                                               x=num(12), y=num(y), units="tiles")
        if mode == "movement" and actor["id"] == ANGRY:
            for i in (0, 2):
                actor["updateScript"][i]["command"] = "EVENT_ACTOR_MOVE_TO"
                actor["updateScript"][i]["args"].update(collideWith=[], moveType="horizontal", lockDirection=[])
        write_json(path, actor)
    return root / "project"


def expected(engine, scene, ticks, pause, mode="lifecycle"):
    index = {a["id"]: i + 1 for i, a in enumerate(scene["actors"])}
    worker, controller = index[ANGRY], index[OBLIVIOUS]
    # Init: LOCK, RESERVE, SET_SPRITE_MODE, SET_CONST wait, INVOKE, STOP.
    init = plain(4) + [op_wait(5)]
    def worker_ops():
        # RESERVE once; SET_POSITION = RPN + SET_CONST actor + SET_POS;
        # WAIT = SET_CONST + INVOKE; implicit loop = IDLE + JUMP after locals.
        if mode == "movement":
            ops = plain(1)
            for x in (14, 18):
                ops += plain(2) + move_ops(worker, x << 8, 21 << 8, ATTR_H_FIRST,
                                           sequence(False, x, 21, "horizontal", []))
                ops += plain(1) + [op_wait(pause)]
            return ops + [op_idle(), op_jump(1)]
        if mode == "self_stop":
            return (plain(1) + plain(2) + [op_set_pos(worker, 14 << 8, 21 << 8)] +
                    plain(1) + [op_wait(pause)] + plain(1) + [op_stop_update(worker)] +
                    plain(2) + [op_set_pos(worker, 18 << 8, 21 << 8)])
        return (plain(1) + plain(2) + [op_set_pos(worker, 14 << 8, 21 << 8)] +
                plain(1) + [op_wait(pause)] + plain(2) + [op_set_pos(worker, 18 << 8, 21 << 8)] +
                plain(1) + [op_wait(pause), op_idle(), op_jump(1)])
    def controller_ops():
        if mode == "stop_update":
            return (plain(1) + plain(1) + [op_wait(2 * pause)] +
                    plain(1) + [op_stop_update(worker)] + plain(1) + [op_stop_update(worker)] +
                    plain(1) + [op_stop_update(0)] + plain(1) + [op_wait(2 * pause)] +
                    plain(1) + [op_activate(worker, False)] + plain(1) + [op_wait(pause)] +
                    plain(1) + [op_activate(worker, True)] + plain(1) +
                    [op_wait(pause), op_idle(), op_jump(1)])
        if mode == "offscreen":
            return (plain(1) + plain(1) + [op_wait(2 * pause)] +
                    plain(2) + [op_set_pos(0, 12 << 8, 50 << 8)] + plain(1) + [op_wait(pause)] +
                    plain(2) + [op_set_pos(0, 12 << 8, 20 << 8)] + plain(1) +
                    [op_wait(pause), op_idle(), op_jump(1)])
        return (plain(1) + plain(1) + [op_wait(2 * pause)] +
                plain(1) + [op_activate(worker, False)] + plain(1) + [op_wait(pause)] +
                plain(1) + [op_activate(worker, True)] + plain(1) +
                [op_wait(pause), op_idle(), op_jump(1)])
    scheduler = ScriptScheduler(init, {worker: worker_ops, controller: controller_ops})
    return engine_frames(engine, ticks, thread=scheduler), scheduler


def build_case(root, toolchain, pause=8, mutate=None, mode="lifecycle"):
    project_path = project_case(root, pause, mode)
    manifest = export_project(project_path, root / "export")
    if mutate:
        rel, old, new = mutate
        path = root / "export" / rel
        source = path.read_text(encoding="utf-8")
        if source.count(old) != 1:
            raise AssertionError((rel, old, source.count(old)))
        source = source.replace(old, new)
        write_new(path.with_suffix(".mutated"), source.encode())
        path.with_suffix(".mutated").replace(path)
        manifest["sha256"][rel] = hashlib.sha256(source.encode()).hexdigest()
        write_json(root / "export/export.json", manifest)
    build = build_rom(root / "export", toolchain)
    project = load_project(project_path)
    scene = project.scenes[0]
    engine = build_engine_scene(project, scene)
    total = 9 * pause + 20
    frames, scheduler = expected(engine, scene, total + 10, pause, mode)
    width, height, pixels = background_pixels(project, scene)
    write_new(root / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
    source = background_source(project, scene)
    priority_mask = bytes(bool(source.attributes[(y // 8) * (width // 8) + x // 8] & 128)
                          and bool(source.indices[y * width + x])
                          for y in range(height) for x in range(width))
    write_new(root / "scene.gb-priority", priority_mask)
    write_json(root / "source.json", {
        "gb_priority_mask": "scene.gb-priority",
        "size_px": [width, height], "probe_frames": total, "step_from_game_time": True,
        "view": list(view_of(engine.background))[::-1],
        "expected_scroll": [list(p) for p, _ in frames], "actors": [d for _, d in frames],
        "project": str(project_path), "default_sprite_mode": project.settings["spriteMode"],
        "sprite_palettes": [list(p) for p in sprite_palettes(project, scene)],
        "oracle": "tools.gbvm_actors.ScriptScheduler", "actor_starts": scheduler.starts})
    return manifest, build


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--pause", type=int, default=120)
    p.add_argument("--mode", choices=("lifecycle", "offscreen", "movement", "stop_update", "self_stop"),
                   default="lifecycle")
    args = p.parse_args()
    if args.out.exists() or args.pause < 5:
        p.error("output must be new; pause must be at least 5 frames")
    args.out.mkdir(parents=True)
    manifest, build = build_case(args.out, args.toolchain, args.pause, mode=args.mode)
    name = "loop" if args.mode == "lifecycle" else args.mode
    rom = args.out / f"ngpcraft_m2_actor_updates_{name}_{args.pause}f.ngc"
    write_new(rom, (args.out / "export/rom/game.ngc").read_bytes())
    print(json.dumps({"rom": str(rom), "contexts": manifest["scene_engine"]["update_contexts"],
                      "bytes": build["header"]["size_bytes"]}))


if __name__ == "__main__":
    main()
