"""GBVM states/platform.c on the scene engine (M3).

The state machine is GB Studio's own file, vendored by tools/extract_platform.py
(ngpcraft/runtime/platform_runtime.json): C89 hoisting and listed adaptations
only. This module writes what GBVM's headers and generated data give it:
- the defines of data/states_defines.h (compileStateDefines, generateGBVMData.ts):
  a checked checkbox is `#define KEY`, an animation state its index, any other
  define field `#define KEY value`;
- the engine field variables, set once at boot as script_engine_init does;
- the GBVM names the file uses (math.h, input.h, camera, actor helpers),
  bound to the scene engine's arrays and helpers in scene_actors.c.
"""
import json
import re
from pathlib import Path

from .background_rom import c_array
from .compiler import CompileError
from .world import at_pos_width as boxes, wide

RUNTIME = Path(__file__).with_name("runtime") / "platform_runtime.json"

# GBVM headers (bd6f41c): math.h, input.h, camera.h, collision.h, gbs_types.h,
# actor.h, vm.h. Values copied; the GB Studio names stay so platform.c reads as is.
PRELUDE = r"""/* GBVM states/platform.c on the scene engine (ngpcraft/scene_platform.py).
 * Names below: GBVM math.h, input.h, camera.h, collision.h, gbs_types.h,
 * actor.h and vm.h at bd6f41c, bound to the actor arrays above. */
/* GBVM is written for SDCC, which promotes char operands to its 16-bit int
 * before arithmetic; cc900 computes them on 8 bits (u8 + u8, u8 << 8 lose
 * their carry). The platformer's own UBYTE/BYTE are therefore 16-bit here,
 * which gives the Game Boy's int arithmetic; the unit macros below truncate
 * to 8 bits where GBVM casts, then widen. */
#define UBYTE unsigned short
#define BYTE short
#define UWORD unsigned short
#define WORD short
#define TRUE 1
#define FALSE 0
#define true 1
#define false 0
#define WORD_MIN (-32767 - 1)
#ifndef NULL
#define NULL 0
#endif
#define SUBPX_TO_PX(a) ((a) >> 5)
#define SUBPX_TO_TILE(a) ((UWORD)(unsigned char)((a) >> 8))
#define PX_TO_SUBPX(a) ((a) << 5)
#define TILE_TO_SUBPX(a) (((UWORD)(unsigned char)(a)) << 8)
#define SUBPX_TILE_REMAINDER(a) ((UWORD)(unsigned char)((a) & 0xFF))
#define EXCLUSIVE_OFFSET(x) ((x) + 1)
#define IS_NEG(a) ((unsigned char)(a)&0x80)
#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#define MAX(a, b) (((a) > (b)) ? (a) : (b))
#define CLAMP(a, min, max) (((a) < (min)) ? (min) : (((a) > (max)) ? (max) : (a)))
#define DIV_2(a) ((a) >> 1)
#define IS_DIR_HORIZONTAL(dir) ((dir)&01)
#define IS_DIR_VERTICAL(dir) (!((dir)&01))
#define SET_FLAG(n, f) ((n) |= (f))
#define CLR_FLAG(n, f) ((n) &= ~(f))
#define CHK_FLAG(n, f) ((n) & (f))
#define DIR_DOWN 0
#define DIR_RIGHT 1
#define DIR_UP 2
#define DIR_LEFT 3
#define DIR_NONE 4
#define ANIM_JUMP_LEFT 0
#define ANIM_JUMP_RIGHT 2
#define ANIM_CLIMB 6
#define COLLISION_TOP 0x1
#define COLLISION_BOTTOM 0x2
#define COLLISION_LEFT 0x4
#define COLLISION_RIGHT 0x8
#define COLLISION_ALL 0xF
#define TILE_PROP_LADDER 0x10
#define COLLISION_GROUP_MASK 0xF
#define COLLISION_GROUP_FLAG_PLATFORM 0x40
#define COLLISION_GROUP_FLAG_SOLID 0x80
#define ACTOR_FLAG_DISABLED 0x40
#define SCRIPT_TERMINATED 0x8000
#define CAMERA_LOCK_X_FLAG 0x01
#define CAMERA_LOCK_Y_FLAG 0x02
#define SCREEN_WIDTH 160
/* GBDK J_* bits, as ngc_joy (scene_input.c) holds them. */
#define J_RIGHT 0x01
#define J_LEFT 0x02
#define J_UP 0x04
#define J_DOWN 0x08
#define J_A 0x10
#define J_B 0x20
#define J_SELECT 0x40
#define J_START 0x80
#define INPUT_LEFT (ngc_joy & J_LEFT)
#define INPUT_RIGHT (ngc_joy & J_RIGHT)
#define INPUT_UP (ngc_joy & J_UP)
#define INPUT_DOWN (ngc_joy & J_DOWN)
#define INPUT_A (ngc_joy & J_A)
#define INPUT_B (ngc_joy & J_B)
#define INPUT_UP_PRESSED (ngc_joy_pressed & J_UP)
#define INPUT_A_PRESSED (ngc_joy_pressed & J_A)
#define INPUT_B_PRESSED (ngc_joy_pressed & J_B)
#define INPUT_PRESSED(btn) (ngc_joy_pressed & (btn))
extern unsigned char ngc_joy;
extern unsigned char ngc_joy_pressed;
/* Scene engine camera (scene_actors.c, camera.c state) and clock. */
#define camera_offset_x cam_offset_x
#define camera_offset_y cam_offset_y
#define camera_deadzone_x cam_deadzone_x
#define camera_deadzone_y cam_deadzone_y
#define camera_settings cam_settings
#define game_time ngc_game_time
#define scroll_x gb_scroll_x
/* collision.c and actor.h helpers of the scene engine. */
#define tile_col_test_range_x col_range_x
#define tile_col_test_range_y col_range_y
#define actor_set_anim(i, a) set_anim((i), (a))
#define actor_set_dir(i, d, m) ngc_actor_set_dir((i), (d), (m))
#define actor_set_anim_idle(i) set_anim((i), act_dir[i])
#define actor_reset_anim(i) set_anim((i), act_anim[i])
void ngc_player_load_animations(unsigned char set);
#define actor_stop_anim(i)do { act_fstart[i] = act_frame[i]; act_fend[i] = (UBYTE)(act_frame[i] + 1u); } while (0)
/* State script storage is emitted selectively in gbvm.c. */
"""


