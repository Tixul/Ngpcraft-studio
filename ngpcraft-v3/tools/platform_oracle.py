"""M3 platformer proof: the ROM against GB Studio's unmodified platform.c.

1. A GB Studio platformer scene (gbs2 path_to_sample_town, scripts emptied,
   no triggers or other actors) is exported and built by cc900.
2. The native emulator drives the hardware buttons (port 0xB0) and reads, at
   every game tick entry, the buttons the ROM saw (ngc_joy, ngc_joy_pressed),
   the player position (act_x[0], act_y[0]: first objects of scene_actors'
   RAM, checked against ngc_game_time's link address) and platform.c's state.
3. GBVM's platform.c and collision.c, unmodified, are compiled by Clang to
   wasm32 around tests/platform_reference.c, with the export's
   states_defines.h, engine field values and collision map; each step is fed
   the buttons the ROM saw. Every tick must match.

  python -B -m tools.platform_oracle --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> [--frames N]
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
from tools.demo_gbs_scene import derive, write_json  # noqa: E402
from tools.demo_actor_moves import GBS2  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402
from tools.reference import REFERENCE  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.project import load_project  # noqa: E402
from ngpcraft.scene_platform import field_values, runtime, state_defines  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402

# Port 0xB0 bits (docs/M2_FOOT_GROUND.md): 1 up, 2 down, 4 left, 8 right, 16 A, 32 B.
UP, DOWN, LEFT, RIGHT, A, B = 1, 2, 4, 8, 16, 32
# Walk, stop, run (B), jumps (tap and held A), slope up and down, left, a jump
# while running, a fall from the roof, drop-through attempt (down + A).
SCHEDULE = [[30, 110, RIGHT], [60, 64, A], [130, 200, RIGHT | B], [150, 175, A],
            [220, 300, LEFT], [240, 243, A], [320, 420, RIGHT | B], [330, 334, A],
            [440, 470, LEFT | B], [445, 470, A], [500, 520, DOWN], [505, 508, DOWN | A],
            [540, 600, LEFT], [560, 590, A], [620, 700, RIGHT]]
# Moving platform (project_case platform=RIDE_PLATFORM): wait under it, jump,
# ride it both ways, walk off its end, jump back on, jump off.
RIDE_PLATFORM = ("platform", 96, 136, 104)
RIDE = [[20, 70, RIGHT], [100, 104, A], [150, 154, A], [262, 300, LEFT], [330, 334, A],
        [360, 400, RIGHT], [420, 424, A], [430, 470, RIGHT]]
FIELDS_KEYS = ("ngc_joy", "ngc_joy_pressed", "plat_state", "plat_vel_x", "plat_vel_y", "plat_grounded")


def encode_collisions(values):
    """GB Studio's compress8bitNumberString, the inverse of decompress_tile_colors:
    a value then `!` (one) or `count+` (hex)."""
    out, k = [], 0
    while k < len(values):
        n = 1
        while k + n < len(values) and values[k + n] == values[k]:
            n += 1
        out.append(f"{values[k]:02x}" + ("!" if n == 1 else f"{n:x}+"))
        k += n
    return "".join(out)


def project_case(root, start=(4, 10), platform=None, fields=None, script=None, tiles=None):
    """platform: None, or (flag, x0, x1, y) in pixels: one actor with that
    collision extra flag ("platform" or "solid"), persistent, moving between x0
    and x1 by an ordinary On Update loop (two Move To events). script: the
    scene's On Init events (e.g. a Camera Move To that stops the follow).
    tiles: {(x, y): collision value} written over the scene's (ladders...)."""
    derive(GBS2, root / "project", "path_to_sample_town", start, fade=False)
    folder = next((root / "project/project/scenes").rglob("scene.gbsres")).parent
    template = json.loads(sorted((folder / "actors").glob("*.gbsres"))[0].read_text(encoding="utf-8"))
    for sub in ("triggers", "actors"):
        if (folder / sub).is_dir():
            shutil.rmtree(folder / sub)
    if platform is not None:
        from tools.demo_actor_moves import ev, num
        flag, x0, x1, y = platform
        actor = dict(template)
        actor.update(id="m3-platform", name="Moving platform", symbol="actor_m3_platform", _index=0,
                     prefabId="", coordinateType="pixels", x=x0, y=y, direction="right", moveSpeed=1,
                     animSpeed=15, collisionGroup="", collisionExtraFlags=[flag], persistent=True,
                     isPinned=False, script=[], hit1Script=[], hit2Script=[], hit3Script=[],
                     updateScript=[ev(f"m3-move-{i}", "EVENT_ACTOR_MOVE_TO", actorId="$self$", x=num(px), y=num(y),
                                      units="pixels", collideWith=[], moveType="horizontal", lockDirection=[])
                                   for i, px in enumerate((x1, x0))])
        (folder / "actors").mkdir()
        write_json(folder / "actors/m3_platform.gbsres", actor)
    if fields:                     # engine field values set in the project, e.g. {"PLATFORM_JUMP_ANIM": "Hurt"}
        path = root / "project/project/engine_field_values.gbsres"
        values = json.loads(path.read_text(encoding="utf-8"))
        values["engineFieldValues"] = [f for f in values["engineFieldValues"] if f["id"] not in fields]
        values["engineFieldValues"] += [{"id": k, "value": v} for k, v in fields.items()]
        write_json(path, values)
    if script is not None or tiles:
        from ngpcraft.scene_assets import decompress_tile_colors
        scene = json.loads((folder / "scene.gbsres").read_text(encoding="utf-8"))
        if script is not None:
            scene["script"] = script
        if tiles:
            size = scene["width"] * scene["height"]
            values = decompress_tile_colors(scene["collisions"], "oracle scene", "collisions")
            values = (values + [0] * size)[:size]
            for (x, y), value in tiles.items():
                values[y * scene["width"] + x] = value
            scene["collisions"] = encode_collisions(values)
        write_json(folder / "scene.gbsres", scene)
    return root / "project"


