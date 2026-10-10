"""Specialized projectile pool from GBVM bd6f41c, pending scene/VM integration.

Definitions are resolved compiler data, not author events. ROM parameters are
copied once on launch; the frame loop reads arrays per field in RAM (D-2).
The caller must select this unit, its actor bridge and resident frame mapping.
"""
from dataclasses import dataclass
from .background_rom import c_array
from .compiler import CompileError
from .projectile_motion import projectile_motion_source

# Definition ids of projectiles loaded by Load Projectile Slot: SLOT_BASE +
# 5 * global table + entry (getGlobalProjectile's tables of five).
SLOT_BASE = 16

# Live projectiles at once: GB Studio 4.3.2 MAX_PROJECTILES (projectiles.h) by
# default; ngpcraft.json ngpc.projectile_capacity raises it, up to the 64
# hardware sprites (a projectile needs at least one). 29 bytes of RAM each.
GBS_PROJECTILE_CAPACITY = 5
NGPC_PROJECTILE_CAPACITY = 64


def projectile_capacity(project):
    """ngpcraft.json ngpc.projectile_capacity: GB Studio's 5 when absent."""
    ext = project.ngpc["ngpc"]
    return ext["projectile_capacity"] if "projectile_capacity" in ext else GBS_PROJECTILE_CAPACITY


@dataclass(frozen=True)
class ProjectileDefinition:
    bounds_subpx: tuple
    animations: tuple  # four inclusive order-index ranges, down/right/up/left
    life_frames: int
    speed_subpx: int
    initial_offset_subpx: int
    animation_tick_mask: int
    loop_animation: bool
    destroy_on_hit: bool
    collision_group: int
    collision_mask: int


def _integer(value, lo, hi, where):
    if type(value) is not int or not lo <= value <= hi:
        raise CompileError(f"{where}: expected integer {lo}..{hi}, got {value!r}")


def validate(definitions, frames, capacity, width_tiles, height_tiles, where):
    _integer(capacity, 1, 255, where + ":capacity (byte list index, 255 is the sentinel)")
    _integer(width_tiles, 1, 255, where + ":viewport_width_tiles")
    _integer(height_tiles, 1, 255, where + ":viewport_height_tiles")
    if not definitions or len(definitions) > 65536:
        raise CompileError(f"{where}: expected 1..65536 reachable definitions")
    if not frames or len(frames) > 65536:
        raise CompileError(f"{where}: expected 1..65536 ordered resident frames")
    for i, frame in enumerate(frames):
        _integer(frame, 0, 65535, f"{where}:frames[{i}]")
    for i, definition in enumerate(definitions):
        here = f"{where}:definitions[{i}]"
        if not isinstance(definition, ProjectileDefinition):
            raise CompileError(f"{here}: expected resolved ProjectileDefinition")
        if not isinstance(definition.bounds_subpx, (tuple, list)) or len(definition.bounds_subpx) != 4:
            raise CompileError(f"{here}: expected left/right/top/bottom bounds")
        for j, value in enumerate(definition.bounds_subpx):
            _integer(value, -32768, 32767, f"{here}:bounds_subpx[{j}]")
        l, r, t, b = definition.bounds_subpx
        if l > r or t > b:
            raise CompileError(f"{here}: inverted bounds")
        if not isinstance(definition.animations, (tuple, list)) or len(definition.animations) != 4:
            raise CompileError(f"{here}: expected four direction animations")
        for j, pair in enumerate(definition.animations):
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise CompileError(f"{here}:animations[{j}]: expected inclusive start/end")
            start, end = pair
            _integer(start, 0, len(frames)-1, f"{here}:animations[{j}].start")
            _integer(end, start, len(frames)-1, f"{here}:animations[{j}].end")
        for name, hi in (("life_frames", 65535), ("speed_subpx", 255), ("initial_offset_subpx", 65535),
                         ("animation_tick_mask", 255), ("collision_group", 255), ("collision_mask", 255)):
            _integer(getattr(definition, name), 0, hi, f"{here}:{name}")
        for name in ("loop_animation", "destroy_on_hit"):
            if type(getattr(definition, name)) is not bool:
                raise CompileError(f"{here}:{name}: expected boolean")


