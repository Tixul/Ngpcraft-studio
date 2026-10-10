"""Specialized topdown.c state and GBVM input semantics on NGPC.

Hardware read: NgpCraft_base_template/src/core/ngpc_input.c (MIT), Sys Lever
0x6F82 once per game tick. Button order mapped to GBDK's J_* bits. No repeat
timer: GBVM only uses held, pressed and most-recent direction here.
"""
from .background_rom import c_array
from .compiler import CompileError
from .project import load_model
from .scene_init import _Builder, _commented
from .scene_updates import ActorUpdate


def has_events(events):
    return any(not _commented(e) for e in events)


def build_interactions(project):
    b = _Builder(project, load_model())
    scripts = []
    for scene in project.scenes:
        # Scene types whose state code runs actor_t.script on an interaction
        # (topdown.c, platform.c, adventure.c in front of the player; pointnclick.c on a click).
        if scene["type"] not in ("TOPDOWN", "PLATFORM", "POINTNCLICK", "ADVENTURE") or scene["type"] in project.settings["disabledSceneTypeIds"]:
            continue
        for index, raw in enumerate(scene["actors"]):
            actor, path, note = b.actor(raw)
            if actor["collisionGroup"] or not has_events(actor["script"]):
                continue
            where = f"{path}:script"
            b.check_custom_events(actor["script"], where, set())
            scripts.append(ActorUpdate(scene["id"], actor, index, where + note, actor["script"],
                           [f"{where}[{i}]{note}" for i in range(len(actor["script"]))]))
    if b.errors:
        raise CompileError("actor interaction scripts:\n  " + "\n  ".join(b.errors))
    return scripts


def validate_topdown(project, scene, grid, interact):
    """Fail closed on executable hooks the topdown slice does not deliver yet."""
    b = _Builder(project, load_model())
    where = b.path_of[scene["id"]]
    if type(grid) is not int or grid not in (8, 16):
        raise CompileError(f"{where}: topdown_grid {grid!r}: expected 8 or 16 pixels")
    buttons = {"INPUT_A": 0x10, "INPUT_B": 0x20, "INPUT_A_OR_B": 0x30,
               "INPUT_LEFT": 2, "INPUT_RIGHT": 1, "INPUT_UP": 4, "INPUT_DOWN": 8}
    if not isinstance(interact, str) or interact not in buttons:
        raise CompileError(f"{where}: INPUT_TOPDOWN_INTERACT {interact!r} is not delivered "
                           "(GB Studio 4.3.2 offers INPUT_A or INPUT_B; Start/Select come from Option, ECARTS 2)")
    # Actor hit1..3 branches run on projectile hits (M4_PROJECTILES.md):
    # compiled into the actor's collision script like compileData.ts.
    return buttons[interact]


# ECARTS §2 (A5, 2026-10-01): Option is both Start and Select. A press
# shorter than OPTION_LONG_FRAMES gives one game tick of the short role, on
# release; held OPTION_LONG_FRAMES ticks, it gives the long role until release.
OPTION_LONG_FRAMES = 60                       # about one second
J_START, J_SELECT = 0x80, 0x40                # GBDK gb.h


def option_roles(long_press):
    """ngpcraft.json option_long_press -> (short bit, long bit)."""
    return (J_START, J_SELECT) if long_press == "select" else (J_SELECT, J_START)


def input_source(long_press="select"):
    short, long_ = option_roles(long_press)
    return f'''/* NGPC hardware read from NgpCraft_base_template ngpc_input.c (MIT).
 * GBVM input.c: snapshot after process_VM, even while the VM is locked.
 * Sys Lever bits (SDK SysWork.txt): U D L R A B Option, then D of a Neo Geo
 * controller (not mapped). GBDK: R L U D A B Select Start.
 * Option (ECARTS 2): short press = 0x{short:02X} for one tick on release,
 * press held {OPTION_LONG_FRAMES} ticks = 0x{long_:02X} until release. */
unsigned char ngc_joy;
unsigned char ngc_joy_pressed;
unsigned char ngc_recent_joy;
unsigned char ngc_last_joy;                 /* input.c last_joy, read by events_update */
static unsigned char ngc_option_ticks;

void ngc_input_init(void)
{{
    ngc_joy = 0u; ngc_joy_pressed = 0u; ngc_recent_joy = 0u; ngc_last_joy = 0u;
    ngc_option_ticks = 0u;
}}

void ngc_input_update(void)
{{
    unsigned char raw = *(volatile unsigned char *)0x6F82;
    unsigned char option = 0u;
    if (raw & 0x40u) {{
        if (ngc_option_ticks < {OPTION_LONG_FRAMES}u) ++ngc_option_ticks;
        if (ngc_option_ticks == {OPTION_LONG_FRAMES}u) option = 0x{long_:02X}u;
    }} else {{
        if (ngc_option_ticks != 0u && ngc_option_ticks < {OPTION_LONG_FRAMES}u) option = 0x{short:02X}u;
        ngc_option_ticks = 0u;
    }}
    ngc_last_joy = ngc_joy;
    ngc_joy = (unsigned char)((raw & 0x30u) | ((raw & 0x03u) << 2)
                             | ((raw & 0x04u) >> 1) | ((raw & 0x08u) >> 3) | option);
    if ((ngc_joy ^ ngc_last_joy) & 0x0Fu)
        ngc_recent_joy = (unsigned char)((ngc_joy & (unsigned char)~ngc_last_joy) & 0x0Fu);
    ngc_joy_pressed = (unsigned char)(ngc_joy & (unsigned char)~ngc_last_joy);
}}
'''