def runtime():
    return json.loads(RUNTIME.read_text(encoding="utf-8"))


def project_runtime(project):
    """The platformer this project links: a translated engine plugin replacing
    states/platform.c (translated.py), else GB Studio's own file."""
    from .translated import runtime as translated_runtime
    return translated_runtime(project, "states/platform.c") or runtime()


def widened_fields(data=None):
    """PLATFORM engine fields declared UBYTE/BYTE by GB Studio, 16-bit here (PRELUDE);
    with `data`, those of that platformer (a translated plugin's: same PRELUDE)."""
    return {f["key"] for f in (data or runtime())["fields"] if f["cType"] in ("UBYTE", "BYTE")}


def memory_names():
    """Platformer variables scripts write (engine memory table, assembler.ENGINE_MEMORY):
    the engine fields, plat_next_state (EVENT_PLATFORMER_STATE_SET) and plat_vel_y
    (playerBounce)."""
    return sorted({f["key"] for f in runtime()["fields"] if f["cType"] != "define"}
                  | {"plat_next_state", "plat_vel_y"})


def states_order(project):
    """compileSprites.ts statesOrder: state names of every sprite sheet, counted,
    most frequent first (stable sort), "" first."""
    counts = {}
    for _, sheet in project.by_type["sprite"]:
        for state in sheet["states"]:
            if state["name"]:
                counts[state["name"]] = counts[state["name"]] + 1 if state["name"] in counts else 1
    return [""] + sorted(counts, key=lambda name: -counts[name])


