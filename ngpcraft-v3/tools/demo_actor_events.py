"""Actor events of GB Studio on the scene engine: a proof ROM and its oracle.

sample_town (derived copy of gbs2), a scene script of four groups of actor
events separated by WAIT 20 frames; a marker variable set before each group
gives the tick of each group, measured on the VM (tools/probe_gbvm.py). The
oracle applies the same events as vm_actor.c at those ticks; the probe compares
every frame.
"""
import argparse
import hashlib
import json
import os
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
from tools.gbvm_actors import COLLISION, HIDDEN, engine_frames
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
GBS2 = REFERENCE / "appData/templates/gbs2"
DUCK, ANGRY, OBLIVIOUS, CAT = ("d12d5092-2716-441d-bab0-0e15fe715f5e", "a85c4ba5-9bc0-4daa-94bc-4ef68a690659",
                               "5cf6eb6c-be62-4efc-9cbb-14a8efac21a8", "076a0612-024a-426a-85fc-3e1054b9f74e")


def ev(eid, command, **args):
    return {"command": command, "args": args, "id": eid}


def num(v):
    return {"type": "number", "value": v}


def marker(eid, value):
    return ev(eid, "EVENT_SET_VALUE", variable="0", value=num(value))


def wait(eid, frames=20):
    return ev(eid, "EVENT_WAIT", units="frames", frames=num(frames))


SCRIPT = [
    marker("m1", 1),
    ev("a1", "EVENT_ACTOR_SET_POSITION", actorId=OBLIVIOUS, x=num(14), y=num(22), units="tiles"),
    ev("a2", "EVENT_ACTOR_SET_DIRECTION", actorId=ANGRY, direction={"type": "direction", "value": "left"}),
    ev("a3", "EVENT_ACTOR_HIDE", actorId=DUCK),
    ev("a4", "EVENT_ACTOR_SET_ANIMATION_SPEED", actorId=ANGRY, speed=1),
    wait("w1"), marker("m2", 2),
    ev("b1", "EVENT_ACTOR_SHOW", actorId=DUCK),
    ev("b2", "EVENT_ACTOR_DEACTIVATE", actorId=OBLIVIOUS),
    ev("b3", "EVENT_ACTOR_SET_POSITION", actorId=CAT, x=num(10), y=num(22), units="tiles"),
    ev("b4", "EVENT_ACTOR_ACTIVATE", actorId=CAT),
    ev("b5", "EVENT_ACTOR_SET_FRAME", actorId=DUCK, frame=num(1)),
    wait("w2"), marker("m3", 3),
    ev("c1", "EVENT_ACTOR_ACTIVATE", actorId=OBLIVIOUS),
    ev("c2", "EVENT_ACTOR_DEACTIVATE", actorId="player"),
    ev("c3", "EVENT_ACTOR_COLLISIONS_DISABLE", actorId=ANGRY),
    ev("c4", "EVENT_ACTOR_SET_MOVEMENT_SPEED", actorId=ANGRY, speed=2),
    wait("w3"), marker("m4", 4),
    ev("d1", "EVENT_ACTOR_ACTIVATE", actorId="player"),
]


def console_script(frames, angry):
    """The same four groups, each shown `frames` frames, the fourth one putting
    everything back as it was, inside EVENT_LOOP: made to be watched on a console."""
    groups = [e for e in SCRIPT if e["command"] != "EVENT_WAIT"]
    body = []
    for event in groups:
        if event["id"] in ("m2", "m3", "m4"):
            body.append(wait("w" + event["id"], frames))
        body.append(event)
    body += [ev("d2", "EVENT_ACTOR_SET_POSITION", actorId=OBLIVIOUS, x=num(18), y=num(16), units="tiles"),
             ev("d3", "EVENT_ACTOR_SET_DIRECTION", actorId=ANGRY,
                direction={"type": "direction", "value": angry["direction"]}),
             ev("d4", "EVENT_ACTOR_SET_ANIMATION_SPEED", actorId=ANGRY, speed=angry["animSpeed"]),
             ev("d5", "EVENT_ACTOR_SET_POSITION", actorId=CAT, x=num(4), y=num(6), units="tiles"),
             wait("w4", frames)]
    return [{"command": "EVENT_LOOP", "args": {}, "children": {"true": body}, "id": "loop"}]


