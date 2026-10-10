"""Custom scripts (EVENT_CALL_CUSTOM_EVENT) on the scene engine: proof ROM and oracle.

sample_town (derived copy of gbs2) and two custom scripts:
  Place(actor 0, V0, V1, by value): activate actor 0, put it at (V0, V1)
      tiles, VAR_2 = V0 + V1, then V0 + 1 (its own copy only);
  Twice(actor 0, V0 by value): call Place(its actor 0, its V0, 18), then
      VAR_3 = V0 (unchanged by Place's increment: arguments are copies).
Scene init: marker 1, VAR_4 = 22, Place(Oblivious, 14, VAR_4), wait,
marker 2, Twice(Cat, 5 + 5), wait, marker 3, and the same in a loop for
the console. The oracle applies the actor effects at the marker ticks
measured on the VM; the variables are checked on the VM.
"""
import argparse
import json
from pathlib import Path

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_actor_events import CAT, GBS2, OBLIVIOUS, ROOT, ev, marker, num, wait
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import derive, write_json
from tools.gbvm_actors import engine_frames

PLACE, TWICE = "proof-place-0000", "proof-twice-0000"


class MarkersNotReached(RuntimeError):
    """The scene script did not run its three groups (a broken call or return)."""


def var(v):
    return {"type": "variable", "value": v}


def custom_scripts(by_reference=False):
    place = {"_resourceType": "script", "id": PLACE, "name": "proof/Place", "description": "",
             "symbol": "script_place",
             "variables": {"V0": {"id": "V0", "name": "X", "passByReference": False},
                           "V1": {"id": "V1", "name": "Y", "passByReference": False}},
             "actors": {"0": {"id": "0", "name": "Who"}},
             "script": [ev("p1", "EVENT_ACTOR_ACTIVATE", actorId="0"),
                        ev("p2", "EVENT_ACTOR_SET_POSITION", actorId="0", x=var("V0"), y=var("V1"), units="tiles"),
                        ev("p3", "EVENT_SET_VALUE", variable="2",
                           value={"type": "add", "valueA": var("V0"), "valueB": var("V1")}),
                        ev("p4", "EVENT_INC_VALUE", variable="V0")]}
    twice = {"_resourceType": "script", "id": TWICE, "name": "proof/Twice", "description": "",
             "symbol": "script_twice",
             "variables": {"V0": {"id": "V0", "name": "X", "passByReference": False}},
             "actors": {"0": {"id": "0", "name": "Who"}},
             "script": [ev("t1", "EVENT_CALL_CUSTOM_EVENT", customEventId=PLACE, **{
                            "$actor[0]$": "0", "$variable[V0]$": var("V0"), "$variable[V1]$": num(18)}),
                        ev("t2", "EVENT_SET_VALUE", variable="3", value=var("V0"))]}
    if by_reference:
        place["variables"]["V0"]["passByReference"] = True
        twice["variables"]["V0"]["passByReference"] = True
    return place, twice


def scene_script(frames, loop, by_reference=False):
    body = [marker("m1", 1),
            ev("s1", "EVENT_SET_VALUE", variable="4", value=num(22)),
            ev("s2", "EVENT_CALL_CUSTOM_EVENT", customEventId=PLACE, **{
                "$actor[0]$": OBLIVIOUS, "$variable[V0]$": num(14), "$variable[V1]$": var("4")}),
            wait("w1", frames), marker("m2", 2),
            ev("s3", "EVENT_CALL_CUSTOM_EVENT", customEventId=TWICE, **{
                "$actor[0]$": CAT, "$variable[V0]$": {"type": "add", "valueA": num(5), "valueB": num(5)}}),
            wait("w2", frames), marker("m3", 3)]
    if by_reference:
        body[2]["args"]["$variable[V0]$"] = var("5")
        body.insert(2, ev("ref-init", "EVENT_SET_VALUE", variable="5", value=num(14)))
    if not loop:
        return body
    # Console: put both back where the scene starts them, show it, start again.
    body += [ev("r1", "EVENT_ACTOR_SET_POSITION", actorId=OBLIVIOUS, x=num(18), y=num(16), units="tiles"),
             ev("r2", "EVENT_ACTOR_SET_POSITION", actorId=CAT, x=num(4), y=num(6), units="tiles"),
             wait("w3", frames)]
    return [{"command": "EVENT_LOOP", "args": {}, "children": {"true": body}, "id": "loop"}]


def project_case(root, frames=20, loop=False, by_reference=False):
    derive(GBS2, root / "project", "sample_town", (12, 20), fade=False)
    scene_file = next((root / "project/project/scenes").rglob("scene.gbsres"))
    scene = json.loads(scene_file.read_text(encoding="utf-8"))
    scene["script"] = scene_script(frames, loop, by_reference)
    write_json(scene_file, scene)
    folder = root / "project/project/scripts/proof"
    folder.mkdir(parents=True, exist_ok=True)
    for script in custom_scripts(by_reference):
        write_json(folder / f"{script['symbol']}.gbsres", script)
    return root / "project"