# platform.c PLATFORM_ANIM_OVERRIDES_SET: (animation field, feature flags it needs).
ANIM_OVERRIDES = (("PLATFORM_FALL_ANIM", ()), ("PLATFORM_FLOAT_ANIM", ("FEAT_PLATFORM_FLOAT",)),
                  ("PLATFORM_JUMP_ANIM", ("FEAT_PLATFORM_JUMP",)), ("PLATFORM_RUN_ANIM", ("FEAT_PLATFORM_RUN",)),
                  ("PLATFORM_LADDERS_ANIM", ("FEAT_PLATFORM_LADDERS",)), ("PLATFORM_DASH_ANIM", ("FEAT_PLATFORM_DASH",)),
                  ("PLATFORM_WALL_SLIDE_ANIM", ("FEAT_PLATFORM_WALL_JUMP", "FEAT_PLATFORM_JUMP")),
                  ("PLATFORM_KNOCKBACK_ANIM", ("FEAT_PLATFORM_KNOCKBACK",)),
                  ("PLATFORM_BLANK_ANIM", ("FEAT_PLATFORM_BLANK",)))


def state_defines(project, data):
    """data/states_defines.h for the PLATFORM fields (compileStateDefines): a
    checked checkbox is `#define KEY`, an animation state its statesOrder
    index (nothing if unset or unknown), any other define `#define KEY value`."""
    from .gbvm.emit import engine_field
    order = states_order(project)
    lines, defined = [], set()
    for field in data["fields"]:
        if field["cType"] != "define":
            continue
        value = engine_field(project, field["key"])
        if field["type"] == "checkbox":
            if value:
                lines.append(f"#define {field['key']}")
                defined.add(field["key"])
        elif field["type"] == "animationstate":
            if value and str(value) in order:
                lines.append(f"#define {field['key']} {order.index(str(value))}")
                defined.add(field["key"])
        else:
            lines.append(f"#define {field['key']} {value}")
            defined.add(field["key"])
    overrides = any(anim in defined and all(f in defined for f in flags) for anim, flags in ANIM_OVERRIDES)
    lines.append(f"#define NGC_PLATFORM_ANIM_OVERRIDES {int(overrides)}   /* PLATFORM_ANIM_OVERRIDES_SET */")
    return lines


def uses_anim_states(project):
    """True when platform.c loads animation sets (PLATFORM_ANIM_OVERRIDES_SET)."""
    return "#define NGC_PLATFORM_ANIM_OVERRIDES 1" in "\n".join(state_defines(project, project_runtime(project)))


def field_values(project, data):
    """Engine field variables: (C type, name, value), set once at boot."""
    from .gbvm.emit import engine_field
    out = []
    for field in data["fields"]:
        if field["cType"] == "define":
            continue
        value = engine_field(project, field["key"])
        if isinstance(value, str):
            if value not in data["consts"]:
                raise CompileError(f"engine field {field['key']}: value {value!r} unknown to GB Studio 4.3.2")
            value = data["consts"][value]
        if type(value) is not int:
            raise CompileError(f"engine field {field['key']}: value {value!r} is not a number")
        out.append((field["cType"], field["key"], value))
    return out


# Wide world (world.py, docs/PLAN_GRANDES_CARTES.md G4): platform.c keeps GB
# Studio's 1/32 px; its positions are the actor arrays (32 bits) and these
# UWORD locals; tiles are UBYTE, already 16-bit here (PRELUDE).
WIDE_POSITIONS = ("new_x", "new_y", "y_bottom", "x_mid_coord", "slope_y_coord",
                  "plat_attached_prev_x", "plat_attached_prev_y", "plat_temp_y")
WIDE_MACROS = (("#define SUBPX_TO_TILE(a) ((UWORD)(unsigned char)((a) >> 8))",
                "#define SUBPX_TO_TILE(a) ((UWORD)((a) >> 8))   /* wide world: 16-bit tiles */"),
               ("#define PX_TO_SUBPX(a) ((a) << 5)",
                "#define PX_TO_SUBPX(a) ((unsigned long)(a) << 5)   /* wide world */"),
               ("#define TILE_TO_SUBPX(a) (((UWORD)(unsigned char)(a)) << 8)",
                "#define TILE_TO_SUBPX(a) ((unsigned long)(UWORD)(a) << 8)   /* wide world */"))


