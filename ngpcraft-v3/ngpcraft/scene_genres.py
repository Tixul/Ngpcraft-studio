"""GBVM states/shmup.c, pointnclick.c and adventure.c on the scene engine (M5).

The state files are GB Studio's own, vendored by tools/extract_genres.py
(ngpcraft/runtime/{shmup,pointnclick}_runtime.json): C89 hoisting and listed
adaptations only. This module writes what GBVM's headers and generated data
give them, as scene_platform.py does for platform.c:
- the defines of data/states_defines.h (compileStateDefines);
- the engine field variables, set once at boot (script_engine_init);
- the GBVM names the files use (sincos.h, math.h, camera.h, collision.h,
  game_time.h, actor.h), bound to scene_actors.c;
- the hooks (triggers, player hit scripts, clicks), from what the scene uses.
Study, branch points and proof bench: docs/ETUDE_M5_SHMUP_PNC.md.
"""
import json
from pathlib import Path

from .background_rom import c_array
from .compiler import CompileError

RUNTIME = {state: Path(__file__).with_name("runtime") / f"{state}_runtime.json"
           for state in ("shmup", "pointnclick", "adventure")}
STATES = {"SHMUP": "shmup", "POINTNCLICK": "pointnclick", "ADVENTURE": "adventure"}

# GBVM headers at bd6f41c, beyond scene_platform.PRELUDE (values copied).
PRELUDE = r"""/* GBVM states/shmup.c, pointnclick.c on the scene engine (ngpcraft/scene_genres.py).
 * sincos.h (sine_wave, upoint_translate_angle), math.h, camera.h, collision.h,
 * game_time.h, actor.h, input.h at bd6f41c. */
#define SIN(a) (state_sine[(unsigned char)(a)])
#define COS(a) (state_sine[(unsigned char)((a) + 64u)])
/* upoint_translate_angle: x += (int16_t)(SIN(a) * speed) >> 7, y -= ... COS.
 * SDCC computes the product as a signed 16-bit int; UBYTE is 16-bit unsigned
 * here, so both factors are widened to signed short explicitly. */
#define NGC_TRANSLATE_ANGLE(i, a, speed) do { \
    act_x[i] += (UWORD)((short)((short)SIN(a) * (short)(speed)) >> 7); \
    act_y[i] -= (UWORD)((short)((short)COS(a) * (short)(speed)) >> 7); } while (0)
#define ANGLE_0DEG 0
#define ANGLE_45DEG 32
#define ANGLE_90DEG 64
#define ANGLE_135DEG 96
#define ANGLE_180DEG 128
#define ANGLE_225DEG 160
#define ANGLE_270DEG 192
#define ANGLE_315DEG 224
#define camera_x ngc_camera_x
#define camera_y ngc_camera_y
#define scroll_y gb_scroll_y
#define INPUT_A_PRESSED (ngc_joy_pressed & J_A)
#define SCREEN_WIDTH_HALF 80
#define SUBPX_SNAP_PX(a) ((a) & 0xFFE0)
#define SUBPX_PX_REMAINDER(a) ((UBYTE)((a) & 0x1F))
#define COLLISION_GROUP_NONE 0x0
#define COLLISION_GROUP_1 0x2
#define COLLISION_GROUP_2 0x4
#define COLLISION_GROUP_3 0x8
#define PLAYER_HURT_IFRAMES 20
#define player_iframes collision_iframes
#define IS_FRAME_ODD ((ngc_game_time & 0x1) == 1)
#define ANIM_CURSOR 0
#define ANIM_CURSOR_HOVER 1
#define MAX_TRIGGERS 31
#define NO_TRIGGER_COLLISON 0xFF
#define image_tile_width NGC_COLL_W
#define image_tile_height NGC_COLL_H
#define image_width_subpx ((UWORD)(NGC_COLL_W << 8))
#define image_height_subpx ((UWORD)(NGC_COLL_H << 8))
extern unsigned short ngc_camera_x;
extern unsigned short ngc_camera_y;
/* actor.h player_moving, as scene_topdown.py declares it (one variable per scene link). */
static unsigned char player_moving;
"""


