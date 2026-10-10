"""M4 Launch Projectile: author events and hit scripts in a native ROM.

Disposable project derived from gbs2 Sample Town. A gun actor's On Update
script launches ten projectiles with the five authored direction modes (plus
the source-direction shortcut); targets and the player run their collision
scripts. Oracle: the released GBVM projectiles.c and math_atan2.c compiled to
wasm, fed with the launches the event semantics predict at the ticks the ROM
marks (variable 20), and with the hit-script ends the ROM marks. Compared:
which script branch starts at which tick (variables), and every picture
against the editor PNGs (tools/probe_stream.Expected).
"""
import argparse
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
from array import array
from pathlib import Path

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.projectiles import scene_registry
from ngpcraft.scene_assets import background_source, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
import tools.lab_projectile_pool as pool
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import derive, write_json
from tools.probe_rom import power_on_ram, verified_tick, write_frame
from tools.probe_stream import Expected
from tools.reference import REFERENCE

START = (12, 12)                      # player tile: (96, 96) px, still camera
POSITIONS = {"t1": (128, 72), "t2": (112, 40), "gun": (48, 72)}
ORDER = ("t1", "t2", "gun")           # engine actors 1..3; oracle group = 1 << index
SHEETS = {"t1": "cat", "t2": "sage", "gun": "dog"}
DIRECTIONS = {"t1": "left", "t2": "down", "gun": "right"}
MARK, ANGLE_VAR = 20, 25
# (actor index, projectile group) -> variable the started branch increments.
HIT_VARS = {(1, 2): 22, (1, 4): 24, (1, 8): 26, (2, 2): 27, (0, 2): 29}
END_VARS = {1: 28, 2: 30, 0: 31}      # each hit script's last event
WATCH = (MARK, 22, 24, 26, 27, 28, 29, 30, 31)
DIR_ANGLE = {"down": 128, "right": 64, "up": 0, "left": 192}   # dir_angle_lookup


def shot(sheet, **kw):
    args = dict(spriteSheetId=sheet, spriteStateId="", speed=2, animSpeed=15, loopAnim=True, lifeTime=2,
                initialOffset=0, destroyOnHit=True, collisionGroup="1", collisionMask=["1"], x=0, y=0,
                actorId="gun", directionType="direction", direction="right", angle=0, angleVariable="0",
                otherActorId="$self$", targetActorId="$self$")
    args.update(kw)
    return args


def plan(sheet):
    """(marker, event args, frames waited after)."""
    return [(1, shot(sheet), 50),
            (2, shot(sheet), 0), (3, shot(sheet), 60),                 # second hit while busy
            (4, shot(sheet, directionType="angle", angle=64, collisionGroup="2", x=4, y=-2), 50),
            (5, shot(sheet, directionType="anglevar", angleVariable=str(ANGLE_VAR), collisionGroup="3"), 50),
            (6, shot(sheet, directionType="actor", otherActorId="gun", initialOffset=8), 50),
            (7, shot(sheet, directionType="actor", otherActorId="t2", lifeTime=0.5), 40),
            (8, shot(sheet, directionType="target", targetActorId="t2", collisionMask=["2"]), 60),
            (9, shot(sheet, directionType="target", targetActorId="player", collisionMask=["player"], speed=1.5), 60),
            (10, shot(sheet, destroyOnHit=False, collisionGroup="2", speed=1), 120)]