def oracle_actions(scene_actors, ticks, restore=None):
    """The same events, as vm_actor.c applies them, at the measured ticks."""
    index = {a["id"]: i + 1 for i, a in enumerate(scene_actors)}
    duck, angry, oblivious, cat = (index[x] for x in (DUCK, ANGRY, OBLIVIOUS, CAT))

    def at(actor, fn):
        return lambda scene, actors: fn(scene, actors[actor])

    def pos(x, y):
        def apply(scene, a):
            a.x, a.y = x << 8, y << 8
        return apply
    return {
        ticks[0]: [at(oblivious, pos(14, 22)), at(angry, lambda s, a: s.set_dir(a, 3, False)),
                   at(duck, lambda s, a: s.set_flags(a, HIDDEN, HIDDEN)),
                   at(angry, lambda s, a: setattr(a, "anim_tick", 1))],
        ticks[1]: [at(duck, lambda s, a: s.set_flags(a, 0, HIDDEN)),
                   at(oblivious, lambda s, a: s.vm_deactivate(a)), at(cat, pos(10, 22)),
                   at(cat, lambda s, a: s.vm_activate(a)), at(duck, lambda s, a: s.set_frame_offset(a, 1))],
        ticks[2]: [at(oblivious, lambda s, a: s.vm_activate(a)), at(0, lambda s, a: s.vm_deactivate(a)),
                   at(angry, lambda s, a: s.set_flags(a, 0, COLLISION))],
        ticks[3]: [at(0, lambda s, a: s.vm_activate(a))] + (restore(at, pos, oblivious, angry, cat) if restore else []),
    }


def build_case(root, toolchain, emulator, bios, mutate=None, frames_total=100, console_frames=None):
    derive(GBS2, root / "project", "sample_town", (12, 20), fade=False)
    scene_file = next((root / "project/project/scenes").rglob("scene.gbsres"))
    scene = json.loads(scene_file.read_text(encoding="utf-8"))
    angry = json.loads(next(f for f in scene_file.parent.rglob("actors/*.gbsres")
                            if json.loads(f.read_text(encoding="utf-8"))["id"] == ANGRY).read_text(encoding="utf-8"))
    scene["script"] = console_script(console_frames, angry) if console_frames else SCRIPT
    write_json(scene_file, scene)
    export_project(root / "project", root / "export")
    if mutate:
        path = root / "export/src/scene_actors.c"
        text = mutate(path.read_text(encoding="utf-8"))
        path.write_bytes(text.encode("utf-8"))
        manifest = json.loads((root / "export/export.json").read_text(encoding="utf-8"))
        manifest["sha256"]["src/scene_actors.c"] = hashlib.sha256(text.encode()).hexdigest()
        (root / "export/export.json").write_text(json.dumps(manifest), encoding="utf-8")
    build_rom(root / "export", toolchain)
    # Ticks of the four groups, measured on the VM (markers), not predicted.
    subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(root / "export"),
                    "--emulator", str(emulator), "--bios", str(bios),
                    "--out", str(root / "markers"), "--frames", str(max(80, 4 * (console_frames or 20)))], capture_output=True, check=True, timeout=300)
    changes = json.loads((root / "markers/probe.json").read_text())["changes"]
    marks = next(v for v in changes.values() if [c["value"] for c in v][:5] == [0, 1, 2, 3, 4])[:5]
    ticks = [c["frame"] for c in marks[1:]]
    project = load_project(root / "project")
    scene = next(s for s in project.scenes if s["id"] == project.settings["startSceneId"])
    engine = build_engine_scene(project, scene)
    width, height, pixels = background_pixels(project, scene)
    restore = None
    if console_frames:
        dirs = {"down": 0, "right": 1, "up": 2, "left": 3}

        def restore(at, pos, oblivious, angry_i, cat):
            def anim(s, a):
                a.anim_tick = angry["animSpeed"]
            return [at(oblivious, pos(18, 16)), at(angry_i, lambda s, a: s.set_dir(a, dirs[angry["direction"]], False)),
                    at(angry_i, anim), at(cat, pos(4, 6))]
    actions = oracle_actions(scene["actors"], ticks, restore)
    # The init thread holds VM_LOCK from its first tick until it ends: the last
    # group's tick, or never for the console loop (actors_update, VM_ISLOCKED).
    lock = {1: True} if console_frames else {1: True, ticks[3]: False}
    for tick, state in lock.items():
        actions.setdefault(tick, []).append(lambda s, a, state=state: setattr(s, "locked", state))
    frames = engine_frames(engine, frames_total + 10, actions)
    (root / "scene.rgb444").write_bytes(b"".join(v.to_bytes(2, "little") for v in pixels))
    (root / "source.json").write_text(json.dumps({
        "size_px": [width, height], "probe_frames": frames_total, "step_from_game_time": True,
        "view": list(view_of(engine.background))[::-1],
        "expected_scroll": [list(p) for p, _ in frames], "actors": [d for _, d in frames],
        "project": str(root / "project"), "default_sprite_mode": project.settings["spriteMode"],
        "sprite_palettes": [list(p) for p in sprite_palettes(project, scene)]}))
    return ticks




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--bios", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=2, help="duration of each phase on screen")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    args.out.mkdir(parents=True)
    seconds = args.seconds
    frames = 60 * seconds
    ticks = build_case(args.out, args.toolchain, args.emulator, args.bios,
                       frames_total=4 * frames - 5, console_frames=frames)
    name = f"ngpcraft_m2_actor_events_loop_{seconds}s.ngc"
    write_new(args.out / name, (args.out / "export/rom/game.ngc").read_bytes())
    print(json.dumps({"rom": str(args.out / name), "group_ticks": ticks}))


if __name__ == "__main__":
    main()