def projectile_ram_bytes(capacity):
    """Persistent pool only: 29 bytes/instance + two byte list heads.

    Excludes actor script handles, VM contexts, OAM/cache and C stack.
    """
    _integer(capacity, 1, 255, "projectile pool capacity")
    return 29 * capacity + 2


# projectiles.c PROJECTILES_COLLISION_SPREAD: the iterator mask of the collision
# test (one projectile in 4 or 2 per frame, rotating), None: every frame.
COLLISION_SPREADS = {"SPREAD_4": 3, "SPREAD_2": 1, "EVERY_FRAME": None}


def projectile_source(definitions, frames, *, capacity, width_tiles, height_tiles, where="projectile pool", definition_ids=None,
                      collision_spread="SPREAD_4"):
    validate(definitions, frames, capacity, width_tiles, height_tiles, where)
    if collision_spread not in COLLISION_SPREADS:
        raise CompileError(f"{where}: collision spread {collision_spread!r} unknown to GB Studio 4.3.2")
    spread = COLLISION_SPREADS[collision_spread]
    if definition_ids is None:
        definition_ids = tuple(range(len(definitions)))
    if not isinstance(definition_ids, (tuple, list)) or len(definition_ids) != len(definitions):
        raise CompileError(f"{where}: definition_ids must match resolved definitions")
    for index in definition_ids:
        _integer(index, 0, 65535, where + ":definition_ids")
    if len(set(definition_ids)) != len(definition_ids):
        raise CompileError(f"{where}: duplicate definition_ids")
    lines = projectile_motion_source()
    lines += ["/* GBVM projectiles.c: byte list links, per-field RAM, no per-tick definition lookup. */",
              "unsigned char proj_head;", "static unsigned char proj_free;"]
    for name, ctype, count in (("x", "unsigned short", capacity), ("y", "unsigned short", capacity),
                               ("dx", "short", capacity), ("dy", "short", capacity),
                               ("life", "unsigned short", capacity), ("frame", "unsigned short", capacity),
                               ("start", "unsigned short", capacity), ("end", "unsigned short", capacity),
                               ("bounds", "short", capacity * 4), ("tick", "unsigned char", capacity),
                               ("flags", "unsigned char", capacity), ("group", "unsigned char", capacity),
                               ("mask", "unsigned char", capacity), ("next", "unsigned char", capacity)):
        lines.append(f"static {ctype} proj_{name}[{count}];")
    c_array(lines, "proj_frames", frames)
    # Load Projectile Slot (projectiles.SLOT_BASE): GBVM projectile_defs[5]
    # as a slot -> definition map, the scene's own types at load_scene, a
    # loaded one replacing a slot until the next load_scene. A slot holding
    # no definition launches nothing (GBVM would read stale data, ECARTS 26).
    slots = any(n >= SLOT_BASE for n in definition_ids)
    if slots:
        lines += ["static unsigned short proj_slot[5];",
                  "void ngc_projectile_load_slot(unsigned char dest, unsigned short id)", "{",
                  "    if (dest < 5u) proj_slot[dest] = id;", "}", ""]
    lines += ["unsigned char ngc_projectile_hit(const short *bounds, unsigned short x, unsigned short y,",
              "                                unsigned char mask, unsigned char group);",
              "void ngc_sprite_draw(unsigned short frame, short x, short y);",
              "void ngc_projectiles_init(void)", "{", "    unsigned short i;",
              "    proj_head = 255u; proj_free = 255u;",
              f"    for (i = 0u; i < {capacity}u; ++i) {{",
              "        proj_next[i] = proj_free; proj_free = (unsigned char)i;", "    }",
              *([f"    proj_slot[{t}] = {t if t in definition_ids else 0xFFFF}u;" for t in range(5)] if slots else []),
              "}", "",
              "unsigned char ngc_projectile_launch(unsigned short type, unsigned short x, unsigned short y, unsigned char angle)",
              "{", "    unsigned char i;", "    unsigned char direction;", "    unsigned char speed;",
              "    unsigned short offset;", "    unsigned short pos[2];", "    short delta[2];", "    short *bounds;",
              *(["    if (type < 5u) type = proj_slot[type];"] if slots else []),
              "    i = proj_free;", "    if (i == 255u) return 0u;",
              "    direction = ngc_projectile_direction(angle);",
              "    bounds = proj_bounds + ((unsigned short)i << 2);", "    switch (type) {"]
    for n, definition in zip(definition_ids, definitions):
        flags = int(not definition.loop_animation) | (int(not definition.destroy_on_hit) << 1)
        lines += [f"    case {n}u:", f"        proj_life[i] = {definition.life_frames}u;",
                  f"        proj_tick[i] = {definition.animation_tick_mask}u;", f"        proj_flags[i] = {flags}u;",
                  f"        proj_group[i] = {definition.collision_group}u;", f"        proj_mask[i] = {definition.collision_mask}u;",
                  f"        speed = {definition.speed_subpx}u; offset = {definition.initial_offset_subpx}u;"]
        for j, value in enumerate(definition.bounds_subpx):
            lines.append(f"        bounds[{j}] = {value};")
        lines.append("        switch (direction) {")
        for j, (start, end) in enumerate(definition.animations):
            lines.append(f"        case {j}u: proj_start[i] = {start}u; proj_end[i] = {(end+1)&65535}u; break;")
        lines += ["        }", "        break;"]
    lines += ["    default: return 0u;  /* unreachable for a compiler-resolved type */", "    }",
              "    proj_frame[i] = proj_start[i];", "    pos[0] = x; pos[1] = y;",
              "    ngc_projectile_motion(pos, delta, angle, speed, offset);",
              "    proj_x[i] = pos[0]; proj_y[i] = pos[1]; proj_dx[i] = delta[0]; proj_dy[i] = delta[1];",
              "    proj_free = proj_next[i]; proj_next[i] = proj_head; proj_head = i;", "    return 1u;", "}", "",
              "static unsigned char proj_remove(unsigned char i, unsigned char previous)", "{",
              "    unsigned char next = proj_next[i];",
              "    if (previous == 255u) proj_head = next; else proj_next[previous] = next;",
              "    proj_next[i] = proj_free; proj_free = i; return next;", "}", "",
              "void ngc_projectiles_update(unsigned short time, short scroll_x, short scroll_y)", "{",
              "    unsigned char i;", "    unsigned char previous;",
              *(["    unsigned char iterator;"] if spread is not None else []),
              "    unsigned char tx;", "    unsigned char ty;", "    unsigned char lo_x;", "    unsigned char hi_x;",
              "    unsigned char lo_y;", "    unsigned char hi_y;", "    unsigned char edge;", "    unsigned short pos;",
              "    tx = (unsigned char)((scroll_x > 0 ? (unsigned short)scroll_x : 0u) >> 3);",
              "    ty = (unsigned char)((scroll_y > 0 ? (unsigned short)scroll_y : 0u) >> 3);",
              "    edge = (unsigned char)((unsigned short)tx - 2u); lo_x = edge < tx ? edge : 0u;",
              f"    edge = (unsigned char)((unsigned short)tx + {width_tiles}u + 2u); hi_x = edge > tx ? edge : 255u;",
              "    edge = (unsigned char)((unsigned short)ty - 2u); lo_y = edge < ty ? edge : 0u;",
              f"    edge = (unsigned char)((unsigned short)ty + {height_tiles}u + 2u); hi_y = edge > ty ? edge : 255u;",
              *(["    iterator = (unsigned char)time;"] if spread is not None else []),
              "    i = proj_head; previous = 255u;",
              "    while (i != 255u) {",
              "        if (proj_life[i] == 0u) { i = proj_remove(i, previous); continue; }",
              "        --proj_life[i];",
              "        if ((time & (unsigned short)proj_tick[i]) == 0u) {",
              "            proj_frame[i] = (unsigned short)(proj_frame[i] + 1u);",
              "            if (proj_frame[i] == proj_end[i]) {",
              "                if (!(proj_flags[i] & 1u)) proj_frame[i] = proj_start[i];",
              "                else proj_frame[i] = (unsigned short)(proj_frame[i] - 1u);", "            }", "        }",
              "        pos = (unsigned short)(proj_x[i] + (unsigned short)proj_dx[i]);",
              "        if ((unsigned char)(pos >> 8) < lo_x || (unsigned char)(pos >> 8) >= hi_x) {",
              "            i = proj_remove(i, previous); continue;", "        }", "        proj_x[i] = pos;",
              "        pos = (unsigned short)(proj_y[i] + (unsigned short)proj_dy[i]);",
              "        if ((unsigned char)(pos >> 8) < lo_y || (unsigned char)(pos >> 8) >= hi_y) {",
              "            i = proj_remove(i, previous); continue;", "        }", "        proj_y[i] = pos;",
              *([f"        if ((iterator & {spread}u) == 0u) {{",
                 "            iterator = (unsigned char)((unsigned short)iterator + 1u);",
                 "            if (ngc_projectile_hit(proj_bounds + ((unsigned short)i << 2), proj_x[i], proj_y[i], proj_mask[i], proj_group[i])",
                 "                && !(proj_flags[i] & 2u)) { i = proj_remove(i, previous); continue; }",
                 "        } else iterator = (unsigned char)((unsigned short)iterator + 1u);"] if spread is not None else
                ["        if (ngc_projectile_hit(proj_bounds + ((unsigned short)i << 2), proj_x[i], proj_y[i], proj_mask[i], proj_group[i])",
                 "            && !(proj_flags[i] & 2u)) { i = proj_remove(i, previous); continue; }"]),
              "        previous = i; i = proj_next[i];", "    }", "}", "",
              "/* Append to actor OAM; the caller alone begins/ends the sprite list. */",
              "void ngc_projectiles_render(short scroll_x, short scroll_y)", "{", "    unsigned char i = proj_head;",
              "    short x;", "    short y;", "    while (i != 255u) {",
              "        x = (short)(proj_x[i] >> 5) - scroll_x; y = (short)(proj_y[i] >> 5) - scroll_y;",
              "        ngc_sprite_draw(proj_frames[proj_frame[i]], x, y); i = proj_next[i];", "    }", "}", ""]
    return lines