# GBVM headers used by adventure.c beyond the two preludes (bd6f41c, values copied).
ADVENTURE_PRELUDE = r"""/* adventure.c: input.h joy / recent_joy / INPUT_RECENT_*, INPUT_DPAD;
 * math.h MOD_4, FLIPPED_DIR; gbs_types.h point16_t, direction_e. */
#define direction_e UBYTE
/* One member per line, 16-bit scalars: the form scene_link.py shares between scenes. */
typedef struct {
    short x;
    short y;
} point16_t;
extern unsigned char ngc_recent_joy;      /* scene_topdown.py input.c recent_joy */
#define joy ngc_joy
#define recent_joy ngc_recent_joy
#define INPUT_DPAD (J_UP | J_DOWN | J_LEFT | J_RIGHT)
#define INPUT_RECENT_LEFT ((recent_joy & J_LEFT) || (!recent_joy && (joy & J_LEFT)))
#define INPUT_RECENT_RIGHT ((recent_joy & J_RIGHT) || (!recent_joy && (joy & J_RIGHT)))
#define INPUT_RECENT_UP ((recent_joy & J_UP) || (!recent_joy && (joy & J_UP)))
#define INPUT_RECENT_DOWN ((recent_joy & J_DOWN) || (!recent_joy && (joy & J_DOWN)))
#define MOD_4(a) ((a)&3)
#define FLIPPED_DIR(dir) MOD_4((dir) + 2)
"""
# adventure.c ADVENTURE_ANIM_OVERRIDES_SET: (animation field, feature flag it needs).
ADVENTURE_ANIM_OVERRIDES = (("ADVENTURE_RUN_ANIM", "FEAT_ADVENTURE_RUN"), ("ADVENTURE_DASH_ANIM", "FEAT_ADVENTURE_DASH"),
                            ("ADVENTURE_KNOCKBACK_ANIM", "FEAT_ADVENTURE_KNOCKBACK"),
                            ("ADVENTURE_BLANK_ANIM", "FEAT_ADVENTURE_BLANK"), ("ADVENTURE_PUSH_ANIM", "FEAT_ADVENTURE_PUSH"))
# adventure.c file-scope statics of GBVM types the extractor's name list does not
# read (direction_e, point16_t): cleared at boot with the others.
ADVENTURE_STATICS = ("facing_dir", "delta", "movement_delta", "collision_dir")
# adventure.c static initializers removed by the extractor, restored at boot.
ADVENTURE_BOOT_VALUES = {"facing_dir": "DIR_DOWN", "adv_attached_actor": "255u"}

