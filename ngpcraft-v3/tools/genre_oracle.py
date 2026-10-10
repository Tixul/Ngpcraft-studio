"""M5 genres proof: ROM against GB Studio's original states with declared adapters.

1. A gbs2 genre scene (Space Battle, Player's House; scripts replaced by
   counters) is exported and built by cc900.
2. The native emulator drives the hardware buttons (port 0xB0) and reads, at
   every game tick entry, the buttons the ROM saw, the player and camera, the
   shmup state and the counters the scripts increment.
3. GBVM's shmup.c / pointnclick.c, camera.c, collision.c, trigger.c and
   actor.c (overlap, player collision) are compiled by Clang to
   wasm32 around tests/genre_reference.c with the export's defines, engine
   field values and collision map; each step is fed the buttons the ROM saw and
   the other actors' positions. Every tick must match, and every script start
   of the reference must show as its counter. Counters observed together do
   not establish the order of calls within one frame. Adapters reproduce
   SDCC unsigned 16-bit cursor bounds and the approved NGPC vertical viewport.

  python -B -m tools.genre_oracle --kind shmup|pnc --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> --clang <clang.exe> [--frames N]
"""
import argparse
import hashlib
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
from tools.demo_actor_moves import GBS2, ev  # noqa: E402
from tools.platform_oracle import c_values, file_locals  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402
from tools.reference import REFERENCE  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.project import load_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402

UP, DOWN, LEFT, RIGHT, A, B = 1, 2, 4, 8, 16, 32
ACTOR_TAG, TRIGGER_TAG, PLAYER_TAG = 100, 200, 900
SCENES = {"shmup": ("space_battle", (0, 9), "right"), "pnc": ("players_house", (9, 6), "up"),
          "adventure": ("sample_town", (12, 20), "down")}
SCENE_TYPE = {"shmup": "SHMUP", "pnc": "POINTNCLICK", "adventure": "ADVENTURE"}
STATE_FILE = {"shmup": "shmup", "pnc": "pointnclick", "adventure": "adventure"}


def compress(values):
    """GB Studio compress8bitNumberString (scene collisions)."""
    out, i = [], 0
    while i < len(values):
        j = i
        while j < len(values) and values[j] == values[i]:
            j += 1
        out.append(f"{values[i]:02x}" + ("!" if j - i == 1 else f"{j - i:x}+"))
        i = j
    return "".join(out)


def inc(eid, var):
    return [ev(eid, "EVENT_INC_VALUE", variable=var)]