def projectile_collision_source(groups, where="projectile actor bridge"):
    """Place in the actor object to reuse current bounds, flags and prev chain.

    ngc_projectile_script owns the actor's hit handle/VM argument; collision
    acceptance does not depend on whether that script can currently execute.
    """
    if not groups or len(groups) > 255:
        raise CompileError(f"{where}: expected 1..255 actor groups, player first")
    for i, group in enumerate(groups):
        _integer(group, 0, 255, f"{where}:groups[{i}]")
    lines = []
    c_array(lines, "proj_actor_groups", groups, "unsigned char")
    lines += ["void ngc_projectile_script(unsigned char actor, unsigned char group);",
              "/* GBVM actor_overlapping_bb returns the first overlapping actor, then the",
              " * mask is checked: an actor outside the mask (the player next to his own",
              " * melee hit) hid the others. NGPC (docs/ECARTS_GBSTUDIO.md, projectiles):",
              " * actors outside the mask are skipped, the first one in it is hit. */",
              "unsigned char ngc_projectile_hit(const short *b, unsigned short x, unsigned short y, unsigned char mask, unsigned char group)",
              "{", "    unsigned char i = 0u;", "    const short *o;",
              "    unsigned short left = (unsigned short)(x + (unsigned short)b[0]);",
              "    unsigned short right = (unsigned short)(x + (unsigned short)b[1]);",
              "    unsigned short top = (unsigned short)(y + (unsigned short)b[2]);",
              "    unsigned short bottom = (unsigned short)(y + (unsigned short)b[3]);",
              "    while (i != 255u) {", "        o = act_bounds + ((unsigned short)i << 2);",
              "        if ((act_flags[i] & 8u) && (proj_actor_groups[i] & mask) &&",
              "            (unsigned short)(act_x[i] + (unsigned short)o[0]) <= right &&",
              "            (unsigned short)(act_x[i] + (unsigned short)o[1]) >= left &&",
              "            (unsigned short)(act_y[i] + (unsigned short)o[2]) <= bottom &&",
              "            (unsigned short)(act_y[i] + (unsigned short)o[3]) >= top) {",
              "            ngc_projectile_script(i, group); return 1u;", "        }",
              "        if (mask == 1u) return 0u;  /* player-only fast path */",
              "        i = act_prev[i];", "    }", "    return 0u;", "}", ""]
    return lines