def widen(defined, where):
    """platform.c at the wide position width (world.py)."""
    for name in WIDE_POSITIONS:
        defined, n = re.subn(r"\bUWORD(\s+" + name + r")\b(?=\s*[;=])", r"unsigned long\1", defined)
        if n < 1:
            raise CompileError(f"{where}: platform.c position {name} changed (wide world)")
    for axis in ("x", "y"):
        old = f"ngc_camera_{axis} = (unsigned short)(act_{axis}[0] + 256u);"
        if defined.count(old) != 1:
            raise CompileError(f"{where}: platform.c camera assignment changed (wide world)")
        defined = defined.replace(old, f"ngc_camera_{axis} = (unsigned long)(act_{axis}[0] + 256u);")
    # VEL_TO_SUBPX of a negative velocity is an UNSIGNED 16-bit value (| 0xFF00): added to a 16-bit
    # position it wraps right, added to a 32-bit one (ladder state: act_y[0] += ...) it adds 65536
    # minus the step. Signed here; every other use stores it in a WORD already (same value).
    old = "#define VEL_TO_SUBPX(v) ((((v) & 0x8000) ? (((v) >> 8) | 0xFF00) : ((v) >> 8)) << 1)"
    if defined.count(old) != 1:
        raise CompileError(f"{where}: platform.c VEL_TO_SUBPX changed (wide world)")
    defined = defined.replace(old, "#define VEL_TO_SUBPX(v) ((WORD)((((v) & 0x8000) ? (((v) >> 8) | 0xFF00) "
                                   ": ((v) >> 8)) << 1))   /* wide world: signed */")
    return defined


def validate_platform(project, scene):
    """Fail closed on what the platformer slice does not deliver yet."""
    return None


OVERLAP = ["/* actor.c actor_overlapping_player (from NULL): same list order and UWORD boxes. */",
           "static unsigned char player_overlap(void)", "{",
           "    unsigned char i = act_prev[0];", "    const short *box;",
           "    unsigned short l = (unsigned short)(act_x[0] + (unsigned short)act_bounds[0]);",
           "    unsigned short r = (unsigned short)(act_x[0] + (unsigned short)act_bounds[1]);",
           "    unsigned short t = (unsigned short)(act_y[0] + (unsigned short)act_bounds[2]);",
           "    unsigned short b = (unsigned short)(act_y[0] + (unsigned short)act_bounds[3]);",
           "    while (i != 255u) {",
           "        if (!(act_flags[i] & 0x08u)) { i = act_prev[i]; continue; }",
           "        box = act_bounds + ((unsigned short)i << 2);",
           "        if ((unsigned short)(act_x[i] + (unsigned short)box[0]) <= r &&",
           "            (unsigned short)(act_x[i] + (unsigned short)box[1]) >= l &&",
           "            (unsigned short)(act_y[i] + (unsigned short)box[2]) <= b &&",
           "            (unsigned short)(act_y[i] + (unsigned short)box[3]) >= t) return i;",
           "        i = act_prev[i];", "    }", "    return 255u;", "}"]