def c_values(source, name):
    m = re.search(r"\b" + name + r"\[\d+\] = \{(.*?)\};", source, re.S)
    if not m:
        raise ValueError(f"{name} not found in scene_actors.c")
    return [int(v, 0) for v in re.findall(r"-?0x[0-9A-Fa-f]+|-?\d+", m.group(1))]


def player_addresses(export):
    """act_x[0] / act_y[0] at their link addresses (public in scene_actors.c)."""
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    symbols = build["link_audit"]["public_symbols"]
    return symbols["_act_x"]["address"], symbols["_act_y"]["address"], build


# PLAYER.dir, PLAYER.animation and camera_deadzone_x: file-local variables of
# scene_actors.c, absent from the link map. cc900 places that file's variables
# in declaration order, each rounded up to a word: in each block (up to its
# first blank line) the addresses follow its first (public) variable, and every
# other public variable of the block must land where that rule puts them.
# (first public variable, file-locals read)
LOCAL_BLOCKS = (("act_x", ("act_dir", "act_anim")), ("ngc_camera_x", ("cam_deadzone_x",)))
DECLARATION = re.compile(r"^(?:static )?(?:unsigned |signed )?(char|short) (\w+)(?:\[(\d+)\])?;$", re.M)


def file_locals(export, symbols):
    source = (export / "src/scene_actors.c").read_text(encoding="utf-8")
    out = {}
    for first, names in LOCAL_BLOCKS:
        begin = source.index(f"\nunsigned short {first}")
        block = source[begin:source.index("\n\n", begin)]
        address, found = symbols["_" + first]["address"], {}
        for ctype, name, count in DECLARATION.findall(block):
            found[name] = address
            size = (2 if ctype == "short" else 1) * (int(count) if count else 1)
            address += size + (size & 1)
        checks = [name for name in found if name != first and "_" + name in symbols]
        if not checks or any(found[name] != symbols["_" + name]["address"] for name in checks):
            raise ValueError(f"{first}: declaration-order layout does not match the link map")
        for name in names:
            # Camera Set Property makes the camera state public (scene_engine: camera_*).
            public = "_camera_" + name[4:] if name.startswith("cam_") else None
            out[name] = found[name] if name in found else symbols[public]["address"]
    return out