def project_case(root, kind, fields=None, walls=(), triggers=(), actors=(), player_hits=False, start=None,
                 direction=None, keep_actors=True, scenery=None, shots=False, script=None):
    """gbs2 scene of `kind`, scripts emptied (tools.demo_gbs_scene.derive), then:
    walls [(x, y, collision value)] in tiles; triggers [(x, y, w, h, enter var
    or enter events, leave var or None)]: counters (or the given events); actors
    [(x, y, group, script var[, collision extra flags])] added in pixels; player_hits: the scene's player hit scripts count in variable "9"
    (playerHit1..3 add 1, 2, 3 to it); keep_actors=False removes gbs2's actors;
    script: the scene's On Init events (e.g. Set Adventure State).
    Returns (project folder, {(tag, argument): variable})."""
    scene, start0, direction0 = SCENES[kind]
    emptied = derive(GBS2, root / "project", scenery or scene, start or start0, fade=False)
    write_json(root / "fixture-source.json", {"reference": str(GBS2), "scene": scenery or scene,
               "kind": kind, "emptied_scripts": emptied, "counter_scripts": True})
    settings_path = root / "project/project/settings.gbsres"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["startDirection"] = direction or direction0
    write_json(settings_path, settings)
    folder = next((root / "project/project/scenes").rglob("scene.gbsres")).parent
    scene_path = folder / "scene.gbsres"
    data = json.loads(scene_path.read_text(encoding="utf-8"))
    data["type"] = SCENE_TYPE[kind]
    if shots:
        from tools.lab_projectile_events import shot
        from ngpcraft.project import load_project
        sprites = {s['name']: s['id'] for _, s in load_project(root/'project').by_type['sprite']}
        fire = ev('genre-fire-input','EVENT_SET_INPUT_SCRIPT',input=['a'],override=True)
        fire['children'] = {'true':[ev('genre-fire','EVENT_LAUNCH_PROJECTILE',
            **shot(sprites['npc002'],actorId='player',collisionMask=[],destroyOnHit=False))]}
        data['script'] = [fire]
    if script is not None:
        data["script"] = list(data["script"] if shots else []) + script
    events = {}
    if walls:
        from ngpcraft.scene_assets import decompress_tile_colors
        tiles = decompress_tile_colors(data["collisions"], str(scene_path), "collisions") if data.get("collisions") else []
        tiles += [0] * (data["width"] * data["height"] - len(tiles))
        for x, y, value in walls:
            tiles[y * data["width"] + x] = value
        data["collisions"] = compress(tiles)
    if player_hits:
        for k in (1, 2, 3):
            data[f"playerHit{k}Script"] = [ev(f"g-hit-{k}-{n}", "EVENT_INC_VALUE", variable="9") for n in range(k)]
        events.update({(PLAYER_TAG, g): "9" for g in (2, 4, 8)})
    write_json(scene_path, data)
    template = json.loads(sorted((GBS2 / "project/scenes").rglob("triggers/*.gbsres"))[0].read_text(encoding="utf-8"))
    if (folder / "triggers").is_dir():
        shutil.rmtree(folder / "triggers")
    (folder / "triggers").mkdir()
    for k, (x, y, w, h, enter, leave) in enumerate(triggers):
        trigger = dict(template, id=f"g-trigger-{k}", symbol=f"trigger_g_{k}", _index=k, prefabId="",
                       prefabScriptOverrides={}, x=x, y=y, width=w, height=h,
                       script=enter if isinstance(enter, list) else inc(f"g-t{k}-in", enter) if enter else [],
                       leaveScript=inc(f"g-t{k}-out", leave) if leave else [])
        write_json(folder / f"triggers/g_trigger_{k:02d}.gbsres", trigger)
        if isinstance(enter, str):
            events[(TRIGGER_TAG + k, 1)] = enter
        if leave:
            events[(TRIGGER_TAG + k, 2)] = leave
    existing = sorted((folder / "actors").glob("*.gbsres")) if (folder / "actors").is_dir() else []
    if not keep_actors:
        for path in existing:
            path.unlink()
        existing = []
    if actors:
        actor_template = json.loads(sorted((GBS2 / "project/scenes").rglob("actors/sign_post.gbsres"))[0]
                                    .read_text(encoding="utf-8"))
        (folder / "actors").mkdir(exist_ok=True)
        for k, (x, y, group, var, *extra) in enumerate(actors):
            actor = dict(actor_template)
            actor.update(id=f"g-actor-{k}", name=f"g actor {k}", symbol=f"actor_g_{k}", _index=len(existing) + k,
                         prefabId="", coordinateType="pixels", x=x, y=y, direction="down", collisionGroup=group,
                         collisionExtraFlags=list(extra[0]) if extra else [], persistent=True, isPinned=False, script=inc(f"g-a{k}", var) if var else [],
                         updateScript=[], hit1Script=[], hit2Script=[], hit3Script=[])
            write_json(folder / f"actors/zz_g_actor_{k:02d}.gbsres", actor)
            if var:
                events[(ACTOR_TAG + 1 + len(existing) + k, 0)] = var
    if fields:
        path = root / "project/project/engine_field_values.gbsres"
        values = json.loads(path.read_text(encoding="utf-8"))
        values["engineFieldValues"] = [f for f in values["engineFieldValues"] if f["id"] not in fields]
        values["engineFieldValues"] += [{"id": k, "value": v} for k, v in fields.items()]
        write_json(path, values)
    return root / "project", events


SHMUP_KEYS = ("shooter_reached_end", "shooter_dest", "shooter_direction")