def topdown_source(n, grid, interact_mask, interactions, triggers=None, hits=None):
    lines = ["/* GBVM states/topdown.c; actor arrays, collision helpers above. */",
             "extern unsigned char ngc_joy;", "extern unsigned char ngc_joy_pressed;",
             "extern unsigned char ngc_recent_joy;",
             "static unsigned char player_moving;", "static unsigned short player_max_pos;"]
    if hits:
        from .scene_collisions import collision_source
        lines += collision_source(hits)
    if triggers:
        from .scene_triggers import trigger_source
        lines += trigger_source(triggers)
    if interactions:
        lookup = {**interactions, **hits['actors']} if hits else interactions
        c_array(lines, "act_interact_addr", [lookup[i] if i in lookup else 0 for i in range(n)],
                "unsigned short")
        lines.append("void ngc_vm_interact(unsigned short addr);")
    lines += ["void ngc_topdown_init(void)", "{", "    player_moving = 0u; player_max_pos = 0u;",
              *(["    trigger_last = 255u;"] if triggers else []),
              *(["    collision_iframes = 0u; collision_pending = 255u;"] if hits else []),
              "    act_x[0] &= " + ("0xFE00u;" if grid == 16 else "0xFF00u;"),
              ("    act_y[0] = (unsigned short)((act_y[0] & 0xFE00u) + 256u);" if grid == 16
               else "    act_y[0] &= 0xFF00u;"), "}", ""]
    # actor_in_front_of_player / actor_with_script_in_front_of_player: same
    # list order and UWORD boxes. Script lookup includes actors without collision.
    lines += ["static unsigned char player_front(unsigned char scripted)", "{",
              "    unsigned short x = act_x[0];", "    unsigned short y = act_y[0];",
              "    unsigned short l;", "    unsigned short r;", "    unsigned short t;", "    unsigned short b;",
              "    unsigned char i;", "    const short *box;",
              f"    if (act_dir[0] == 0u) y = (unsigned short)(y + {grid * 32}u);",
              f"    else if (act_dir[0] == 1u) x = (unsigned short)(x + {grid * 32}u);",
              f"    else if (act_dir[0] == 2u) y = (unsigned short)(y - {grid * 32}u);",
              f"    else x = (unsigned short)(x - {grid * 32}u);",
              "    l = (unsigned short)(x + (unsigned short)act_bounds[0]);",
              "    r = (unsigned short)(x + (unsigned short)act_bounds[1]);",
              "    t = (unsigned short)(y + (unsigned short)act_bounds[2]);",
              "    b = (unsigned short)(y + (unsigned short)act_bounds[3]);",
              "    i = act_prev[0];", "    while (i != 255u) {",
              *( ["        if (scripted ? !act_interact_addr[i] : !(act_flags[i] & 0x08u)) { i = act_prev[i]; continue; }"]
                 if interactions else ["        (void)scripted;", "        if (!(act_flags[i] & 0x08u)) { i = act_prev[i]; continue; }"]),
              "        box = act_bounds + ((unsigned short)i << 2);",
              "        if ((unsigned short)(act_x[i] + (unsigned short)box[0]) <= r &&",
              "            (unsigned short)(act_x[i] + (unsigned short)box[1]) >= l &&",
              "            (unsigned short)(act_y[i] + (unsigned short)box[2]) <= b &&",
              "            (unsigned short)(act_y[i] + (unsigned short)box[3]) >= t) return i;",
              "        i = act_prev[i];", "    }", "    return 255u;", "}", "",
              "void ngc_topdown_update(void)", "{", "    unsigned char dir = 255u;",
              "    unsigned char dirs;", "    unsigned char ts;", "    unsigned char te;",
              "    unsigned char tile;", *(["    unsigned char hit;"] if interactions or hits else []),
              ("    if (((act_x[0] >> 5) & 15u) == 0u && ((act_y[0] >> 5) & 15u) == 8u) {" if grid == 16
               else "    if (((act_x[0] | act_y[0]) & 0xFFu) == 0u) {"),
              "        player_moving = 0u;",
              *(["        if (trigger_update()) return;"] if triggers else []),
              "        dirs = ngc_recent_joy ? ngc_recent_joy : ngc_joy;",
              "        if (dirs & 2u) dir = 3u; else if (dirs & 1u) dir = 1u;",
              "        else if (dirs & 4u) dir = 2u; else if (dirs & 8u) dir = 0u;",
              "        if (dir != 255u) {", "            player_moving = 1u;",
              "            if (dir == 3u || dir == 1u) {",
              "                ts = (unsigned char)((unsigned short)(act_y[0] + (unsigned short)act_bounds[2]) >> 8);",
              "                te = (unsigned char)((unsigned short)(act_y[0] + (unsigned short)act_bounds[3]) >> 8);",
              "                if (dir == 3u) {",
              "                    tile = (unsigned char)((unsigned short)(act_x[0] + (unsigned short)act_bounds[0]) >> 8);",
              "                    if (col_range_y(8u, (unsigned char)(tile - 1u), ts, te)) player_moving = 0u;",
              "                } else {",
              "                    tile = (unsigned char)((unsigned short)(act_x[0] + (unsigned short)act_bounds[1]) >> 8);",
              "                    if (col_range_y(4u, (unsigned char)(tile + 1u), ts, te)) player_moving = 0u;", "                }",
              "            } else {",
              "                ts = (unsigned char)((unsigned short)(act_x[0] + (unsigned short)act_bounds[0]) >> 8);",
              "                te = (unsigned char)((unsigned short)(act_x[0] + (unsigned short)act_bounds[1]) >> 8);",
              "                if (dir == 2u) {",
              "                    tile = (unsigned char)((unsigned short)(act_y[0] + (unsigned short)act_bounds[2]) >> 8);",
              "                    if (col_range_x(2u, (unsigned char)(tile - 1u), ts, te)) player_moving = 0u;",
              "                } else {",
              "                    tile = (unsigned char)((unsigned short)(act_y[0] + (unsigned short)act_bounds[3]) >> 8);",
              "                    if (col_range_x(1u, (unsigned char)(tile + 1u), ts, te)) player_moving = 0u;", "                }",
              "            }", "            ngc_actor_set_dir(0u, dir, player_moving);",
              "        } else set_anim(0u, act_dir[0]);",
              *(["        hit = player_overlap();",
                 "        if (hit != 255u && (act_collision_group[hit] & 0x0Fu)) collision_pending = hit;",
                 "        if (player_moving) {",
                 "            hit = player_front(0u);",
                 "            if (hit != 255u) collision_pending = hit;",
                 "        } else hit = 255u;",
                 "        if (hit != 255u) {"] if hits else
                ["        if (player_moving && player_front(0u) != 255u) {"]),
              "            act_fstart[0] = act_frame[0]; act_fend[0] = (unsigned char)(act_frame[0] + 1u);",
              "            player_moving = 0u;", "        }"]
    if interactions:
        lines += [f"        if (ngc_joy_pressed & {interact_mask}u) {{", "            hit = player_front(1u);",
                  ("            if (hit != 255u && !(act_collision_group[hit] & 0x0Fu)) {" if hits else "            if (hit != 255u) {"),
                  "                ngc_actor_set_dir(hit, (unsigned char)((act_dir[0] + 2u) & 3u), 0u);",
                  "                player_moving = 0u;", "                ngc_vm_interact(act_interact_addr[hit]);",
                  "            }", "        }"]
    off = 2 if grid == 16 else 1
    for direction, axis, sign in ((1, "x", "+"), (3, "x", "-"), (0, "y", "+"), (2, "y", "-")):
        lines.append(f"        {'if' if direction == 1 else 'else if'} (act_dir[0] == {direction}u) "
                     f"player_max_pos = (unsigned short)(((act_{axis}[0] >> 8) {sign} {off}u) << 8);")
    lines += ["    }", "    if (player_moving) {"]
    for direction, axis, sign, cmp in ((1, "x", "+", ">"), (3, "x", "-", "<"), (0, "y", "+", ">"), (2, "y", "-", "<")):
        pos = f"act_{axis}[0]"
        lines += [f"        {'if' if direction == 1 else 'else if'} (act_dir[0] == {direction}u) {{",
                  f"            {pos} = (unsigned short)({pos} {sign} act_speed[0]);",
                  f"            if ({pos} {cmp} player_max_pos) {pos} = player_max_pos;"]
        if sign == "-":
            lines.append(f"            else if (player_max_pos == 0u && {pos} >= (unsigned short)(0xFFFFu - act_speed[0])) {pos} = 0u;")
        lines.append("        }")
    lines += ["    }", "}", ""]
    return lines