def rom_trace(export, emulator, bios, frames, schedule, variables=()):
    rom = (export / "rom/game.ngc").read_bytes()
    x_addr, y_addr, build = player_addresses(export)
    count = (y_addr - x_addr) // 2
    tick = verified_tick(rom, build)
    symbols = build["link_audit"]["public_symbols"]
    if variables:
        manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
        memory = symbols["_script_memory"]["address"]
        # A GB Studio global variable id "N" is script_memory[N]; the export names it.
        var_index = {v: int(v) for v in variables}
        if sorted(manifest["variables"].values())[:len(variables)] != sorted(var_index.values()):
            raise ValueError("variables of the export do not match the probed ids")
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    # platform.c reads scroll_x when ngc_platform_update starts: the previous
    # tick's scroll, or the one an instant camera move just set (vm_camera_set_pos
    # calls scroll_update before the state update).
    plat = symbols["_ngc_platform_update"]["address"]
    locals_ = file_locals(export, symbols)
    machine.set_breakpoints([tick, plat])
    words = {"plat_vel_x", "plat_vel_y"}

    def read(address, size, signed=False):
        return int.from_bytes(machine.read(address, size), "little", signed=signed)
    rows = []
    for frame in range(frames + 1):
        stop, _ = machine.run(3_000_000, record=False)
        while stop.stop_status == 40 and stop.stop_pc == plat:
            if rows:
                rows[-1]["platform_scroll_x"] = read(symbols["_ngc_scroll_next"]["address"], 2)
            stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40 or stop.stop_pc != tick:
            raise SystemExit(f"tick {frame} not reached")
        row = {"x": read(x_addr, 2), "y": read(y_addr, 2),
               "actors": [[read(x_addr + 2 * i, 2), read(y_addr + 2 * i, 2)] for i in range(1, count)],
               "plat_is_actor_attached": read(symbols["_plat_is_actor_attached"]["address"], 1),
               **{name: read(address, 1, name == "cam_deadzone_x") for name, address in locals_.items()},
               "anim_set": read(symbols["_ngc_player_anim_set"]["address"], 1) if "_ngc_player_anim_set" in symbols else 0}
        for key in FIELDS_KEYS:
            row[key] = read(symbols["_" + key]["address"], 2 if key in words else 1, key in words)
        if variables:              # GBVM variable i = script_memory[i] (vm.h VM_GLOBAL)
            row["vars"] = {v: read(memory + 2 * var_index[v], 2, True) for v in variables}
        rows.append(row)
        held = 0
        for start, end, mask in schedule:
            if start <= frame < end:
                held |= mask
        machine.write(0x00B0, bytes([held]))      # hardware port; never write Sys Lever
    return rows