def rom_trace(export, emulator, bios, frames, schedule, variables=(), kind="shmup", variable_symbols=None):
    rom = (export / "rom/game.ngc").read_bytes()
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    symbols = build["link_audit"]["public_symbols"]
    x_addr, y_addr = symbols["_act_x"]["address"], symbols["_act_y"]["address"]
    source = (export / "src/scene_actors.c").read_text(encoding="utf-8")
    count = int(re.search(r"unsigned short act_x\[(\d+)\];", source).group(1))
    variable_indices = json.loads((export / "export.json").read_text(encoding="utf-8"))["variables"]
    variable_indices = {v: variable_indices[variable_symbols[v]] for v in variables}
    tick = verified_tick(rom, build)
    memory = symbols["_script_memory"]["address"]
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(Path(emulator).resolve()):
        raise ValueError("different native emulator binding")
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    locals_ = file_locals(export, symbols) if kind == "adventure" else {}
    # adventure.c reads adv_next_state when ngc_adventure_update starts: the value
    # its previous update left, or the one a script just wrote (Set Adventure
    # State, Engine Field Set): read there and fed to the reference.
    update = symbols["_ngc_adventure_update"]["address"] if kind == "adventure" else None
    stops = [tick] + ([update] if update is not None else [])
    machine.set_breakpoints(stops)

    def read(address, size, signed=False):
        return int.from_bytes(machine.read(address, size), "little", signed=signed)
    rows = []
    for frame in range(frames + 1):
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status == 40 and update is not None and stop.stop_pc == update and rows:
            rows[-1]["adv_next_state_in"] = read(symbols["_adv_next_state"]["address"], 2)
            machine.set_breakpoints([])
            machine.run(1, record=False)
            machine.set_breakpoints(stops)
            stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40 or stop.stop_pc != tick:
            raise SystemExit(f"tick {frame} not reached")
        row = {"x": read(x_addr, 2), "y": read(y_addr, 2),
               "physical_frame": stop.frame_count, "total_cycles": stop.total_cycles,
               "camera_x": read(symbols["_ngc_camera_x"]["address"], 2),
               "camera_y": read(symbols["_ngc_camera_y"]["address"], 2),
               "actors": [[read(x_addr + 2 * i, 2), read(y_addr + 2 * i, 2)] for i in range(1, count)],
               "ngc_joy": read(symbols["_ngc_joy"]["address"], 1),
               "ngc_joy_pressed": read(symbols["_ngc_joy_pressed"]["address"], 1),
               "vars": {v: read(memory + 2 * variable_indices[v], 2, True) for v in variables}}
        if kind == "shmup":
            row.update({k: read(symbols["_" + k]["address"], 2 if k in ("shooter_dest", "shooter_direction") else 1) for k in SHMUP_KEYS})
        if kind == "adventure":      # UBYTE is 16-bit in the scene engine (scene_platform.PRELUDE)
            row.update({name: read(address, 1) for name, address in locals_.items() if name != "cam_deadzone_x"})
            row["anim_set"] = read(symbols["_ngc_player_anim_set"]["address"], 1) if "_ngc_player_anim_set" in symbols else 0
            row.update({"adv_state": read(symbols["_adv_state"]["address"], 2),
                        "adv_vel_x": read(symbols["_adv_vel_x"]["address"], 2, True),
                        "adv_vel_y": read(symbols["_adv_vel_y"]["address"], 2, True)})
        rows.append(row)
        held = 0
        for start, end, mask in schedule:
            if start <= frame < end:
                held |= mask
        machine.set_breakpoints([])
        machine.run(1, record=False)
        machine.set_breakpoints(stops)
        machine.write(0x00B0, bytes([held]))      # hardware port; never write Sys Lever
    from tools.probe_rom import write_frame
    write_frame(export.parent / "final.ppm", machine.framebuffer())
    from tools.music_rom import png
    (export.parent / "final.png").write_bytes(png(list(machine.framebuffer())))
    write_json(export.parent / "native-proof.json", {
        "core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
        "binding_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
        "rom_sha256": hashlib.sha256(rom).hexdigest(), "silicon_timing": {"word_wait":10,"bios_wait":8},
        "ticks": frames, "ram": "noise", "debugger_only": True,
        "physical_frame_intervals": sorted({rows[i]['physical_frame']-rows[i-1]['physical_frame'] for i in range(2,len(rows))})})
    return rows