def oracle_actions(scene_actors, ticks, loop):
    index = {a["id"]: i + 1 for i, a in enumerate(scene_actors)}
    oblivious, cat = index[OBLIVIOUS], index[CAT]

    def place(i, x, y):
        def run(scene, actors):
            scene.vm_activate(actors[i])
            actors[i].x, actors[i].y = x << 8, y << 8
        return run

    def put(i, x, y):
        def run(scene, actors):
            actors[i].x, actors[i].y = x << 8, y << 8
        return run
    actions = {ticks[0]: [place(oblivious, 14, 22)], ticks[1]: [place(cat, 10, 18)]}
    if loop:
        actions[ticks[2]] = [put(oblivious, 18, 16), put(cat, 4, 6)]
    return actions


def build_case(root, toolchain, emulator, bios, frames=20, loop=False, probe_frames=100, mutate=None, by_reference=False):
    import hashlib
    import subprocess
    import sys
    project = project_case(root, frames, loop, by_reference)
    manifest = export_project(project, root / "export")
    if mutate:
        rel, old, new = mutate
        path = root / "export" / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"mutation target found {text.count(old)} times in {rel}")
        text = text.replace(old, new)
        path.write_bytes(text.encode("utf-8"))
        manifest["sha256"][rel] = hashlib.sha256(text.encode()).hexdigest()
        (root / "export/export.json").write_text(json.dumps(manifest), encoding="utf-8")
    build_rom(root / "export", toolchain)
    subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(root / "export"),
                    "--emulator", str(emulator), "--bios", str(bios), "--out", str(root / "markers"),
                    "--frames", str(max(probe_frames, 3 * frames + 20))],
                   capture_output=True, check=True, timeout=600)
    changes = json.loads((root / "markers/probe.json").read_text())["changes"]
    marks = next((v for v in changes.values() if [c["value"] for c in v][:4] == [0, 1, 2, 3]), None)
    if marks is None:
        raise MarkersNotReached(f"markers 1, 2, 3 not reached in order: {changes}")
    ticks = [c["frame"] for c in marks[1:4]]
    loaded = load_project(project)
    scene = next(s for s in loaded.scenes if s["id"] == loaded.settings["startSceneId"])
    engine = build_engine_scene(loaded, scene)
    actions = oracle_actions(scene["actors"], ticks, loop)
    if loop:
        # The console loop: the same actions every turn, period measured (marker 1 again).
        again = [c["frame"] for c in marks[4:] if c["value"] == 1]
        if not again:
            raise SystemExit("the loop did not come back within the probed frames")
        period = again[0] - ticks[0]
        first = dict(actions)
        for turn in range(1, (probe_frames + 10) // period + 1):
            for tick, fns in first.items():
                actions.setdefault(tick + turn * period, []).extend(fns)
    lock = {1: True} if loop else {1: True, ticks[2]: False}
    for tick, state in lock.items():
        actions.setdefault(tick, []).append(lambda s, a, state=state: setattr(s, "locked", state))
    frames_out = engine_frames(engine, probe_frames + 10, actions)
    width, height, pixels = background_pixels(loaded, scene)
    write_new(root / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
    write_new(root / "source.json", json.dumps({
        "size_px": [width, height], "probe_frames": probe_frames, "step_from_game_time": True,
        "view": list(view_of(engine.background))[::-1],
        "expected_scroll": [list(p) for p, _ in frames_out], "actors": [d for _, d in frames_out],
        "project": str(project), "default_sprite_mode": loaded.settings["spriteMode"],
        "sprite_palettes": [list(p) for p in sprite_palettes(loaded, scene)],
        "marker_ticks": ticks, "variables": changes}).encode("utf-8"))
    return manifest, ticks, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--bios", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=0, help="console loop: seconds per phase")
    parser.add_argument("--by-reference", action="store_true", help="mutate caller variables through nested calls")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    args.out.mkdir(parents=True)
    loop = args.seconds > 0
    frames = 60 * args.seconds if loop else 20
    manifest, ticks, changes = build_case(args.out, args.toolchain, args.emulator, args.bios, frames, loop,
                                          probe_frames=3 * frames + 40 if loop else 100, by_reference=args.by_reference)
    name = f"ngpcraft_m2_custom_events_{args.seconds}s.ngc" if loop else "ngpcraft_m2_custom_events.ngc"
    if args.by_reference:
        name = name.replace("custom_events", "custom_events_references")
    write_new(args.out / name, (args.out / "export/rom/game.ngc").read_bytes())
    print(json.dumps({"rom": str(args.out / name), "marker_ticks": ticks,
                      "scripts": sorted(manifest["scripts"])}, indent=1))


if __name__ == "__main__":
    main()