# actor.c actor_with_script_in_front_of_player(8): the player box moved 8 px
# (point_translate_dir_word) along its direction, actors with a script only.
FRONT = ["static unsigned char plat_front_script(void)", "{",
         "    unsigned short x = act_x[0];", "    unsigned short y = act_y[0];",
         "    unsigned short l;", "    unsigned short r;", "    unsigned short t;", "    unsigned short b;",
         "    unsigned char i;", "    const short *box;",
         "    if (act_dir[0] == 0u) y = (unsigned short)(y + 256u);",
         "    else if (act_dir[0] == 1u) x = (unsigned short)(x + 256u);",
         "    else if (act_dir[0] == 2u) y = (unsigned short)(y - 256u);",
         "    else if (act_dir[0] == 3u) x = (unsigned short)(x - 256u);",
         "    l = (unsigned short)(x + (unsigned short)act_bounds[0]);",
         "    r = (unsigned short)(x + (unsigned short)act_bounds[1]);",
         "    t = (unsigned short)(y + (unsigned short)act_bounds[2]);",
         "    b = (unsigned short)(y + (unsigned short)act_bounds[3]);",
         "    i = act_prev[0];", "    while (i != 255u) {",
         "        if (!act_interact_addr[i]) { i = act_prev[i]; continue; }",
         "        box = act_bounds + ((unsigned short)i << 2);",
         "        if ((unsigned short)(act_x[i] + (unsigned short)box[0]) > r ||",
         "            (unsigned short)(act_x[i] + (unsigned short)box[1]) < l ||",
         "            (unsigned short)(act_y[i] + (unsigned short)box[2]) > b ||",
         "            (unsigned short)(act_y[i] + (unsigned short)box[3]) < t) { i = act_prev[i]; continue; }",
         "        return i;", "    }", "    return 255u;", "}"]