# adventure.c needs, beyond the harness (GBVM input.h, math.h, gbs_types.h,
# vm.h, actor.h at bd6f41c; values copied). Not used by shmup.c / pointnclick.c.
ADVENTURE_EXTRA = r"""typedef struct { WORD x, y; } point16_t;
typedef struct { UBYTE script_bank; UBYTE *script_addr; UWORD handle; } script_event_t;
typedef struct { int unused; } SCRIPT_CTX;
#define OLDCALL
#define SCRIPT_TERMINATED 0x8000
#define FN_ARG0 -1
#define FN_ARG1 -2
#define FN_ARG2 -3
#define VM_REF_TO_PTR(idx) ((void *)0)
#define J_SELECT 0x40
#define J_START 0x80
#define INPUT_A (joy & J_A)
#define INPUT_B (joy & J_B)
#define INPUT_PRESSED(btn) (joy_pressed & (btn))
#define INPUT_DPAD (J_UP | J_DOWN | J_LEFT | J_RIGHT)
#define INPUT_RECENT_LEFT ((recent_joy & J_LEFT) || (!recent_joy && (joy & J_LEFT)))
#define INPUT_RECENT_RIGHT ((recent_joy & J_RIGHT) || (!recent_joy && (joy & J_RIGHT)))
#define INPUT_RECENT_UP ((recent_joy & J_UP) || (!recent_joy && (joy & J_UP)))
#define INPUT_RECENT_DOWN ((recent_joy & J_DOWN) || (!recent_joy && (joy & J_DOWN)))
#define MOD_4(a) ((a)&3)
#define FLIPPED_DIR(dir) MOD_4((dir) + 2)
#define IS_DIR_VERTICAL(dir) (!((dir)&01))
#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#define MAX(a, b) (((a) > (b)) ? (a) : (b))
#define CLAMP(a, min, max) (((a) < (min)) ? (min) : (((a) > (max)) ? (max) : (a)))
#define COLLISION_GROUP_FLAG_SOLID 0x80
static void *memset(void *d, int c, unsigned long n) { unsigned char *p = d; while (n--) *p++ = (unsigned char)c; return d; }
static void actor_set_anim_idle(actor_t *a) { a->animation = a->dir; }
static void actor_reset_anim(actor_t *a) { (void)a; }
#define load_animations(ptr, bank, anim, dest) (loaded_set = (anim))
static void point_translate_dir_word(upoint16_t *point, direction_e dir, uint16_t speed) {
    if (dir == DIR_RIGHT) point->x += speed; else if (dir == DIR_LEFT) point->x -= speed;
    else if (dir == DIR_DOWN) point->y += speed; else if (dir == DIR_UP) point->y -= speed;
}
"""