# actor.c actor_overlapping_player_from(start): the next overlapping actor after
# start in the same list (scene_platform.OVERLAP from NULL), same UWORD boxes.
OVERLAP_FROM = ["static unsigned char player_overlap_from(unsigned char start)", "{",
                "    unsigned char i = act_prev[start];", "    const short *box;",
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


def state_name(scene):
    if scene["type"] not in STATES:
        raise CompileError(f"scene {scene['name']}: {scene['type']} has no vendored genre state")
    return STATES[scene["type"]]


def runtime(state):
    return json.loads(RUNTIME[state].read_text(encoding="utf-8"))


def _sine():
    data = json.loads((Path(__file__).with_name("runtime") / "projectile_motion.json").read_text(encoding="utf-8"))
    if len(data["sine_wave"]) != 256:
        raise CompileError("projectile_motion.json: sine_wave must hold 256 values")
    return "{" + ", ".join(str(v) for v in data["sine_wave"]) + "}"


def state_defines(project, data):
    """data/states_defines.h (compileStateDefines): `#define KEY value` for each
    define field of the scene type (select fields: the option name); a checked
    checkbox is `#define KEY`, an animation state its statesOrder index
    (nothing if unset or unknown), as scene_platform.state_defines."""
    from .gbvm.emit import engine_field
    from .scene_platform import states_order
    lines, defined = [], set()
    for field in data["fields"]:
        if field["cType"] != "define":
            continue
        value = engine_field(project, field["key"])
        if field["type"] == "checkbox":
            if value:
                lines.append(f"#define {field['key']}")
                defined.add(field["key"])
            continue
        if field["type"] == "animationstate":
            order = states_order(project)
            if value and str(value) in order:
                lines.append(f"#define {field['key']} {order.index(str(value))}")
                defined.add(field["key"])
            continue
        options = [o[0] for o in field.get("options", [])]
        if options and value not in options:
            raise CompileError(f"engine field {field['key']}: value {value!r} unknown to GB Studio 4.3.2")
        lines.append(f"#define {field['key']} {value}")
        defined.add(field["key"])
    if any(f["key"] == "FEAT_ADVENTURE_RUN" for f in data["fields"]):
        overrides = any(anim in defined and flag in defined for anim, flag in ADVENTURE_ANIM_OVERRIDES)
        lines.append(f"#define NGC_ADVENTURE_ANIM_OVERRIDES {int(overrides)}   /* ADVENTURE_ANIM_OVERRIDES_SET */")
    return lines


def uses_anim_states(project, scene):
    """True when adventure.c loads animation sets (ADVENTURE_ANIM_OVERRIDES_SET)."""
    return (scene["type"] == "ADVENTURE" and
            "#define NGC_ADVENTURE_ANIM_OVERRIDES 1" in "\n".join(state_defines(project, runtime("adventure"))))


def field_values(project, data):
    """Engine field variables: (C type, name, value), set once at boot."""
    from .gbvm.emit import engine_field
    out = []
    for field in data["fields"]:
        if field["cType"] == "define":
            continue
        value = engine_field(project, field["key"])
        if isinstance(value, str):          # a select field: engine.json consts (ADVENTURE_*_STATE)
            consts = data["consts"]
            if value not in consts:
                raise CompileError(f"engine field {field['key']}: value {value!r} unknown to GB Studio 4.3.2")
            value = consts[value]
        if type(value) is not int:
            raise CompileError(f"engine field {field['key']}: value {value!r} is not a number")
        if field["cType"] == "UBYTE" and not 0 <= value <= 255:
            raise CompileError(f"engine field {field['key']}: {value!r} is outside the GBVM byte range 0..255")
        out.append((field["cType"], field["key"], value))
    return out


def memory_names():
    """Genre variables scripts write (EVENT_ENGINE_FIELD_SET): the non-define fields."""
    return sorted({f["key"] for state in RUNTIME for f in runtime(state)["fields"] if f["cType"] != "define"})


def widened_fields():
    """Engine fields declared UBYTE/BYTE by GB Studio, 16-bit here (scene_platform.PRELUDE)."""
    return {f["key"] for state in RUNTIME for f in runtime(state)["fields"] if f["cType"] in ("UBYTE", "BYTE")}


def needs_player_collisions(project, scene):
    """shmup.c reads player_iframes, which actors_handle_player_collision sets on
    any contact with an actor of collision group 1..3, scripts or not: the
    collision bookkeeping is then needed even without any collision script."""
    if scene["type"] != "SHMUP":
        return False
    from .scene_collisions import collision_groups
    return any(g & 0x0F for g in collision_groups(project, scene)[1:])


def _shmup_hooks(defines, triggers, hits):
    screen = "#define SHOOTER_TRIGGER_ACTIVATION ON_SCREEN_ENTER" in defines
    lines = []
    if triggers:
        from .scene_triggers import SCREEN_BOX, trigger_source
        if screen:
            lines += trigger_source(triggers, box=SCREEN_BOX, name="trigger_screen_update")
            lines += ["#define NGC_SHMUP_SCREEN_TRIGGERS() trigger_screen_update()", "#define NGC_SHMUP_TRIGGERS() 0"]
        else:
            lines += trigger_source(triggers)
            lines += ["#define NGC_SHMUP_TRIGGERS() trigger_update()", "#define NGC_SHMUP_SCREEN_TRIGGERS() 0"]
    else:
        lines += ["#define NGC_SHMUP_TRIGGERS() 0", "#define NGC_SHMUP_SCREEN_TRIGGERS() 0"]
    if hits and hits["player"] is not None:
        lines += ["#define NGC_PLAYER_HAS_HIT_SCRIPT 1",
                  f"#define NGC_PLAYER_HIT(g) ngc_vm_hit({hits['player']}u, (unsigned short)(g))"]
    else:
        lines += ["#define NGC_PLAYER_HAS_HIT_SCRIPT 0", "#define NGC_PLAYER_HIT(g) ((void)(g))"]
    return lines


def _click_hooks(project, scene, triggers, interactions, hits):
    from .scene_collisions import collision_groups
    from .scene_triggers import trigger_lookup_source
    lines = []
    if triggers:
        lines += trigger_lookup_source(triggers)
        lines += ["#define NGC_TRIGGER_AT_PLAYER() trigger_at_player()",
                  "#define NGC_TRIGGER_HAS_SCRIPT(t) 1",
                  "#define NGC_TRIGGER_INTERACT(t) trigger_interact(t)"]
    else:
        lines += ["#define NGC_TRIGGER_AT_PLAYER() 255u", "#define NGC_TRIGGER_HAS_SCRIPT(t) 0",
                  "#define NGC_TRIGGER_INTERACT(t) ((void)(t))"]
    n = len(collision_groups(project, scene))
    interactions = interactions or {}
    hit_actors = (hits or {}).get("actors") or {}
    if set(interactions) & set(hit_actors):
        raise CompileError(f"scene {scene['name']}: an actor has both an interaction and a combined collision script")
    if interactions or hit_actors:
        # actor_t.script: the interaction script, or for an actor with a collision
        # group the combined script (compileData.ts), whose branch 0 is its script.
        # 0: no script (an actor with neither).
        c_array(lines, "act_click_addr", [interactions[i] if i in interactions else hit_actors[i] if i in hit_actors
                                          else 0 for i in range(n)],
                "unsigned short")
        if interactions and hit_actors:
            c_array(lines, "act_click_combined", [1 if i in hit_actors else 0 for i in range(n)], "unsigned char")
            body = ["    if (act_click_combined[i]) ngc_vm_hit(act_click_addr[i], 0u);",
                    "    else ngc_vm_interact(act_click_addr[i]);"]
        elif interactions:
            body = ["    ngc_vm_interact(act_click_addr[i]);"]
        else:
            body = ["    ngc_vm_hit(act_click_addr[i], 0u);"]
        lines += [*(["void ngc_vm_interact(unsigned short addr);"] if interactions else []),
                  "/* pointnclick.c: script_execute(actor->script, ..., 1, 0). */",
                  "static void click_actor(unsigned char i)", "{", *body, "}"]
        lines += ["#define NGC_ACTOR_HAS_SCRIPT(i) (act_click_addr[i] != 0u)",
                  "#define NGC_ACTOR_INTERACT(i) click_actor(i)"]
    else:
        lines += ["#define NGC_ACTOR_HAS_SCRIPT(i) 0", "#define NGC_ACTOR_INTERACT(i) ((void)(i))"]
    return lines


def data_code_adventure(data, callbacks=False):
    """adventure.c without its state-script storage, which lives in gbvm.c when a
    script attaches one (gbvm/platform_callbacks.py, ngc_vm_adventure_*); without
    any, the callbacks run nothing, as an unattached slot does in GBVM."""
    import re
    code = data["code"]
    code = re.sub(r"script_event_t adv_events\[CALLBACK_SIZE\];", "", code)
    code = re.sub(r"#ifdef NGC_ADVENTURE_STATE_SCRIPTS\n.*?\n#endif", "", code, flags=re.S)
    for fn in ("adv_callback_reset", "adv_callback_execute"):
        code = re.sub(r"static void " + fn + r"\([^\n]*\)\s*\{.*?^\}", "", code, flags=re.M | re.S)
        code = re.sub(r"^(?:static )?void " + fn + r"\([^\n]*\);$", "", code, flags=re.M)
    if callbacks:
        code = code.replace("adv_callback_reset()", "ngc_vm_adventure_reset()")
        code = code.replace("adv_callback_execute(", "ngc_vm_adventure_event(")
        code = "void ngc_vm_adventure_reset(void);\nvoid ngc_vm_adventure_event(unsigned char i);\n" + code
    else:
        code = code.replace("adv_callback_reset();", "")
        code = re.sub(r"adv_callback_execute\([^)]*\)", "((void)0)", code)
    return code.split("\n")


def _adventure_hooks(project, scene, triggers, interactions):
    from .scene_collisions import collision_groups
    from .scene_platform import FRONT
    from .scene_triggers import trigger_source
    lines = ADVENTURE_PRELUDE.split("\n") + OVERLAP_FROM
    if triggers:
        lines += trigger_source(triggers)
        lines.append("#define NGC_ADVENTURE_TRIGGERS() ((void)trigger_update())")
    else:
        lines.append("#define NGC_ADVENTURE_TRIGGERS() ((void)0)")
    if interactions:
        n = len(collision_groups(project, scene))
        c_array(lines, "act_interact_addr", [interactions[i] if i in interactions else 0 for i in range(n)],
                "unsigned short")
        lines += ["void ngc_vm_interact(unsigned short addr);"] + FRONT + [
                  "#define NGC_ACTOR_IN_FRONT(grid) plat_front_script()",
                  "#define NGC_ACTOR_HAS_SCRIPT(i) (act_interact_addr[i] != 0u)",
                  "#define NGC_ACTOR_INTERACT(i) ngc_vm_interact(act_interact_addr[i])"]
    else:
        lines += ["#define NGC_ACTOR_IN_FRONT(grid) 255u", "#define NGC_ACTOR_HAS_SCRIPT(i) 0",
                  "#define NGC_ACTOR_INTERACT(i) ((void)(i))"]
    return lines


def genre_source(project, scene, width_tiles, height_tiles, interactions=None, triggers=None, hits=None, view_h=152,
                 callbacks=False):
    """C appended to scene_actors.c: prelude, the vendored state, its entry points
    ngc_<state>_boot (shmup: engine fields), ngc_<state>_init, ngc_<state>_update."""
    from .scene_collisions import collision_groups, collision_source
    from .scene_platform import OVERLAP, PRELUDE as PLATFORM_PRELUDE
    state = state_name(scene)
    data = runtime(state)
    if data["unadapted"]:
        raise CompileError(f"{state} runtime has unadapted GBVM names {data['unadapted']}")
    defines = state_defines(project, data)
    lines = PLATFORM_PRELUDE.split("\n")
    c_array(lines, "state_sine", [int(v) for v in _sine().strip("{}").split(",")], "signed char")
    lines += PRELUDE.split("\n") + defines
    lines += [f"#define SCREEN_HEIGHT {view_h}", f"#define SCREEN_HEIGHT_HALF {view_h >> 1}",
              "/* ECARTS 1: GB Studio's vertical offsets are set for a 144 px view. */",
              f"#define NGC_SHMUP_VIEW_DELTA {(view_h >> 1) - 72}",
              f"#define NGC_COLL_W {width_tiles}u", f"#define NGC_COLL_H {height_tiles}u"]
    if hits:                  # collision_source: act_collision_group, player_overlap, iframes
        lines += collision_source(hits)
        lines.append("#define NGC_PLAYER_COLLISION(i) (collision_pending = (unsigned char)(i))")
    else:
        lines.append("/* actor_t.collision_group (toASMCollisionGroup): base group | extra flags. */")
        c_array(lines, "act_collision_group", collision_groups(project, scene), "unsigned char")
        lines += OVERLAP + ["#define NGC_PLAYER_COLLISION(i) ((void)(i))",
                            "/* No actor of the scene has a collision group: player_iframes stays 0. */",
                            "static unsigned char collision_iframes;"]
    if state == "shmup":
        lines += _shmup_hooks(defines, triggers, hits)
    elif state == "adventure":
        lines += _adventure_hooks(project, scene, triggers, interactions)
    else:
        lines += _click_hooks(project, scene, triggers, interactions, hits)
    if state == "adventure":
        lines += data_code_adventure(data, callbacks)
    else:
        lines += data["code"].split("\n")
    values = field_values(project, data)
    shmup_triggers = state == "shmup" and triggers
    if state == "adventure":
        return lines + [
            "static void genre_clear(unsigned char *p, UWORD n) { while (n--) *p++ = 0u; }",
            "/* crt0 BSS clear, then script_engine_init: engine field values, once at boot. */",
            "void ngc_adventure_boot(void)", "{",
            *[f"    genre_clear((unsigned char *)&{name}, sizeof({name}));"
              for name in [*data["file_variables"], *ADVENTURE_STATICS] if name != "adv_events"],
            *[f"    {key} = ({ctype}){value};" for ctype, key, value in values],
            *[f"    {key} = {value};               /* adventure.c static initializer */"
              for key, value in ADVENTURE_BOOT_VALUES.items()], "}",
            "/* state_init at scene load; trigger and collision state of the scene. */",
            "void ngc_adventure_init(void)", "{",
            *(["    trigger_last = 255u;"] if triggers else []),
            *(["    collision_iframes = 0u; collision_pending = 255u;"] if hits else ["    collision_iframes = 0u;"]),
            "    adventure_init();", "}",
            "void ngc_adventure_update(void) { adventure_update(); }", ""]
    lines += ["static void genre_clear(unsigned char *p, UWORD n) { while (n--) *p++ = 0u; }" if state == "shmup" else "",
              *(["/* crt0 BSS clear, then script_engine_init: engine field values, once at boot. */",
                 "void ngc_shmup_boot(void)", "{",
                 *[f"    genre_clear((unsigned char *)&{name}, sizeof({name}));" for name in data["public_variables"]],
                 *[f"    {key} = ({ctype}){value};" for ctype, key, value in values], "}"]
                if state == "shmup" else []),
              "/* state_init at scene load; trigger and collision state of the scene. */",
              f"void ngc_{state}_init(void)", "{",
              *(["    trigger_last = 255u;"] if shmup_triggers else []),
              *(["    collision_iframes = 0u; collision_pending = 255u;"] if hits else ["    collision_iframes = 0u;"]),
              f"    {state}_init();", "}",
              f"void ngc_{state}_update(void) {{ {state}_update(); }}", ""]
    return lines