def platform_source(project, scene, width_tiles, height_tiles, interactions=None, triggers=None, hits=None, callbacks=False):
    """C appended to scene_actors.c: prelude, platform.c, scene engine entry points.
    interactions {actor index: script address}, triggers [(trigger, address)], hits
    (scene_collisions.collision_source config): the scene's scripts, as topdown."""
    from .scene_collisions import collision_groups, collision_source
    from .scene_triggers import trigger_source
    data = project_runtime(project)
    translated = "prelude" in data           # a translated engine plugin's platform.c
    if translated and callbacks:
        raise CompileError(f"scene {scene['name']}: platformer state scripts are GB Studio's platform.c; "
                           f"the project's {data['plugin']} plugin replaces that file")
    values = field_values(project, data)
    defined = data["code"]
    # Callback storage lives in GBVM, shared by scene engines; absent without usage.
    defined = re.sub(r"script_event_t plat_events\[CALLBACK_SIZE\];", "", defined)
    defined = re.sub(r"#ifdef NGC_PLATFORM_STATE_SCRIPTS\n.*?\n#endif", "", defined, flags=re.S)
    for fn in ("plat_callback_reset", "plat_callback_execute"):
        defined = re.sub(r"static void " + fn + r"\([^\n]*\)\s*\{.*?^\}", "", defined, flags=re.M|re.S)
        defined = re.sub(r"^(?:static )?void " + fn + r"\([^\n]*\);$", "", defined, flags=re.M)
    if callbacks:
        defined = defined.replace("plat_callback_reset()", "ngc_vm_platform_reset()")
        defined = defined.replace("plat_callback_execute(", "ngc_vm_platform_event(")
        defined = "void ngc_vm_platform_reset(void);\nvoid ngc_vm_platform_event(unsigned char i);\n" + defined
    else:
        defined = defined.replace("plat_callback_reset();", "")
        defined = re.sub(r"plat_callback_execute\([^)]*\)", "((void)0)", defined)

    prelude = PRELUDE
    if wide():
        if translated:
            raise CompileError(f"scene {scene['name']}: the {data['plugin']} plugin over 255 tiles is not "
                               "delivered yet (docs/PLAN_GRANDES_CARTES.md G6)")
        defined = widen(defined, f"scene {scene['name']}")
        for old, new in WIDE_MACROS:
            if prelude.count(old) != 1:
                raise CompileError("platform prelude macro changed (wide world)")
            prelude = prelude.replace(old, new)
    lines = prelude.split("\n") + state_defines(project, data)
    lines += [f"#define NGC_COLL_W {width_tiles}u", f"#define NGC_COLL_H {height_tiles}u"]
    if translated:
        lines += data["prelude"].split("\n")
    if hits:                  # collision_source: act_collision_group, player_overlap, callbacks
        lines += collision_source(hits)
        lines.append("#define NGC_PLAYER_COLLISION(i) (collision_pending = (unsigned char)(i))")
    else:
        lines.append("/* actor_t.collision_group (toASMCollisionGroup): base group | extra flags. */")
        c_array(lines, "act_collision_group", collision_groups(project, scene), "unsigned char")
        lines += boxes(OVERLAP) + ["#define NGC_PLAYER_COLLISION(i) ((void)(i))"]
    if triggers:
        lines += trigger_source(triggers, force=True)
        lines.append("#define NGC_PLATFORM_TRIGGERS(force) ((void)trigger_update((unsigned char)((force) != 0)))")
    else:
        lines.append("#define NGC_PLATFORM_TRIGGERS(force) ((void)0)")
    if interactions:
        n = len(collision_groups(project, scene))
        c_array(lines, "act_interact_addr", [interactions[i] if i in interactions else 0 for i in range(n)],
                "unsigned short")
        lines += ["void ngc_vm_interact(unsigned short addr);"] + boxes(FRONT) + [
                  "#define NGC_ACTOR_IN_FRONT(grid) plat_front_script()",
                  "#define NGC_ACTOR_HAS_SCRIPT(i) (act_interact_addr[i] != 0u)",
                  "#define NGC_ACTOR_INTERACT(i) ngc_vm_interact(act_interact_addr[i])"]
    else:
        lines += ["#define NGC_ACTOR_IN_FRONT(grid) 255u", "#define NGC_ACTOR_HAS_SCRIPT(i) 0",
                  "#define NGC_ACTOR_INTERACT(i) ((void)(i))"]
    lines += ["#define NGC_STATE_SCRIPT_EXECUTE(addr, handle) ((void)(handle))   /* never attached yet */",
              "/* collision.h tile_at: COLLISION_ALL outside the scene. */",
              "static UBYTE tile_at(UBYTE tx, UBYTE ty)", "{",
              "    if (tx >= NGC_COLL_W || ty >= NGC_COLL_H) return COLLISION_ALL;",
              "    return coll_map[(unsigned short)ty * NGC_COLL_W + tx];", "}"]
    init = [*(["    trigger_last = 255u;"] if triggers else []),
            *(["    collision_iframes = 0u; collision_pending = 255u;"] if hits else [])]
    # Engine fields platform.c does not declare itself are declared here.
    for ctype, key, _ in values:
        if f" {key};" not in defined:
            lines.append(f"{ctype} {key};")
    lines += defined.split("\n")
    # The Game Boy C runtime clears BSS at power on and platform.c relies on it
    # (jump buffer, timers, attached actor...): the NGPC RAM is random (startup.c
    # clears nothing), so every file-scope variable is cleared once at boot.
    # Bytes, not UBYTE: the PRELUDE's UBYTE is 16-bit, which cleared twice the
    # size and spilled past the last variable (fixed 2026-10-12).
    lines += ["static void plat_clear(unsigned char *p, UWORD n) { while (n--) *p++ = 0u; }",
              "/* crt0 BSS clear, then script_engine_init: engine field values, once at boot. */",
              "void ngc_platform_boot(void)", "{",
              *[f"    plat_clear((unsigned char *)&{name}, sizeof({name}));"
                for name in (data["boot_clear"] if "boot_clear" in data else data["public_variables"]) if name != "plat_events"],
              *[f"    {key} = ({ctype}){value};" for ctype, key, value in values],
              *[f"    {key} = {value};               /* platform.c static initializer */"
                for key, value in data["boot_values"].items()], "}",
              "/* state_init at scene load; trigger and collision state of the scene (as topdown). */",
              "void ngc_platform_init(void)", "{", *init,
              *(["    pp_sync_in();", "    platform_init();", "    pp_sync_out();"] if translated
                else ["    platform_init();"]), "}",
              ("void ngc_platform_update(void) { pp_sync_in(); platform_update(); pp_sync_out(); }" if translated
               else "void ngc_platform_update(void) { platform_update(); }"), ""]
    return lines