def reference_source(export, kind):
    source = (export / "src/scene_actors.c").read_text(encoding="utf-8")
    core = REFERENCE / "appData/engine/gbvm/src"
    strip = lambda text: re.sub(r"\s+(?:OLDCALL\s+)?(?:BANKED|NONBANKED)\b", "",
                                re.sub(r"^#(?:include|pragma).*$", "", text, flags=re.M))
    state = STATE_FILE[kind]
    defines = "\n".join(f"#define {d}" for d in re.findall(r"^#define (SHOOTER_(?:MOVEMENT_TYPE|TRIGGER_ACTIVATION|WALL_COLLISION_GROUP) \w+)$",
                                                          source.split("/* GBVM states/shmup.c")[1] if "/* GBVM states/shmup.c" in source else "", re.M)[:3])
    width = int(re.search(r"#define NGC_COLL_W (\d+)u", source).group(1))
    height = int(re.search(r"#define NGC_COLL_H (\d+)u", source).group(1))
    coll = c_values(source, "coll_map") if "coll_map[" in source else [0] * (width * height)
    view_h = int(re.search(r"#define SCREEN_HEIGHT (\d+)\n", source).group(1))
    fields = "".join(f"    {name} = (UINT8){value};\n" for name, value in
                     re.findall(r"^    (shooter_scroll_speed) = \(UBYTE\)(\d+);$", source, re.M))
    if kind == "adventure":
        # data/states_defines.h of the export, and the engine field values its boot sets.
        # Only the generated block (scene_genres.state_defines, closed by NGC_ADVENTURE_ANIM_OVERRIDES):
        # adventure.c's own #ifndef defaults follow it.
        generated = source.split("#define NGC_ADVENTURE_ANIM_OVERRIDES")[0]
        defines = "\n".join(re.findall(r"^#define (?:INPUT_ADVENTURE|FEAT_ADVENTURE|ADVENTURE)_\w+.*$", generated, re.M))
        fields = "".join(f"    {name} = ({ctype}){value};\n" for name, ctype, value in
                         re.findall(r"^    (adv_\w+) = \((WORD|UBYTE|BYTE)\)(-?\d+);$", source, re.M))
    actors = int(re.search(r"unsigned short act_x\[(\d+)\];", source).group(1))
    actor_c = (core / "core/actor.c").read_text(encoding="utf-8")
    overlap = re.search(r"actor_t \*actor_overlapping_player_from\(actor_t \*start_actor\) BANKED \{.*?\n\}\n", actor_c, re.S)
    hit = re.search(r"void actors_handle_player_collision\(void\) BANKED \{.*?\n\}", actor_c, re.S)
    trigger_h = (core.parent / "include/trigger.h").read_text(encoding="utf-8")
    at = re.search(r"inline UBYTE trigger_at_intersection\(rect16_t \*bb, upoint16_t \*offset\) \{.*?\n\}\n", trigger_h, re.S)
    trigger_c = strip((core / "core/trigger.c").read_text(encoding="utf-8"))
    trigger_c = (trigger_c.replace("trigger_t triggers[MAX_TRIGGERS];", "").replace("UBYTE triggers_len;", "")
                 .replace("hit_trigger = trigger_at_tile(tx, ty);", "hit_trigger = NO_TRIGGER_COLLISON; (void)tx; (void)ty;"))
    camera = strip((core / "core/camera.c").read_text(encoding="utf-8")).replace(
        "camera_reset();", "camera_deadzone_x = camera_deadzone_y = 0;")
    collision = strip((core / "core/collision.c").read_text(encoding="utf-8"))
    collision = (collision.replace("UBYTE _save = CURRENT_BANK;", "").replace("SWITCH_ROM(collision_bank);", "")
                 .replace("SWITCH_ROM(_save);", "").replace("UBYTE tile_hit_x = 0;", "UBYTE tile_hit_x;")
                 .replace("UBYTE tile_hit_y = 0;", "UBYTE tile_hit_y;"))
    sine = c_values((core.parent / "include/sincos.h").read_text(encoding="utf-8"), "sine_wave")
    body = strip((core / f"states/{state}.c").read_text(encoding="utf-8"))
    if state == "pointnclick":
        # SDCC promotes these sums to unsigned 16-bit int; wasm32 instead
        # promotes ushort to signed 32-bit int. Reproduce that machine width
        # at the four comparisons, especially a negative cursor bound.
        for axis, edge in (("x", "left"), ("x", "right"), ("y", "top"), ("y", "bottom")):
            old = f"PLAYER.pos.{axis} + PLAYER.bounds.{edge} >"
            body = body.replace(old, f"(UWORD)(PLAYER.pos.{axis} + PLAYER.bounds.{edge}) >")
    if state == "shmup":
        delta = (view_h >> 1) - 72
        body = body.replace("MOVEMENT_TYPE_LOCK_PERPENDICULAR ? 64 : camera_offset_y", f"MOVEMENT_TYPE_LOCK_PERPENDICULAR ? {64 + delta} : camera_offset_y").replace("MOVEMENT_TYPE_LOCK_PERPENDICULAR ? -48 : camera_offset_y", f"MOVEMENT_TYPE_LOCK_PERPENDICULAR ? {-48 - delta} : camera_offset_y")
        body += ("\n#define STATE_INIT shmup_init\n#define STATE_UPDATE shmup_update\n"
                 "#define GET_STATE_0 shooter_reached_end\n#define GET_STATE_1 shooter_dest\n#define GET_STATE_2 shooter_direction\n")
    elif state == "adventure":
        body += ("\n#define STATE_INIT adventure_init\n#define STATE_UPDATE adventure_update\n"
                 "#define GET_STATE_0 adv_state\n#define GET_STATE_1 adv_vel_x\n#define GET_STATE_2 adv_vel_y\n")
    else:
        body += ("\n#define STATE_INIT pointnclick_init\n#define STATE_UPDATE pointnclick_update\n"
                 "#define GET_STATE_0 0\n#define GET_STATE_1 0\n#define GET_STATE_2 0\n")
    front = re.search(r"actor_t \*actor_with_script_in_front_of_player\(UBYTE grid_size\) BANKED \{.*?\n\}\n", actor_c, re.S)
    harness = (ENGINE / "tests/genre_reference.c").read_text(encoding="utf-8")
    return (harness.replace("@DEFINES@", defines).replace("@VIEW_H@", str(view_h))
            .replace("@WIDTH@", str(width)).replace("@HEIGHT@", str(len(coll) // width))
            .replace("@MAP@", ",".join(map(str, coll))).replace("@ACTORS@", str(actors))
            .replace("@FIELDS@", fields).replace("@SINE@", ",".join(map(str, sine)))
            .replace("@CAMERA_BODY@", camera).replace("@COLLISION_BODY@", collision)
            .replace("@ACTOR_BODY@", strip(overlap.group(0)))
            .replace("@TRIGGER_BODY@", "static " + strip(at.group(0)).replace("inline ", "") + trigger_c)
            .replace("@HIT_BODY@", strip(hit.group(0)) + "\n").replace("@BODY@", body)
            .replace("@EXTRA@", ADVENTURE_EXTRA if kind == "adventure" else "")
            .replace("@FRONT_BODY@", "static " + strip(front.group(0)) if kind == "adventure" else "")
            + ("void set_next_state(int s) { adv_next_state = (state_e)s; }\n" if kind == "adventure" else "")), source


RUN_JS = r"""const fs=require('fs');
const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2])),{env:{}}).exports;
const c=JSON.parse(fs.readFileSync(process.argv[3]));
c.actors.forEach((a,i)=>e.set_actor(i,...a));c.triggers.forEach((t,i)=>e.set_trigger(i,...t));
e.reset(...c.init);let rows=[];
for(const s of c.steps){s[2].forEach((a,i)=>e.set_pos(i+1,a[0],a[1]));
 if(s[3]>=0&&e.set_next_state)e.set_next_state(s[3]);e.step(s[0],s[1]);rows.push(Array.from({length:13},(_,i)=>e.get(i)));}
const log=Array.from({length:e.log_count()},(_,i)=>[e.log_get(i,0),e.log_get(i,1),e.log_get(i,2)]);
process.stdout.write(JSON.stringify({rows,log}));"""


def reference_run(root, export, project, rom_rows, clang, kind):
    source, c = reference_source(export, kind)
    (root / "reference.c").write_text(source, encoding="utf-8")
    proc = subprocess.run([clang, "--target=wasm32", "-nostdlib", "-O2", "-Wno-everything",
                           "-Wl,--no-entry", "-Wl,--export-all", "-Wl,--allow-undefined",
                           str(root / "reference.c"), "-o", str(root / "reference.wasm")],
                          capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit("clang: " + proc.stderr[-3000:])
    bounds = c_values(c, "act_bounds")
    flags = c_values(c, "act_init_flags")
    groups = c_values(c, "act_collision_group")
    xs, ys, dirs = c_values(c, "act_init_x"), c_values(c, "act_init_y"), c_values(c, "act_init_dir")
    speeds = c_values(c, "act_init_speed")
    scripted = [0] * len(flags)
    for table in ("act_interact_addr", "act_hit_addr", "act_click_addr"):
        if table + "[" in c:
            scripted = [a or b for a, b in zip(scripted, c_values(c, table))]
    # actor_t.script of the player is the scene's combined player hit script.
    player = re.search(r"ngc_vm_hit\((\d+)u, \(unsigned short\)\(act_collision_group", c)
    actors = [[xs[i], ys[i], *bounds[4 * i:4 * i + 4], flags[i] | (0x08 if i == 0 else 0), groups[i],
               (PLAYER_TAG if player else 0) if i == 0 else (ACTOR_TAG + i if scripted[i] else 0)]
              for i in range(len(flags))]
    from ngpcraft.scene_triggers import build_triggers
    triggers = [[t.trigger["x"], t.trigger["x"] + t.trigger["width"] - 1, t.trigger["y"],
                 t.trigger["y"] + t.trigger["height"] - 1,
                 (1 if t.trigger["script"] else 0) | (2 if t.trigger["leaveScript"] else 0), TRIGGER_TAG + t.index]
                for t in build_triggers(project)]
    # Entry k shows the buttons input_update read in tick k-1 and the actor
    # positions its scripts left: reference step k-1 is fed entry k's.
    # Adventure: and the adv_next_state its update read (-1: not read, kept).
    steps = [[r["ngc_joy"], r["ngc_joy_pressed"], r["actors"],
              prev["adv_next_state_in"] if "adv_next_state_in" in prev else -1]
             for prev, r in zip(rom_rows, rom_rows[1:])]
    write_json(root / "steps.json", {"init": [dirs[0], speeds[0]], "actors": actors, "triggers": triggers, "steps": steps})
    (root / "run.js").write_text(RUN_JS, encoding="utf-8")
    proc = subprocess.run(["node", str(root / "run.js"), str(root / "reference.wasm"), str(root / "steps.json")],
                          capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit("node: " + proc.stderr[-2000:])
    return json.loads(proc.stdout)


def compare(rom_rows, ref_rows, kind):
    mismatches = []
    for k, ref in enumerate(ref_rows, start=1):
        rom = rom_rows[k]
        expected = {"x": ref[0], "y": ref[1], "camera_x": ref[3], "camera_y": ref[4]}
        if kind == "shmup":
            expected.update({"shooter_reached_end": ref[9], "shooter_dest": ref[10], "shooter_direction": ref[11]})
        if kind == "adventure":
            expected.update({"adv_state": ref[9], "adv_vel_x": ref[10], "adv_vel_y": ref[11],
                             "act_dir": ref[2], "act_anim": ref[6], "anim_set": ref[12]})
        actual = {key: rom[key] for key in expected}
        if actual != expected:
            mismatches.append({"tick": k, "expected": expected, "actual": actual})
    return mismatches


def run(out, kind, toolchain, emulator, bios, clang, frames, schedule, mutate=None, **case):
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    project, events = project_case(out, kind, **case)
    manifest = export_project(project, out / "export")
    if mutate:
        mutate(out / "export")
    build_rom(out / "export", Path(toolchain))
    write_json(out / "inputs.json", schedule)
    variables = sorted(set(events.values()), key=int)
    loaded = load_project(project)
    aliases = {v["id"]: v["symbol"].upper() for _, resource in loaded.by_type["variables"]
               for v in resource["variables"]}
    rom_rows = rom_trace(out / "export", emulator, bios, frames, schedule, variables, kind, aliases)
    ref = reference_run(out, out / "export", loaded, rom_rows, clang, kind)
    mismatches = compare(rom_rows, ref["rows"], kind)
    expected = [(tick, events[(tag, arg)]) for tick, tag, arg in ref["log"] if (tag, arg) in events
                for _ in range({2: 1, 4: 2, 8: 3}[arg] if tag == PLAYER_TAG else 1)]
    expected.sort(key=lambda pair: (pair[0], int(pair[1])))
    unexpected = [[tick, tag, arg] for tick, tag, arg in ref["log"] if (tag, arg) not in events]
    seen = []
    for k in range(1, len(rom_rows)):
        for var in variables:
            for _ in range(rom_rows[k]["vars"][var] - rom_rows[k - 1]["vars"][var]):
                seen.append((k, var))
    delays = sorted({k - tick for (tick, _), (k, _) in zip(expected, seen)})
    result = {"ticks": len(ref["rows"]), "mismatches": len(mismatches), "first": mismatches[:3],
              "script_starts": len(ref["log"]), "expected_counter_increments": len(expected), "kinds": [v for _, v in expected],
              "counter_sequence_matches": [v for _, v in expected] == [v for _, v in seen], "seen": len(seen),
              "unexpected_reference_starts": unexpected[:5], "delays": delays,
              "passed": not mismatches and not unexpected and [v for _, v in expected] == [v for _, v in seen]
                        and all(0 <= d <= 3 for d in delays),
              "features": manifest["features"],
              "x_range": [min(r[0] for r in ref["rows"]), max(r[0] for r in ref["rows"])],
              "y_range": [min(r[1] for r in ref["rows"]), max(r[1] for r in ref["rows"])],
              "reached_end": any(r[9] for r in ref["rows"]) if kind == "shmup" else None}
    write_json(out / "comparison.json", {**result, "expected": expected, "seen": seen, "log": ref["log"],
                                          "rom": rom_rows, "reference": ref["rows"]})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kind", choices=("shmup", "pnc", "adventure"), required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--clang", default=os.environ.get("NGC_TEST_CLANG"))
    p.add_argument("--frames", type=int, default=600)
    args = p.parse_args()
    if not args.clang:
        p.error("--clang or NGC_TEST_CLANG is required")
    if args.frames < 1:
        p.error("--frames must be positive")
    schedule = [(10, 50, UP|LEFT), (60, 100, DOWN|RIGHT), (110, 150, UP|RIGHT),
                (160, 200, DOWN|LEFT), (220, 260, LEFT), (280, 320, RIGHT),
                (340, 380, UP), (400, 440, DOWN), (460, 461, A)]
    result = run(args.out, args.kind, args.toolchain, args.emulator, args.bios, args.clang,
                 args.frames, schedule, keep_actors=False)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