def project_case(root, cold=False, modal=False):
    root.mkdir(parents=True, exist_ok=True)
    project = root / "project"
    derive(GBS2, project, "sample_town", START, fade=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    folder = path.parent
    paths = list((folder / "actors").glob("*.gbsres"))
    template = json.loads(paths[0].read_text(encoding="utf-8"))
    for p in paths:
        assert p.resolve().is_relative_to(project.resolve())
        p.unlink()
    for p in (folder / "triggers").glob("*.gbsres"):
        p.unlink()
    loaded = load_project(project)
    sprites = {s["name"]: s["id"] for _, s in loaded.by_type["sprite"]}
    script = [ev("gun-no-collision", "EVENT_ACTOR_COLLISIONS_DISABLE", actorId="gun"),
              ev("angle-var", "EVENT_SET_VALUE", variable=str(ANGLE_VAR), value=num(64))]
    for k, args, wait in plan(sprites["npc002"]):
        script += [ev(f"mark-{k}", "EVENT_SET_VALUE", variable=str(MARK), value=num(k))]
        if not cold:
            script.append(ev(f"shot-{k}", "EVENT_LAUNCH_PROJECTILE", **args))
        if wait:
            script.append(ev(f"wait-{k}", "EVENT_WAIT", units="frames", frames=num(wait)))
    script += [ev("mark-end", "EVENT_SET_VALUE", variable=str(MARK), value=num(11)),
               ev("park", "EVENT_WAIT", units="frames", frames=num(900))]
    if modal:
        # One shot 16 px right of the gun, then a modal dialogue nobody closes.
        script = [script[0], ev("modal-shot", "EVENT_LAUNCH_PROJECTILE", **dict(plan(sprites["npc002"])[0][1], x=16)),
                  ev("modal-text", "EVENT_TEXT", text=["Halt"]), ev("park", "EVENT_WAIT", units="frames", frames=num(900))]
    inc = lambda eid, v: ev(eid, "EVENT_INC_VALUE", variable=str(v))
    scripts = {"t1": dict(hit1Script=[inc("t1-h1", 22), ev("t1-h1-wait", "EVENT_WAIT", units="frames", frames=num(20)),
                                      inc("t1-h1-end", 28)],
                          hit2Script=[inc("t1-h2", 24), inc("t1-h2-end", 28)],
                          hit3Script=[inc("t1-h3", 26), inc("t1-h3-end", 28)]),
               "t2": dict(hit1Script=[inc("t2-h1", 27), inc("t2-h1-end", 30)]),
               "gun": dict(updateScript=script)}
    groups = {"t1": "1", "t2": "2", "gun": ""}
    for i, name in enumerate(ORDER):
        actor = copy.deepcopy(template)
        actor.update(id=name, symbol=f"actor_{name}", name=name, _index=i, prefabId="",
                     spriteSheetId=sprites[SHEETS[name]], coordinateType="pixels",
                     x=POSITIONS[name][0], y=POSITIONS[name][1], direction=DIRECTIONS[name],
                     moveSpeed=1, animSpeed=255, persistent=True, isPinned=False,
                     collisionGroup=groups[name], collisionExtraFlags=[],
                     script=[], startScript=[], updateScript=[], hit1Script=[], hit2Script=[], hit3Script=[])
        actor.update(scripts.get(name, {}))
        write_json(folder / f"actors/{name}.gbsres", actor)
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene.update(name="M4 projectile events", script=[], autoFadeSpeed=None,
                 playerHit1Script=[inc("p-h1", 29), inc("p-h1-end", 31)], playerHit2Script=[], playerHit3Script=[])
    write_json(path, scene)
    return project


def oracle_source(definitions, capacity):
    """projectiles.c (lab_projectile_pool harness, these definitions) + math_atan2.c."""
    saved = pool.definitions
    pool.definitions = lambda: tuple(definitions)
    try:
        source = pool.reference_source(capacity)
    finally:
        pool.definitions = saved
    math_c = (REFERENCE / "appData/engine/gbvm/src/core/math_atan2.c").read_text(encoding="utf-8")
    math_c = math_c[math_c.index("const uint8_t atan2_table"):].replace("atan2(", "reference_atan2(")
    return (source + "\n#define CLAMP(a,min,max) (((a)<(min))?(min):(((a)>(max))?(max):(a)))\n" + math_c +
            "\nunsigned reference_angle(short y,short x){return reference_atan2(y,x);}\n")


REPLAY = r'''const fs=require('fs');
const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const data=JSON.parse(fs.readFileSync(process.argv[3]));
if(data.angles){process.stdout.write(JSON.stringify(data.angles.map(([y,x])=>e.reference_angle(y,x))));process.exit(0);}
e.ref_reset();const out=[];
for(const t of data.ticks){
 data.actors.forEach((a,i)=>{const h=t.terminate.includes(i)?0x8000:(t.first?0x8000:e.ref_get(21,i));
  e.ref_actor(i,a[0],a[1],a[2],a[3],h,i+1<data.actors.length?i+1:255,a[4],a[5],a[6],a[7]);});
 e.ref_begin();
 for(const l of t.launches)e.ref_launch(l[0],l[1],l[2],l[3]);
 e.ref_update(t.time&255,t.scroll[0],t.scroll[1]);
 const hits=[];for(let j=0;j<e.ref_get(19,0);j++)hits.push([e.ref_get(20,2*j),e.ref_get(20,2*j+1)]);
 const live=[];for(let p=e.ref_get(0,0);p!==255;p=e.ref_get(2,p))live.push([e.ref_get(3,p),e.ref_get(4,p),e.ref_get(8,p),e.ref_get(9,p)]);
 out.push({hits,live});}
process.stdout.write(JSON.stringify(out));
'''


def node(out, name, payload):
    write_new(out / f"{name}.json", json.dumps(payload).encode())
    proc = subprocess.run(["node", str(out / "replay.js"), str(out / "oracle.wasm"), str(out / f"{name}.json")],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode:
        raise ValueError(proc.stderr)
    return json.loads(proc.stdout)


def patch_export(export, rel, old, new, manifest):
    path = export / rel
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise ValueError(f"witness anchor changed in {rel}: {old!r}")
    temp = path.with_suffix(".tmp")
    write_new(temp, text.replace(old, new).encode("utf-8"))
    temp.replace(path)
    manifest["sha256"][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(export / "export.json", manifest)


def export_case(out, witness=None, cold=False):
    project = project_case(out, cold)
    if witness == "angle":
        # Event-lowering witness: a constant angle launched a quarter turn off.
        from ngpcraft.gbvm.builder import ScriptBuilder
        original = ScriptBuilder.launch_projectile_in_angle
        ScriptBuilder.launch_projectile_in_angle = lambda self, index, x=0, y=0, angle=0: original(self, index, x, y, angle + 64)
        try:
            manifest = export_project(project, out / "export")
        finally:
            ScriptBuilder.launch_projectile_in_angle = original
    else:
        manifest = export_project(project, out / "export")
    if witness == "group":
        patch_export(out / "export", "src/gbvm.c", "if (ctx) *(ctx->stack_ptr++) = group;",
                     "if (ctx) *(ctx->stack_ptr++) = 2u;", manifest)
    elif witness == "handle":
        patch_export(out / "export", "src/gbvm.c", "    if (!(*handle & SCRIPT_TERMINATED)) return;\n", "", manifest)
    return project, manifest


def run(out, toolchain, emulator, bios, clang, ticks=640, witness=None):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    project_path, manifest = export_case(out, witness)
    build = build_rom(out / "export", toolchain)
    symbols = build["link_audit"]["public_symbols"]
    for needed in ("_ngc_projectile_launch", "_ngc_vm_projectile_hit"):
        if needed not in symbols:
            raise ValueError(f"{needed} not linked")
    project = load_project(project_path)
    scene = project.scenes[0]
    registry = scene_registry(project, scene)
    engine = build_engine_scene(project, scene, projectiles=registry.selections)
    compiled = engine.projectiles
    if list(compiled.definition_indices) != list(range(len(compiled.definitions))):
        raise ValueError("lab expects contiguous projectile types")
    view_h, top = view_of(engine.background)
    # Oracle wasm: released C only (plus the pool harness of the earlier lab).
    clang_out = subprocess.run([clang, "--target=wasm32", "-nostdlib", "-O2", "-fno-builtin", "-Wno-everything",
                                "-Wl,--no-entry", "-Wl,--export-all", "-x", "c", "-", "-o", str(out / "oracle.wasm")],
                               input=oracle_source(compiled.definitions, engine.projectile_capacity),
                               capture_output=True, text=True, timeout=120)
    if clang_out.returncode:
        raise ValueError(clang_out.stderr)
    write_new(out / "replay.js", REPLAY.encode())

    def addr(name):
        return symbols["_" + name]["address"]
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    rom = (out / "export/rom/game.ngc").read_bytes()
    bios_data = Path(bios).read_bytes()
    m = native.NativeMachine(rom, bios=bios_data)
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    m.write(0xA000, bytes(8192))
    m.set_breakpoints([verified_tick(rom, build)])
    word = lambda a: int.from_bytes(m.read(a, 2), "little")
    mem = addr("script_memory")
    slots = {v: manifest["variables"][f"VAR_VARIABLE_{v}"] for v in WATCH + (ANGLE_VAR,)}
    rows, pictures, previous = [], [], None
    for _ in range(ticks):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("tick not reached")
        rows.append({"time": word(addr("ngc_game_time")),
                     "vars": {v: word(mem + 2 * s) for v, s in slots.items()},
                     "actors": [[word(addr("act_x") + 2 * i), word(addr("act_y") + 2 * i)] for i in range(4)],
                     "scroll": [word(addr("ngc_scroll")), word(addr("ngc_scroll") + 2)],
                     "frames_between": None if previous is None else stop.frame_count - previous})
        previous = stop.frame_count
        pictures.append(array("H", (v & 4095 for v in m.framebuffer())))
    # The oracle starts at the entry of tick 0 (no script has run yet).
    first = next(i for i, r in enumerate(rows) if r["time"] >= 1) - 1
    if first < 0 or rows[first]["time"] != 0 or rows[first]["vars"][MARK] != 0:
        raise ValueError("tick 0 entry not observed before the first logic tick")
    if any(b["time"] != a["time"] + 1 for a, b in zip(rows[first:], rows[first + 1:])):
        raise ValueError("game_time is not consecutive at tick entries")
    npc002 = next(s["id"] for _, s in project.by_type["sprite"] if s["name"] == "npc002")
    shots = {k: args for k, args, _ in plan(npc002)}
    index_of = {"player": 0, **{n: i + 1 for i, n in enumerate(ORDER)}}
    actor_rows = rows[first]["actors"]
    sheets_raw = {s["id"]: s for _, s in project.by_type["sprite"]}
    keys = [s.values for s in registry.selections]

    def type_of(args):
        return registry.index({k: v for k, v in args.items()}, "lab")

    # Launch angles first (ATAN2 through the released C), then the replay.
    pending, aims = [], []
    for a, b in zip(rows[first:], rows[first + 1:]):
        for k in range(a["vars"][MARK] + 1, min(b["vars"][MARK], 10) + 1):
            args = shots[k]
            src = b["actors"][index_of[args["actorId"]]]
            pending.append((a["time"], k, args, src, b))
            if args["directionType"] == "target":
                other = b["actors"][index_of[args["targetActorId"]]]
                s16 = lambda v: ((v & 65535) ^ 32768) - 32768
                aims.append([math.trunc(s16(other[1] - src[1]) / 256), math.trunc(s16(other[0] - src[0]) / 256)])
    aim_values = node(out, "aims", {"angles": aims}) if aims else []
    launches, aim_iter = {}, iter(aim_values)
    for time, k, args, src, b in pending:
        kind = args["directionType"]
        if kind == "direction":
            angle = DIR_ANGLE[args["direction"]]
        elif kind == "angle":
            angle = round(math.fmod(args["angle"], 256)) & 255
        elif kind == "anglevar":
            angle = b["vars"][ANGLE_VAR] & 255
        elif kind == "actor":
            angle = DIR_ANGLE[DIRECTIONS[args["otherActorId"]]]
        else:
            angle = next(aim_iter)
        x = (src[0] + math.floor(args["x"] * 32)) & 65535
        y = (src[1] + math.floor(-args["y"] * 32)) & 65535
        launches.setdefault(time, []).append([type_of(args), x, y, angle])
    bounds = engine.bounds
    flags = [8, 8, 8, 0]                     # gun collisions disabled before its first launch
    bound = [1, 1, 1, 0]                     # player, t1, t2 have a collision script
    actors = [[*actor_rows[i], flags[i], bound[i], *bounds[i]] for i in range(4)]
    end_of = {v: i for i, v in END_VARS.items()}
    ticks_in = []
    for n, (a, b) in enumerate(zip(rows[first:], rows[first + 1:])):
        ends = [end_of[v] for v in END_VARS.values() if b["vars"][v] != a["vars"][v]]
        ticks_in.append({"time": a["time"], "first": n == 0, "launches": launches.get(a["time"], []),
                         "terminate": ends, "scroll": [b["scroll"][0], b["scroll"][1]]})
    replay = node(out, "replay", {"actors": actors, "ticks": ticks_in})
    # Hit branches: started at tick t (oracle), first instruction at t + 1,
    # visible at the entry of t + 2.
    expected_events, actual_events, unmapped = [], [], []
    for tick, state in zip(ticks_in, replay):
        for actor, group in state["hits"]:
            if (actor, group) not in HIT_VARS:
                unmapped.append([tick["time"], actor, group])
                continue
            expected_events.append([tick["time"] + 2, HIT_VARS[(actor, group)]])
    for a, b in zip(rows[first:], rows[first + 1:]):
        for v in sorted(set(HIT_VARS.values())):
            for _ in range((b["vars"][v] - a["vars"][v]) & 65535):
                actual_events.append([b["time"], v])
    horizon = rows[-1]["time"]
    expected_events = sorted(e for e in expected_events if e[0] <= horizon)
    actual_events = sorted(actual_events)
    # Pictures: Expected from the editor PNGs; first sprite listed is on top.
    background = background_source(project, scene)
    write_new(out / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in background.pixels))
    projectile_name = sheets_raw[keys[0][0]]["name"]
    steps = {}
    for tick, state in zip(ticks_in, replay):
        engine_dir = {"down": 0, "right": 1, "up": 2, "left": 3}
        entry = [["player", actor_rows[0][0] >> 5, actor_rows[0][1] >> 5,
                  engine_dir[project.settings["startDirection"]], 0]]
        for i, name in enumerate(ORDER, 1):
            entry.append([SHEETS[name], actor_rows[i][0] >> 5, actor_rows[i][1] >> 5, engine_dir[DIRECTIONS[name]], 0])
        for x, y, frame, start in state["live"]:
            d = next(d for definition in compiled.definitions for d, (s, _) in enumerate(definition.animations)
                     if s == start)
            entry.append([projectile_name, x >> 5, y >> 5, d, frame - start])
        steps[tick["time"]] = entry
    source = {"size_px": [background.width, background.height], "view": [top, view_h],
              "actors": [steps.get(s, steps[min(steps)]) for s in range(max(steps) + 1)],
              "project": str(project_path), "default_sprite_mode": project.settings["spriteMode"],
              "sprite_palettes": sprite_palettes(project, scene)}
    write_json(out / "source.json", source)
    oracle = Expected(out, source)
    pixel_errors, checked = [], 0
    # The picture at the entry of tick G was committed at the start of tick
    # G - 1 from the shadow OAM rendered by tick G - 2 (as lab_projectile_scenes).
    for row, picture in zip(rows[first + 2:], pictures[first + 2:]):
        step = row["time"] - 2
        if step not in steps:
            continue
        wanted = oracle.picture(row["scroll"][0], row["scroll"][1], step)
        diff = sum(a != b for a, b in zip(picture, wanted))
        checked += 1
        if diff:
            if len(pixel_errors) in (0, 20):
                write_frame(out / f"mismatch-{step:04d}-actual.ppm", picture)
                write_frame(out / f"mismatch-{step:04d}-expected.ppm", wanted)
            pixel_errors.append({"step": step, "pixels": diff})
    launched = [[t, len(v)] for t, v in sorted(launches.items())]
    result = {"witness": witness, "ticks": len(rows), "first_logic_row": first,
              "launch_ticks": launched, "launches": sum(n for _, n in launched),
              "projectile_types": len(compiled.definitions),
              "hits_expected": expected_events, "hits_actual": actual_events,
              "hits_match": expected_events == actual_events, "unmapped_hits": unmapped,
              "final_vars": rows[-1]["vars"], "pictures_checked": checked,
              "pixel_mismatch_frames": len(pixel_errors), "pixel_first": pixel_errors[:8],
              "max_live": max(len(s["live"]) for s in replay),
              "dropped_frames": [r["time"] for r in rows[first + 2:] if r["frames_between"] != 1],
              "features": manifest["features"],
              "projectile_hit_contexts": manifest["scene_engine"].get("projectile_hit_contexts"),
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "bios_sha256": hashlib.sha256(bios_data).hexdigest(),
              "emulator_core_sha256": hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
              "silicon_timing": {"word_wait": 10, "bios_wait": 8}, "ram": "noise", "tile_ram_cleared": True}
    result["passed"] = bool(result["hits_match"] and not unmapped and not pixel_errors and not result["dropped_frames"]
                            and checked > 0 and result["launches"] == 10)
    write_json(out / "comparison.json", result)
    write_json(out / "rows.json", rows)
    write_json(out / "oracle.json", {"ticks": ticks_in, "replay": replay})
    for tick in sorted({t for t, _ in launched} | {e[0] - 2 for e in expected_events}):
        k = next((i for i, r in enumerate(rows) if r["time"] == tick + 4), None)
        if k is not None:
            write_frame(out / f"frame-{tick + 3:04d}.ppm", pictures[k])
    return result


def modal_run(out, toolchain, emulator, bios, ticks=120, witness=None):
    """Launch then a modal dialogue in the same script pass: ui.c's modal loop
    renders projectiles without projectiles_update, so the shot stays at its
    launch position (never updated). Witness `moving`: modal frame updating them."""
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    project_path = project_case(out, modal=True)
    import ngpcraft.gbvm.emit as emit
    original = emit.modal_frame
    if witness == "moving":
        emit.modal_frame = lambda commit, after, frozen=False: original(commit, after, False)
    elif witness is not None:
        raise ValueError("unknown modal witness")
    try:
        manifest = export_project(project_path, out / "export")
    finally:
        emit.modal_frame = original
    build = build_rom(out / "export", toolchain)
    symbols = build["link_audit"]["public_symbols"]
    project = load_project(project_path)
    scene = project.scenes[0]
    engine = build_engine_scene(project, scene, projectiles=scene_registry(project, scene).selections)
    view_h, top = view_of(engine.background)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    rom = (out / "export/rom/game.ngc").read_bytes()
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    m.write(0xA000, bytes(8192))
    m.set_breakpoints([verified_tick(rom, build)])
    word = lambda a: int.from_bytes(m.read(a, 2), "little")
    stop, _ = m.run(3_000_000, record=False)      # first tick entry: the modal loop never returns
    if stop.stop_status != 40:
        raise ValueError("tick not reached")
    actors = [[word(symbols["_act_x"]["address"] + 2 * i), word(symbols["_act_y"]["address"] + 2 * i)] for i in range(4)]
    m.set_breakpoints([])
    pictures = []
    for _ in range(ticks):
        m.run_frames(1)
        pictures.append(array("H", (v & 4095 for v in m.framebuffer())))
    scroll = [word(symbols["_ngc_scroll"]["address"]), word(symbols["_ngc_scroll"]["address"] + 2)]
    background = background_source(project, scene)
    write_new(out / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in background.pixels))
    engine_dir = {"down": 0, "right": 1, "up": 2, "left": 3}
    entry = [["player", actors[0][0] >> 5, actors[0][1] >> 5, engine_dir[project.settings["startDirection"]], 0]]
    entry += [[SHEETS[n], actors[i][0] >> 5, actors[i][1] >> 5, engine_dir[DIRECTIONS[n]], 0] for i, n in enumerate(ORDER, 1)]
    entry.append(["npc002", (actors[3][0] >> 5) + 16, actors[3][1] >> 5, 1, 0])
    source = {"size_px": [background.width, background.height], "view": [top, view_h], "actors": [entry],
              "project": str(project_path), "default_sprite_mode": project.settings["spriteMode"],
              "sprite_palettes": sprite_palettes(project, scene)}
    wanted = Expected(out, source).picture(scroll[0], scroll[1], 0)
    rows = 80                                     # above the dialogue box
    mismatches = [k for k, p in enumerate(pictures[30:], 30)
                  if any(p[i] != wanted[i] for i in range(rows * 160))]
    write_frame(out / "modal-frame-030.ppm", pictures[30])
    write_frame(out / f"modal-frame-{ticks - 1:03d}.ppm", pictures[-1])
    result = {"witness": witness, "frames_checked": ticks - 30, "rows_checked": rows, "mismatch_frames": len(mismatches),
              "first_mismatch": mismatches[:3], "scroll": scroll, "features": manifest["features"],
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "silicon_timing": {"word_wait": 10, "bios_wait": 8}}
    result["passed"] = not mismatches
    write_json(out / "modal.json", result)
    return result


def cold_run(out, toolchain):
    """Same project without the launches: no pool, no hit handles linked."""
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    _, manifest = export_case(out, cold=True)
    build = build_rom(out / "export", toolchain)
    symbols = build["link_audit"]["public_symbols"]
    result = {"features": manifest["features"],
              "linked": sorted(s for s in symbols if "projectile" in s),
              "passed": not any("projectile" in s for s in symbols) and "projectiles" not in manifest["features"]}
    write_json(out / "cold.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain"):
        p.add_argument("--" + n, required=True)
    for n in ("emulator", "bios", "clang"):
        p.add_argument("--" + n)
    p.add_argument("--witness", choices=("group", "handle", "angle", "moving"))
    p.add_argument("--cold", action="store_true")
    p.add_argument("--modal", action="store_true")
    p.add_argument("--ticks", type=int, default=640)
    a = p.parse_args()
    if a.cold:
        r = cold_run(a.out, a.toolchain)
    elif a.modal:
        r = modal_run(a.out, a.toolchain, a.emulator, a.bios, witness=a.witness)
    else:
        r = run(a.out, a.toolchain, a.emulator, a.bios, a.clang, a.ticks, a.witness)
    print(json.dumps({k: v for k, v in r.items() if k not in ("hits_expected", "hits_actual")}, indent=2))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