def reference_source(export, project):
    data = runtime()
    source = (export / "src/scene_actors.c").read_text(encoding="utf-8")
    coll = c_values(source, "coll_map")
    width = int(re.search(r"#define NGC_COLL_W (\d+)u", source).group(1))
    core = REFERENCE / "appData/engine/gbvm/src"
    strip = lambda text: re.sub(r"^#(?:include|pragma).*$", "", text, flags=re.M)
    defines = "\n".join(l for l in state_defines(project, data) if not l.startswith("#define NGC_"))
    fields = "\n".join(f"    {key} = ({ctype}){value};" for ctype, key, value in field_values(project, data))
    harness = (ENGINE / "tests/platform_reference.c").read_text(encoding="utf-8")
    camera = re.search(r"cam_settings = (\d+)u;", source)
    actors = int(re.search(r"unsigned short act_x\[(\d+)\];", source).group(1))
    actor_c = (core / "core/actor.c").read_text(encoding="utf-8")
    overlap = re.search(r"actor_t \*actor_overlapping_player_from\(actor_t \*start_actor\) BANKED \{.*?\n\}\n",
                        actor_c, re.S)
    hit = re.search(r"void actors_handle_player_collision\(void\) BANKED \{.*?\n\}", actor_c, re.S)   # last in file
    trigger_h = (core.parent / "include/trigger.h").read_text(encoding="utf-8")
    at = re.search(r"inline UBYTE trigger_at_intersection\(rect16_t \*bb, upoint16_t \*offset\) \{.*?\n\}\n", trigger_h, re.S)
    trigger_c = (core / "core/trigger.c").read_text(encoding="utf-8")
    activate = re.search(r"UBYTE trigger_activate_at_intersection\(rect16_t \*bb, upoint16_t \*offset, UBYTE force\) "
                         r"BANKED \{.*?\n\}\n", trigger_c, re.S)
    return (harness.replace("@DEFINES@", defines).replace("@ACTORS@", str(actors))
            .replace("@ACTOR_BODY@", overlap.group(0))
            .replace("@TRIGGER_BODY@", "static " + at.group(0) + activate.group(0))
            .replace("@HIT_BODY@", hit.group(0) + "\n")
            .replace("@COLLISION_BODY@", strip((core / "core/collision.c").read_text(encoding="utf-8"))
                     .replace("UBYTE tile_hit_x = 0;", "UBYTE tile_hit_x;").replace("UBYTE tile_hit_y = 0;", "UBYTE tile_hit_y;"))
            .replace("@BODY@", strip((core / "states/platform.c").read_text(encoding="utf-8")))
            .replace("@FIELDS@", fields).replace("@MAP_SIZE@", str(len(coll)))
            .replace("@MAP@", ",".join(map(str, coll))).replace("@WIDTH@", str(width))
            .replace("@HEIGHT@", str(len(coll) // width))
            .replace("@CAMERA_SETTINGS@", camera.group(1) if camera else "3")), source


RUN_JS = r"""const fs=require('fs');
const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const c=JSON.parse(fs.readFileSync(process.argv[3]));
e.reset(...c.init);c.triggers.forEach((t,i)=>e.set_trigger(i,...t));c.effects.forEach((f,i)=>e.set_effect(i,...f));let rows=[];
for(const s of c.steps){s[2].forEach((a,i)=>e.set_actor(i+1,a[0],a[1],...c.actors[i]));
 e.set_scroll(s[3]);e.step(s[0],s[1]);rows.push(Array.from({length:13},(_,i)=>e.get(i)));}
const log=Array.from({length:e.log_count()},(_,i)=>[e.log_get(i,0),e.log_get(i,1),e.log_get(i,2)]);
process.stdout.write(JSON.stringify({rows,log}));"""

# Script tags of the reference log (tests/platform_reference.c script_execute).
ACTOR_TAG, TRIGGER_TAG = 100, 200


def reference_run(root, export, project, rom_rows, clang, effects=()):
    """effects: [(script tag, argument, kind, value)], tests/platform_reference.c set_effect."""
    source, c = reference_source(export, project)
    (root / "reference.c").write_text(source, encoding="utf-8")
    proc = subprocess.run([clang, "--target=wasm32", "-nostdlib", "-ffreestanding", "-O2", "-Wno-everything",
                           "-Wl,--no-entry", "-Wl,--export-all", str(root / "reference.c"),
                           "-o", str(root / "reference.wasm")], capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit("clang: " + proc.stderr[-3000:])
    x0, y0 = c_values(c, "act_init_x")[0], c_values(c, "act_init_y")[0]
    bounds = c_values(c, "act_bounds")[:4]
    init = [x0, y0, *bounds, c_values(c, "act_init_dir")[0]]
    flags, groups, boxes = c_values(c, "act_init_flags"), c_values(c, "act_collision_group"), c_values(c, "act_bounds")
    # actor_t.script: the interaction script, or the hit script for an actor
    # with a collision group (the export's address tables say which exist).
    scripted = [a or b for a, b in zip(c_values(c, "act_interact_addr") if "act_interact_addr[" in c else [0] * len(flags),
                                       c_values(c, "act_hit_addr") if "act_hit_addr[" in c else [0] * len(flags))]
    actors = [[*boxes[4 * i:4 * i + 4], flags[i], groups[i], ACTOR_TAG + i if scripted[i] else 0]
              for i in range(1, len(flags))]
    # trigger_t from the export's trigger selection (compileData.ts order and bounds).
    from ngpcraft.scene_triggers import build_triggers
    triggers = [[t.trigger["x"], t.trigger["x"] + t.trigger["width"] - 1, t.trigger["y"],
                 t.trigger["y"] + t.trigger["height"] - 1,
                 (1 if t.trigger["script"] else 0) | (2 if t.trigger["leaveScript"] else 0), TRIGGER_TAG + t.index]
                for t in build_triggers(project)]
    # Entry k shows the buttons input_update read in tick k-1 and the actor
    # positions its scripts left, both used by its platform_update: reference
    # step k-1 is fed entry k's buttons and actor positions, and the scroll_x
    # its platform_update read (gb_scroll_x, file-local: ngc_actors_scroll and
    # the camera set copy ngc_scroll_next[0]); 0 when the update did not run.
    steps = [[r["ngc_joy"], r["ngc_joy_pressed"], r["actors"],
              prev["platform_scroll_x"] if "platform_scroll_x" in prev else 0]
             for prev, r in zip(rom_rows, rom_rows[1:])]
    write_json(root / "steps.json", {"init": init, "actors": actors, "triggers": triggers,
                                     "effects": [list(e) for e in effects], "steps": steps})
    (root / "run.js").write_text(RUN_JS, encoding="utf-8")
    proc = subprocess.run(["node", str(root / "run.js"), str(root / "reference.wasm"), str(root / "steps.json")],
                          capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit("node: " + proc.stderr[-2000:])
    return json.loads(proc.stdout)


def compare(rom_rows, ref_rows):
    mismatches = []
    for k, ref in enumerate(ref_rows, start=1):
        rom = rom_rows[k]
        expected = {"x": ref[0], "y": ref[1], "plat_state": ref[3], "plat_vel_x": ref[4],
                    "plat_vel_y": ref[5], "plat_grounded": ref[6] & 0xFF, "anim_set": ref[11]}
        if "act_dir" in rom:
            expected.update(act_dir=ref[2], act_anim=ref[8], cam_deadzone_x=ref[12])
        actual = {key: rom[key] for key in expected}
        if actual != expected:
            mismatches.append({"tick": k, "expected": expected, "actual": actual})
    return mismatches


def run(out, toolchain, emulator, bios, clang, frames=720, schedule=SCHEDULE, mutate=None, platform=None,
        fields=None, script=None, tiles=None):
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    project = project_case(out, platform=platform, fields=fields, script=script, tiles=tiles)
    export_project(project, out / "export")
    if mutate:
        mutate(out / "export")
    build_rom(out / "export", Path(toolchain))
    write_json(out / "inputs.json", schedule)
    rom_rows = rom_trace(out / "export", emulator, bios, frames, schedule)
    ref_rows = reference_run(out, out / "export", load_project(project), rom_rows, clang)["rows"]
    mismatches = compare(rom_rows, ref_rows)
    states = sorted({r[3] for r in ref_rows})
    result = {"ticks": len(ref_rows), "mismatches": len(mismatches), "first": mismatches[:5],
              "states_seen": states, "max_x": max(r[0] for r in ref_rows),
              "min_y": min(r[1] for r in ref_rows), "slopes_touched": any(r[7] for r in ref_rows),
              "attached_ticks": sum(1 for r in ref_rows if r[10]),
              "anim_sets_seen": sorted({r[11] for r in ref_rows})}
    write_json(out / "comparison.json", {**result, "rom": rom_rows, "reference": ref_rows})
    return result


def scripted_case(root, kind="basic", state="jump", fields=None):
    """path_to_sample_town with GB Studio scripts. Returns the project,
    {(reference script tag, argument): variable it increments} and the effects
    [(tag, argument, kind, value)] the reference applies (platform_reference.c).

    basic: a trigger (enter: variable 0 += 1, leave: variable 1 += 1), an actor
    to talk to (variable 2 += 1) and an actor of collision group 1 that hurts
    (On Hit: variable 3 += 1).
    effects: platformer events in two triggers: Player Bounce (high), Engine
    Field Set plat_walk_vel = 3000 and, on leave, Set Platformer State (state).
    fields: engine field values set in the project (project_case)."""
    from tools.demo_actor_moves import ev
    project = project_case(root, fields=fields)
    folder = next((project / "project/scenes").rglob("scene.gbsres")).parent
    template = json.loads(sorted((GBS2 / "project/scenes").rglob("triggers/*.gbsres"))[0].read_text(encoding="utf-8"))
    inc = lambda eid, var: [ev(eid, "EVENT_INC_VALUE", variable=var)]
    (folder / "triggers").mkdir()
    if kind == "effects":
        number = lambda v: {"type": "number", "value": v}
        specs = (("bounce", 13, [ev("m3-b", "EVENT_PLAYER_BOUNCE", height="high")] + inc("m3-b-n", "0"), []),
                 ("field", 6, [ev("m3-f", "EVENT_ENGINE_FIELD_SET", engineFieldKey="plat_walk_vel",
                                  value=number(3000))] + inc("m3-f-n", "1"),
                  [ev("m3-s", "EVENT_PLATFORMER_STATE_SET", state=state)] + inc("m3-s-n", "2")))
        for k, (name, x, enter, leave) in enumerate(specs):
            trigger = dict(template, id=f"m3-{name}", symbol=f"trigger_m3_{name}", _index=k, prefabId="",
                           prefabScriptOverrides={}, x=x, y=12, width=2, height=2, script=enter, leaveScript=leave)
            write_json(folder / f"triggers/m3_{name}.gbsres", trigger)
        consts = runtime()["consts"]
        events = {(TRIGGER_TAG, 1): "0", (TRIGGER_TAG + 1, 1): "1", (TRIGGER_TAG + 1, 2): "2"}
        effects = [(TRIGGER_TAG, 1, 0, -0x600 * 16), (TRIGGER_TAG + 1, 1, 2, 3000),
                   (TRIGGER_TAG + 1, 2, 1, consts[f"PLATFORM_{state.upper()}_STATE"])]
        return project, events, effects
    trigger = dict(template, id="m3-trigger", symbol="trigger_m3", _index=0, prefabId="", prefabScriptOverrides={},
                   x=13, y=12, width=2, height=2, script=inc("m3-t-in", "0"), leaveScript=inc("m3-t-out", "1"))
    write_json(folder / "triggers/m3_trigger.gbsres", trigger)
    actor_template = json.loads(sorted((GBS2 / "project/scenes").rglob("actors/sign_post.gbsres"))[0]
                                .read_text(encoding="utf-8"))
    (folder / "actors").mkdir()
    for k, (name, x, group, var) in enumerate((("talker", 56, "", "2"), ("hurter", 152, "1", "3"))):
        actor = dict(actor_template)
        actor.update(id=f"m3-{name}", name=name, symbol=f"actor_m3_{name}", _index=k, prefabId="",
                     coordinateType="pixels", x=x, y=104, direction="down", collisionGroup=group,
                     collisionExtraFlags=[], persistent=True, isPinned=False, script=inc(f"m3-{name}", var),
                     updateScript=[], hit1Script=[], hit2Script=[], hit3Script=[])
        write_json(folder / f"actors/m3_{name}.gbsres", actor)
    events = {(TRIGGER_TAG, 1): "0", (TRIGGER_TAG, 2): "1", (ACTOR_TAG + 1, 0): "2", (ACTOR_TAG + 2, 0): "3"}
    return project, events, []


# Walk right into the trigger, talk to the sign (A in front of it), walk on
# through the trigger and into the hurting actor, stay on it (iframes), come
# back left into the trigger, tap up twice inside it (forced re-entry: enter
# script again, no leave), leave it.
SCRIPTED = [[20, 40, RIGHT], [48, 50, A], [60, 160, RIGHT], [200, 260, LEFT], [285, 287, UP],
            [300, 302, UP], [320, 340, LEFT], [345, 347, A]]


# Effects case: right through the bounce trigger, back left through it again
# into the field trigger (walk speed changes), out of it (jump state forced).
EFFECTS = [[20, 140, RIGHT], [180, 300, LEFT], [300, 330, RIGHT]]


def run_scripted(out, toolchain, emulator, bios, clang, frames=420, schedule=SCRIPTED, mutate=None, kind="basic",
                 apply_effects=True, state="jump", fields=None):
    """Platformer ROM with scripts against platform.c, trigger.c and
    actors_handle_player_collision: same positions every tick, and every
    script start of the reference shows as the variable it increments, in the
    same order, each at the same delay (the VM runs a started thread at the
    next tick)."""
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    project, events, effects = scripted_case(out, kind, state, fields)
    manifest = export_project(project, out / "export")
    if mutate:
        mutate(out / "export")
    build_rom(out / "export", Path(toolchain))
    write_json(out / "inputs.json", schedule)
    rom_rows = rom_trace(out / "export", emulator, bios, frames, schedule,
                         variables=sorted(set(events.values())))
    ref = reference_run(out, out / "export", load_project(project), rom_rows, clang, effects if apply_effects else ())
    mismatches = compare(rom_rows, ref["rows"])
    expected = [(tick, events[(tag, arg)]) for tick, tag, arg in ref["log"]]
    seen = []
    for k in range(1, len(rom_rows)):
        for var in sorted(set(events.values())):
            for _ in range(rom_rows[k]["vars"][var] - rom_rows[k - 1]["vars"][var]):
                seen.append((k, var))
    delays = sorted({k - tick for (tick, _), (k, _) in zip(expected, seen)})
    result = {"ticks": len(ref["rows"]), "mismatches": len(mismatches), "first": mismatches[:5],
              "script_starts": len(expected), "kinds": [v for _, v in expected],
              "same_order": [v for _, v in expected] == [v for _, v in seen], "delays": delays,
              "features": manifest["features"]}
    write_json(out / "comparison.json", {**result, "expected": expected, "seen": seen, "log": ref["log"],
                                         "reference": ref["rows"]})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--clang", default=os.environ.get("NGC_TEST_CLANG"))
    p.add_argument("--frames", type=int, default=720)
    args = p.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.clang, args.frames)
    print(json.dumps(result, indent=2))
    return 0 if result["mismatches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
