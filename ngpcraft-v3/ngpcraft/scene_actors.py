"""Actor state, animation, activation and render order: specialized C per scene.

Semantics of GBVM (commit bd6f41c), function for function: src/core/actor.c
(actors_update, activate/deactivate_actor_impl, activate_actors_in_row/col,
activate_persistent_actors, actor_set_frames, actor_set_dir, actors_render
order), include/actor.h (actor_set_anim), include/linked_list.h, the
activation part of src/core/scroll.c (scroll_update, scroll_render_rows, no
parallax) and the scene start of core.c / data_manager.c load_scene.
Contract rules C12-C15. NGPC screen 160x152 (ECARTS 1).

D-2 form (cahier 5.5): instance data in RAM arrays per field, list links as
8-bit indexes (0xFF = none) instead of pointers, sheet data in ROM tables.
Movement steps are specialized here; On Update bytecode is run by the VM,
started/stopped at actor activation/deactivation. tools/gbvm_actors.py is the
line-by-line oracle used by the probes.
"""
from .background_rom import c_array
from .compiler import CompileError

FLAGS = {"pinned": 1, "hidden": 2, "anim_noloop": 4, "collision": 8, "persistent": 0x10}
NIL = 0xFF
from .actor_capacity import NGPC_ACTOR_CAPACITY

MAX_ACTORS = NGPC_ACTOR_CAPACITY + 1  # NGPC ceiling; scene_init requires explicit opt-in above GB capacity


MOVE_FEATURES = ("walls", "actors", "relative")


def _collision_source(bounds, collisions, width_tiles, height_tiles, walls, actors):
    lines = ["static unsigned char tile_hit_x;", "static unsigned char tile_hit_y;"] if walls else []
    if walls or actors:
        lines.append("/* compileBounds per actor: left, right, top, bottom (1/32 px). */")
        c_array(lines, "act_bounds", [v for box in bounds for v in box], "short")
    if walls:
        lines.append(f"/* Scene collisions ({width_tiles} x {height_tiles} tiles, compileData.ts). */")
        c_array(lines, "coll_map", list(collisions), "unsigned char")
        W, H = width_tiles, height_tiles
        for axis, fixed, moving, step in (("x", "ty", "tx", "1"), ("y", "tx", "ty", f"{W}")):
            hit = "tile_hit_x" if axis == "x" else "tile_hit_y"
            limit = W if axis == "x" else H
            lines += [
                f"/* collision.c tile_col_test_range_{axis} */",
                f"static unsigned char col_range_{axis}(unsigned char mask, unsigned char {fixed}, "
                f"unsigned char {moving}_start, unsigned char {moving}_end)", "{",
                "    const unsigned char *p;", "    unsigned char tile;", "    unsigned char inc;",
                f"    tile_hit_{'x' if axis == 'x' else 'y'} = {moving}_start;",
                f"    tile_hit_{'y' if axis == 'x' else 'x'} = {fixed};",
                f"    if (tile_hit_x >= {W}u || tile_hit_y >= {H}u) return (unsigned char)((0x0Fu & mask) ? 0x0Fu : 0u);",
                f"    inc = (unsigned char)((unsigned char)({moving}_start - {moving}_end) & 0x80u);  /* UBYTE_LESS_THAN */",
                (f"    p = coll_map + (unsigned short)ty * {W}u + tx_start;" if axis == "x" else
                 f"    p = coll_map + (unsigned short)ty_start * {W}u + tx;"),
                "    for (;;) {", "        tile = *p;", "        if (tile & mask) return tile;",
                f"        if ({hit} == {moving}_end) break;",
                f"        if (inc) {{ ++{hit}; p += {step}; }} else {{ --{hit}; p -= {step}; }}",
                f"        if ({hit} >= {limit}u) return (unsigned char)((0x0Fu & mask) ? 0x0Fu : 0u);",
                "    }", "    return 0u;", "}", ""]
    return lines


def _moves_source(n, moves, bounds, collisions, width_tiles, height_tiles, cancel=False, topdown=False, push=False, split_moves=True, state_walls=None):
    """vm_actor.c movement, actor side (vm_actor_move_to_init / _x / _y / _xy /
    _set_dir_x / _set_dir_y / vm_actor_set_anim_moving, check_collision_*),
    collision.c tile_col_test_range_x/y, actor.c actor_overlapping_bb. The VM
    side (waitable, PC rewind) is in gbvm.c. p points to X, Y of act_move_to_t.
    moves: the features the scripts use (MOVE_FEATURES): code, bounds and
    collision map are emitted only for them. Interrupt checks are emitted only
    when the reachable scripts use VM_ACTOR_MOVE_CANCEL.
    Arithmetic in 16-bit unsigned words as SDCC does (int = 16 bits)."""
    walls, actors, relative = ("walls" in moves, "actors" in moves, "relative" in moves)
    W, H = width_tiles, height_tiles
    state_walls = topdown if state_walls is None else state_walls
    lines = _collision_source(bounds, collisions, width_tiles, height_tiles, walls or state_walls, actors or topdown)
    if walls:
        # check_collision_horizontal / vertical: (start_x, start_y, bounds, end_pos)
        lines += [
            "/* vm_actor.c check_collision_horizontal: bounds b = left, right, top, bottom. */",
            "static unsigned short coll_h(unsigned short sx, unsigned short sy, const short *b, unsigned short end)",
            "{", "    unsigned char tx1;", "    unsigned char ty1;", "    unsigned char tx2;", "    unsigned char ty2;",
            "    unsigned char mask;",
            "    ty1 = (unsigned char)((unsigned short)(sy + (unsigned short)b[2]) >> 8);",
            "    ty2 = (unsigned char)((unsigned char)((unsigned short)(sy + (unsigned short)b[3]) >> 8) + 1u);",
            "    if (sx > end) {",
            "        mask = 0x08u;                                     /* COLLISION_RIGHT */",
            "        tx1 = (unsigned char)((unsigned short)(sx + (unsigned short)b[0]) >> 8);",
            "        tx2 = (unsigned char)((unsigned short)(end + (unsigned short)b[0]) >> 8);",
            "        if (tx2 > tx1) tx2 = 0u;", "    } else {",
            "        mask = 0x04u;                                     /* COLLISION_LEFT */",
            "        tx1 = (unsigned char)((unsigned short)(sx + (unsigned short)b[1]) >> 8);",
            "        tx2 = (unsigned char)((unsigned short)(end + (unsigned short)b[1]) >> 8);",
            f"        if (tx2 < tx1) tx2 = {W}u;", "    }",
            "    while (ty1 != ty2) {",
            "        if (col_range_x(mask, ty1, tx1, tx2)) {",
            "            return (sx > end)",
            "                ? (unsigned short)(((unsigned short)tile_hit_x << 8) - (unsigned short)b[0] + 256u)",
            "                : (unsigned short)(((unsigned short)tile_hit_x << 8) - (unsigned short)(b[1] + 1));",
            "        }", "        ++ty1;", "    }", "    return end;", "}", "",
            "/* vm_actor.c check_collision_vertical */",
            "static unsigned short coll_v(unsigned short sx, unsigned short sy, const short *b, unsigned short end)",
            "{", "    unsigned char tx1;", "    unsigned char ty1;", "    unsigned char tx2;", "    unsigned char ty2;",
            "    unsigned char mask;",
            "    tx1 = (unsigned char)((unsigned short)(sx + (unsigned short)b[0]) >> 8);",
            "    tx2 = (unsigned char)((unsigned char)((unsigned short)(sx + (unsigned short)b[1]) >> 8) + 1u);",
            "    if (sy > end) {",
            "        mask = 0x02u;                                     /* COLLISION_BOTTOM */",
            "        ty1 = (unsigned char)((unsigned short)(sy + (unsigned short)b[2]) >> 8);",
            "        ty2 = (unsigned char)((unsigned short)(end + (unsigned short)b[2]) >> 8);",
            "        if (ty2 > ty1) ty2 = 0u;", "    } else {",
            "        mask = 0x01u;                                     /* COLLISION_TOP */",
            "        ty1 = (unsigned char)((unsigned short)(sy + (unsigned short)b[3]) >> 8);",
            "        ty2 = (unsigned char)((unsigned short)(end + (unsigned short)b[3]) >> 8);",
            f"        if (ty2 < ty1) ty2 = {H}u;", "    }",
            "    while (tx1 != tx2) {",
            "        if (col_range_y(mask, tx1, ty1, ty2)) {",
            "            return (sy > end)",
            "                ? (unsigned short)(((unsigned short)tile_hit_y << 8) - (unsigned short)b[2] + 256u)",
            "                : (unsigned short)(((unsigned short)tile_hit_y << 8) - (unsigned short)(b[3] + 1));",
            "        }", "        ++tx1;", "    }", "    return end;", "}", ""]
    if actors:
        lines += [
            "/* actor.c actor_overlapping_bb(&actor->bounds, &actor->pos, actor): from the",
            " * player along prev, actors with COLLISION other than i; unsigned words. */",
            "static unsigned char overlapping(unsigned char i)", "{",
            "    const short *b = act_bounds + ((unsigned short)i << 2);", "    const short *o;",
            "    unsigned short l = (unsigned short)(act_x[i] + (unsigned short)b[0]);",
            "    unsigned short r = (unsigned short)(act_x[i] + (unsigned short)b[1]);",
            "    unsigned short t = (unsigned short)(act_y[i] + (unsigned short)b[2]);",
            "    unsigned short d = (unsigned short)(act_y[i] + (unsigned short)b[3]);",
            "    unsigned char j = 0u;",
            f"    while (j != {NIL}u) {{",
            "        o = act_bounds + ((unsigned short)j << 2);",
            "        if (j == i || !(act_flags[j] & 0x08u) ||",
            "            (unsigned short)(act_x[j] + (unsigned short)o[0]) > r ||",
            "            (unsigned short)(act_x[j] + (unsigned short)o[1]) < l ||",
            "            (unsigned short)(act_y[j] + (unsigned short)o[2]) > d ||",
            "            (unsigned short)(act_y[j] + (unsigned short)o[3]) < t) { j = act_prev[j]; continue; }",
            ("        return (unsigned char)((unsigned short)j + 1u);" if push else "        return 1u;"),
            "    }", "    return 0u;", "}", ""]
    if relative:
        lines += [
            "/* math.h saturating_add_u16 */",
            "static unsigned short sat_add(unsigned short base, unsigned short delta)", "{",
            "    unsigned short m;",
            "    if (!(delta & 0x8000u)) return (base > (unsigned short)(0xFFFFu - delta)) ? 0xFFFFu : (unsigned short)(base + delta);",
            "    m = (unsigned short)(0u - delta);",
            "    return (m > base) ? 0u : (unsigned short)(base - m);", "}", ""]
    lines += [
        "void ngc_actor_move_init(unsigned char i, unsigned short *p, unsigned char attr)", "{",
        *(["    const short *b = act_bounds + ((unsigned short)i << 2);"] if walls else []),
        "    (void)attr;",
        *(["    act_flags[i] &= 0x7Fu;  /* clear INTERRUPT on new movement */"] if cancel else []),
        "    act_x[i] &= 0xFFE0u;                                   /* SUBPX_SNAP_PX */",
        "    act_y[i] &= 0xFFE0u;"]
    if relative:
        lines += ["    if (attr & 0x18u) {                                    /* ACTOR_ATTR_RELATIVE */",
                  "        p[0] = sat_add(act_x[i], p[0]);", "        p[1] = sat_add(act_y[i], p[1]);", "    }",
                  "    if (attr & 0x08u) { p[0] &= 0xFFE0u; p[1] &= 0xFFE0u; }",
                  "    else if (attr & 0x10u) { p[0] &= 0xFF00u; p[1] &= 0xFF00u; }"]
    if walls:
        lines += ["    if (attr & 0x02u) {                                    /* CHECK_COLL_WALLS */",
                  "        if (attr & 0x01u) {                                /* H_FIRST */",
                  "            if (act_x[i] != p[0]) p[0] = coll_h(act_x[i], act_y[i], b, p[0]);",
                  "            if (act_y[i] != p[1]) p[1] = coll_v(p[0], act_y[i], b, p[1]);",
                  "        } else {",
                  "            if (act_y[i] != p[1]) p[1] = coll_v(act_x[i], act_y[i], b, p[1]);",
                  "            if (act_x[i] != p[0]) p[0] = coll_h(act_x[i], p[1], b, p[0]);",
                  "        }", "    }"]
    lines += ["}", ""]
    if not split_moves:
        return lines
    # One step along an axis: 1 = not arrived (the VM rewinds), 0 = done.
    for axis, other, lo, hi in (("x", "y", 3, 1), ("y", "x", 2, 0)):
        pos, oth = f"act_{axis}[i]", f"act_{other}[i]"
        k, ko = (0, 1) if axis == "x" else (1, 0)

        def hit(sign, axis=axis, pos=pos, oth=oth, ko=ko, done="return 0u;"):
            if not actors:
                return []
            back = "+" if sign == "-" else "-"
            return [f"        if ((attr & 0x20u) && overlapping(i)) {{",
                    f"            {pos} = (unsigned short)({pos} {back} act_speed[i]);",
                    f"            p[{ko}] = {oth};", f"            {done}", "        }"]
        lines += [
            f"unsigned char ngc_actor_move_{axis}(unsigned char i, unsigned short *p, unsigned char attr)", "{",
            "    (void)attr;",
            *(["    if (act_flags[i] & 0x80u) return 0u;"] if cancel else []),
            f"    if (p[{k}] == {pos}) {{ set_anim(i, act_dir[i]); return 0u; }}",
            f"    if (p[{k}] < {pos}) {{",
            f"        {pos} = (unsigned short)({pos} - act_speed[i]);",
            *hit("-", done="set_anim(i, act_dir[i]); return 0u;"),
            f"        if ({pos} <= p[{k}]) {{ {pos} = p[{k}]; set_anim(i, act_dir[i]); return 0u; }}",
            "    } else {",
            f"        {pos} = (unsigned short)({pos} + act_speed[i]);",
            *hit("+", done="set_anim(i, act_dir[i]); return 0u;"),
            f"        if ({pos} >= p[{k}]) {{ {pos} = p[{k}]; set_anim(i, act_dir[i]); return 0u; }}",
            "    }", "    return 1u;", "}", "",
            f"void ngc_actor_move_dir_{axis}(unsigned char i, const unsigned short *p)", "{",
            *(["    if (act_flags[i] & 0x80u) return;"] if cancel else []),
            f"    if (p[{k}] != {pos}) ngc_actor_set_dir(i, (unsigned char)(p[{k}] < {pos} ? {lo}u : {hi}u), 1u);",
            "}", ""]
    xy = ["unsigned char ngc_actor_move_xy(unsigned char i, unsigned short *p, unsigned char attr)", "{",
          "    unsigned char rx = (unsigned char)(p[0] == act_x[i]);",
          "    unsigned char ry = (unsigned char)(p[1] == act_y[i]);", "    (void)attr;"]
    if cancel:
        xy.append("    if (act_flags[i] & 0x80u) return 0u;")
    for axis, other, k, ko, flag in (("x", "y", 0, 1, "rx"), ("y", "x", 1, 0, "ry")):
        pos, oth = f"act_{axis}[i]", f"act_{other}[i]"
        for cmp_, sign, back, arrive in (("<", "-", "+", "<="), (None, "+", "-", ">=")):
            body = [f"        {pos} = (unsigned short)({pos} {sign} act_speed[i]);"]
            if actors:
                body += [f"        if ((attr & 0x20u) && overlapping(i)) {{",
                         f"            {pos} = (unsigned short)({pos} {back} act_speed[i]);",
                         f"            p[{ko}] = {oth};", f"            {flag} = 1u;", "        }"]
            body += [f"        if ({pos} {arrive} p[{k}]) {{ {pos} = p[{k}]; {flag} = 1u; }}"]
            if cmp_:
                xy += [f"    if (!{flag}) {{", f"      if (p[{k}] < {pos}) {{", *["  " + b for b in body], "      } else {"]
            else:
                xy += [*["  " + b for b in body], "      }", "    }"]
    xy += ["    if (rx && ry) { set_anim(i, act_dir[i]); return 0u; }", "    return 1u;", "}", "",
           "void ngc_actor_set_anim_moving(unsigned char i)", "{",
           "    set_anim(i, (unsigned char)(act_dir[i] + 4u));", "}", ""]
    lines += xy
    return lines



def _push_source(cancel=False):
    """vm_actor.c vm_actor_move_to on actor arrays. actorPush uses CHECK_COLL,
    absolute X/Y, vertical first. State bits and chronology are GBVM's;
    collision places the actor at the hit bounds (unlike the split opcodes).
    Returns 1 to rewind the VM PC, 0 to finish this instruction."""
    lines = [
        "/* GBVM vm_actor_move_to: push state (ALLOW 1/2, DIR 4/8, ACTIVE 16/32, NEEDED 64/128). */",
        "unsigned char ngc_actor_push_step(unsigned char i, unsigned short *p, unsigned char *flags)", "{",
        "    unsigned char hit;", "    unsigned char dir;", "    const short *b;", "    const short *o;",
        "    if (!*flags) {",
        "        set_anim(i, (unsigned char)(act_dir[i] + 4u));",
        "        ngc_actor_move_init(i, p, 0x22u);",
        "        *flags = 2u;  /* MOVE_ALLOW_V; no H_FIRST or DIAGONAL */",
        "        if (act_x[i] != p[0]) *flags |= 64u; else *flags |= 2u;",
        "        if (act_y[i] != p[1]) *flags |= 128u; else *flags |= 1u;",
        "        if (act_x[i] > p[0]) *flags |= 4u;",
        "        if (act_y[i] > p[1]) *flags |= 8u;",
        "        return 1u;", "    }",
        *(["    if (act_flags[i] & 0x80u) return 0u;"] if cancel else []),
        "    b = act_bounds + ((unsigned short)i << 2);"]
    for axis, k, allow, needed, direction, active, lo_dir, hi_dir, lo_bound, hi_bound, other_allow in (
            ("x", 0, 1, 64, 4, 16, 3, 1, 0, 1, 2),
            ("y", 1, 2, 128, 8, 32, 2, 0, 2, 3, 1)):
        pos = f"act_{axis}[i]"
        lines += [
            f"    if ((*flags & {allow | needed}u) == {allow | needed}u) {{",
            f"        dir = (unsigned char)((*flags & {direction}u) ? {lo_dir}u : {hi_dir}u);",
            f"        {pos} = (unsigned short)((*flags & {direction}u) ? {pos} - act_speed[i] : {pos} + act_speed[i]);",
            "        hit = overlapping(i);  /* index+1; 0 means no hit */",
            "        if (hit) {", "            --hit;",
            "            o = act_bounds + ((unsigned short)hit << 2);",
            f"            {pos} = (unsigned short)(act_{axis}[hit] + ((*flags & {direction}u)",
            f"                ? o[{hi_bound}] - b[{lo_bound}] + 1 : o[{lo_bound}] - b[{hi_bound}] - 1));",
            "            *flags = 0u;", "            set_anim(i, act_dir[i]);", "            return 0u;", "        }",
            f"        if (!(*flags & {active}u)) {{ *flags |= {active}u; ngc_actor_set_dir(i, dir, 1u); }}",
            f"        if ((*flags & {direction}u) ? {pos} <= p[{k}] : {pos} >= p[{k}]) {{",
            f"            {pos} = p[{k}];", f"            *flags |= {other_allow}u;",
            f"            *flags &= {255 ^ (allow | needed)}u;", "        }", "    }"]
    lines += ["    if (!(*flags & 192u)) { *flags = 0u; set_anim(i, act_dir[i]); return 0u; }",
              "    return 1u;", "}", ""]
    return lines


def _cancel_source():
    """vm_actor_move_cancel: waitable, nearest tile, both axes, no collision or
    animation change. Destination is recomputed at every invocation, in UWORD."""
    lines = ["/* GBVM vm_actor.c vm_actor_move_cancel */",
             "unsigned char ngc_actor_move_cancel(unsigned char i)", "{",
             "    unsigned short dx;", "    unsigned short dy;",
             "    act_flags[i] |= 0x80u;  /* INTERRUPT until next move init */"]
    for axis in ("x", "y"):
        pos, dest = f"act_{axis}[i]", f"d{axis}"
        lines += [f"    {dest} = {pos} & 0xFF00u;",
                  f"    if ({pos} & 0x0080u) {dest} = (unsigned short)({dest} + 256u);",
                  f"    if ({pos} > {dest}) {{",
                  f"        {pos} = (unsigned short)({pos} - act_speed[i]);",
                  f"        if ({pos} < {dest}) {pos} = {dest};",
                  f"    }} else if ({pos} < {dest}) {{",
                  f"        {pos} = (unsigned short)({pos} + act_speed[i]);",
                  f"        if ({pos} > {dest}) {pos} = {dest};", "    }"]
    lines += ["    return (unsigned char)(act_x[i] != dx || act_y[i] != dy);", "}", ""]
    return lines


def actors_source(actors, sheets, height_px, where="scene actors", view_h=152, moves=None, bounds=(),
                  collisions=b"", width_px=0, updates=None, cancel=False, topdown=None, interactions=None, triggers=None, hits=None,
                  stop_update=False, start_update=False, actor_states=None, read_positions=False, camera_shake=False, read_directions=False, push=False, split_moves=True, sprite_switches=(), sprite_states=None, emotes=(), emote_origins=(), resume=False, set_bounds=False):
    """view_h: SCREENHEIGHT of the scene (152, or 144 for a GB Studio scene shown
    between black bands, ECARTS 1): camera half height, SCREEN_TILE_REFRES_H."""
    refres_h = (view_h >> 3) + 3        # SCREEN_TILES_H + PAD_TOP + PAD_BOTTOM
    """actors: [{sheet, x, y (1/32 px), dir (0..3), flags, anim_tick}], actors[0] = player.
    sheets: NgpcSheet list (same order as the sprite unit)."""
    n = len(actors)
    public_states = actor_states is not None
    if sprite_switches and actor_states is None:
        if not sprite_states:
            raise CompileError(f"{where}: sprite switching requires the project's animation states")
        actor_states = sprite_states
    if not 1 <= n <= MAX_ACTORS:
        raise CompileError(f"{where}: {n} actors, NGPC allows 1..{MAX_ACTORS} (player included)")
    order, order_base, anims = [], [], []
    frame_base = 0
    sheet_order_base, sheet_frame_base = [], []
    for sheet in sheets:
        sheet_order_base.append(len(order))
        sheet_frame_base.append(frame_base)
        order += [frame_base + f for f in sheet.frame_order]
        frame_base += len(sheet.frames)
    for i, actor in enumerate(actors):
        s = actor["sheet"]
        if not 0 <= s < len(sheets):
            raise CompileError(f"{where}: actor {i} uses an unknown sheet {s}")
        if actor["dir"] not in (0, 1, 2, 3) or not 0 <= actor["anim_tick"] <= 255 or not 0 <= actor["move_speed"] <= 255:
            raise CompileError(f"{where}: actor {i} direction or animation tick out of range")
        if not 0 <= actor["x"] <= 0xFFFF or not 0 <= actor["y"] <= 0xFFFF:
            raise CompileError(f"{where}: actor {i} position out of range")
        order_base.append(sheet_order_base[s])
        for start, end in sheets[s].animation_offsets[:8]:   # ANIM_SET_DEFAULT: first state
            anims += [start, end]
    lines = ["/* Original V3 actor runtime; semantics of GBVM actor.c, scroll.c activation,",
             " * load_scene. RAM arrays per field, 8-bit list links (0xFF = none). */",
             "void ngc_scroll_set(short x, short y);", "extern unsigned short ngc_scroll_next[2];",
             "void ngc_sprites_begin(void);", "void ngc_sprite_draw(unsigned short frame, short x, short y);",
             "void ngc_sprites_end(void);", ""]
    if updates:
        if any(type(i) is not int or not 1 <= i < n or not 0 < addr <= 65535
               for i, addr in updates.items()):
            raise CompileError(f"{where}: invalid actor update addresses")
        lines += ["void ngc_vm_execute(unsigned short addr, unsigned short *handle);",
                  "void ngc_vm_terminate(unsigned short handle);",
                  f"static unsigned short act_update_handle[{n}];"]
        c_array(lines, "act_update_addr", [updates[i] if i in updates else 0 for i in range(n)])
    c_array(lines, "act_spr_order", order)
    # Bounds in RAM when a script changes them (Set Spritesheet, Set Collision
    # Box): reset from act_init_bounds at each scene load.
    mutable_bounds = bool(sprite_switches) or set_bounds
    if mutable_bounds:
        if len(bounds) != n:
            raise CompileError(f"{where}: collision boxes inconsistent ({len(bounds)} for {n} actors)")
        c_array(lines, "act_init_bounds", [v for box in bounds for v in box], "short")
        lines.append(f"static short act_bounds[{4 * n}];")
    if sprite_switches:
        if len(sheets) > 65536:
            raise CompileError(f"{where}: spritesheet switching data exceeds actor/sheet capacity")
        c_array(lines, "act_init_order_base", order_base)
        lines.append(f"static unsigned short act_order_base[{n}];")
        c_array(lines, "act_init_sheet", [a["sheet"] for a in actors])
        lines.append(f"static unsigned short act_sheet[{n}];")
        for s in sorted({entry[1] for entry in sprite_switches}):
            c_array(lines, f"act_sheet_bounds_{s}", next(e[2] for e in sprite_switches if e[1] == s), "short")
    else:
        c_array(lines, "act_order_base", order_base)
    if camera_shake:
        lines.append("extern unsigned short ngc_scroll_base_next[2];")
    lines.append("/* Per actor, 8 animations: start, end (indexes in the ordered frames). */")
    c_array(lines, "act_anims", anims, "unsigned char")
    # Platformer animation states (load_animations, data_manager.c): every
    # state of the player's sheet, and animations_lookup over the project's
    # statesOrder (compileSpriteSheet: indexOf(state) * 8, 0 if absent).
    states = topdown[4] if topdown is not None and len(topdown) > 4 else None
    if states is not None:
        if actor_states is None:
            player_sheet = sheets[actors[0]["sheet"]]
            names = list(player_sheet.state_names)
            lines.append("/* Player sheet: 8 animations (start, end) per state, all states. */")
            c_array(lines, "act_player_anim_sets", [v for pair in player_sheet.animation_offsets for v in pair],
                    "unsigned char")
            c_array(lines, "act_player_anim_lookup",
                    [max(0, names.index(st) if st in names else 0) * 16 for st in states], "unsigned short")
            lines.append("static const unsigned char *act_player_anims;")
        lines.append("unsigned char ngc_player_anim_set;        /* last load_animations set (probes) */")
    state_loader = public_states or states is not None
    if actor_states is not None:
        lines.append(f"static unsigned char act_state_anims[{16 * n}];")
        for s in sorted({a["sheet"] for a in actors} | {e[1] for e in sprite_switches}):
            sheet = sheets[s]
            names = list(sheet.state_names)
            c_array(lines, f"act_state_sets_{s}", [v for pair in (sheet.animation_offsets if state_loader else sheet.animation_offsets[:8]) for v in pair],
                    "unsigned char")
            if state_loader:
                c_array(lines, f"act_state_lookup_{s}",
                        [(names.index(st) if st in names else 0) * 16 for st in actor_states], "unsigned short")
        # Exactly load_animations: copy only on an event/state transition. The
        # normal animation path reads RAM, without a per-frame sheet lookup.
        if state_loader:
            lines += ["static void actor_load_anim_set(unsigned char i, unsigned short set)", "{",
                      "    const unsigned char *src;", "    unsigned char *dst;", "    unsigned char k;",
                      f"    if (set >= {len(actor_states)}u) set = 0u;",
                      "    switch (act_sheet[i]) {" if sprite_switches else "    switch (i) {"]
            for s in sorted({a["sheet"] for a in actors} | {e[1] for e in sprite_switches}):
                lines += ([f"    case {s}u:"] if sprite_switches else
                          [f"    case {i}u:" for i, a in enumerate(actors) if a["sheet"] == s])
                lines += [f"        src = act_state_sets_{s} + act_state_lookup_{s}[set]; break;"]
            lines += ["    default: return;", "    }", "    dst = act_state_anims + ((unsigned short)i << 4);",
                      "    for (k = 16u; k != 0u; --k) *dst++ = *src++;", "}", ""]
    if emotes:
        from .emote_runtime import emote_source
        lines += emote_source(emotes)
        if len(emote_origins) != len(sheets):
            raise CompileError(f"{where}: emote origins inconsistent with actor spritesheets")
        c_array(lines, "act_emote_origin", list(emote_origins) if sprite_switches else
                [emote_origins[a["sheet"]] for a in actors], "short")
        c_array(lines, "act_emote_offsets", [2,1,0,-1,-2,-3,-4,-5,-6,-5,-4,-3,-2,-1,0], "signed char")
    c_array(lines, "act_init_x", [a["x"] for a in actors])
    c_array(lines, "act_init_y", [a["y"] for a in actors])
    c_array(lines, "act_init_dir", [a["dir"] for a in actors], "unsigned char")
    c_array(lines, "act_init_flags", [a["flags"] for a in actors], "unsigned char")
    c_array(lines, "act_init_tick", [a["anim_tick"] for a in actors], "unsigned char")
    c_array(lines, "act_init_speed", [a["move_speed"] for a in actors], "unsigned char")
    decl = lambda t, name: f"static {t} {name}[{n}];"
    # Positions are public: probes read the player at its link address.
    lines += [f"unsigned short act_x[{n}];", f"unsigned short act_y[{n}];",
              decl("unsigned char", "act_speed"),
              *[decl("unsigned char", f) for f in ("act_dir", "act_flags", "act_frame", "act_fstart",
                                                  "act_fend", "act_tick", "act_anim", "act_next", "act_prev")],
              "static unsigned char act_head;", "static unsigned char act_tail;",
              "static unsigned char inact_head;",
              "/* GBVM scroll_x/y (0x7FFF after scroll_reset) and draw_scroll_x/y. */",
              "static unsigned short gb_scroll_x;", "static unsigned short gb_scroll_y;",
              "static unsigned short draw_x;", "static unsigned short draw_y;",
              "unsigned short ngc_game_time;", "",
              "static unsigned char dl_push(unsigned char head, unsigned char i)", "{",
              f"    act_prev[i] = {NIL}u;", "    act_next[i] = head;",
              f"    if (head != {NIL}u) act_prev[head] = i;", "    return i;", "}", "",
              "static unsigned char dl_remove(unsigned char head, unsigned char i)", "{",
              "    unsigned char nx = act_next[i];", "    unsigned char pv = act_prev[i];",
              f"    if (nx != {NIL}u && pv != {NIL}u) {{ act_next[pv] = nx; act_prev[nx] = pv; }}",
              f"    else if (nx != {NIL}u) {{ act_prev[nx] = {NIL}u; head = nx; }}",
              f"    else if (pv != {NIL}u) act_next[pv] = {NIL}u;",
              f"    else head = {NIL}u;", "    return head;", "}", "",
              "static void set_anim(unsigned char i, unsigned char anim)", "{",
              "    const unsigned char *a;", "    unsigned char start;", "    unsigned char end;",
              "    act_anim[i] = anim;",
              ("    a = act_state_anims + ((unsigned short)i << 4) + (anim << 1);"
               if actor_states is not None else ("    a = (i ? act_anims + ((unsigned short)i << 4) : act_player_anims) + (anim << 1);"
               if states is not None else "    a = act_anims + ((unsigned short)i << 4) + (anim << 1);")),
              "    start = a[0];", "    end = (unsigned char)(a[1] + 1u);",
              "    if (act_fstart[i] != start || act_fend[i] != end) {",
              "        act_frame[i] = start;", "        act_fstart[i] = start;", "        act_fend[i] = end;",
              "    }", "}", "",
              *(["/* data_manager.c load_animations for the player: animations of a state. */",
                 "void ngc_player_load_animations(unsigned char set)", "{",
                 "    ngc_player_anim_set = set;",
                 ("    actor_load_anim_set(0u, set);" if actor_states is not None else
                  f"    act_player_anims = act_player_anim_sets + (set < {len(states) if states else 0}u ? act_player_anim_lookup[set] : 0u);"),
                 "}", ""] if states is not None else []),
              *(["/* vm_actor_set_anim_set: load, then actor_reset_anim. */",
                 "void ngc_actor_set_anim_set(unsigned char i, unsigned short set)", "{",
                 "    actor_load_anim_set(i, set);", "    set_anim(i, act_anim[i]);", "}", ""]
                if public_states else []),
              *(["/* vm_actor_get_pos: preserve ID, copy the two position words. */",
                 "void ngc_actor_get_pos(unsigned char i, unsigned short *p)", "{",
                 "    p[1] = act_x[i];", "    p[2] = act_y[i];", "}", ""]
                if read_positions else []),
              "void ngc_actor_set_dir(unsigned char i, unsigned char dir, unsigned char moving)", "{",
              "    act_dir[i] = dir;", "    set_anim(i, moving ? (unsigned char)(dir + 4u) : dir);", "}", "",
              "void ngc_actor_set_pos(unsigned char i, unsigned short x, unsigned short y)", "{",
              "    act_x[i] = x;", "    act_y[i] = y;", "}", "",
              "/* Tile16 test of actor.c (offset 64, 3 tile16 of bounds, screen 10x9). */",
              "static unsigned char offscreen(unsigned char i)", "{",
              "    unsigned char ax = (unsigned char)((unsigned char)(act_x[i] >> 9) + 67u);",
              "    unsigned char ay = (unsigned char)((unsigned char)(act_y[i] >> 9) + 67u);",
              "    unsigned char sx = (unsigned char)((unsigned char)(draw_x >> 4) + 64u);",
              "    unsigned char sy = (unsigned char)((unsigned char)(draw_y >> 4) + 64u);",
              "    return (unsigned char)(ax < sx || ax > (unsigned char)(sx + 16u) ||",
              "                           ay < sy || ay > (unsigned char)(sy + 15u));", "}", "",
              "static void activate(unsigned char i)", "{",
              "    if (act_flags[i] & 0x60u) return;                 /* ACTIVE | DISABLED */",
              "    if (!(act_flags[i] & 0x01u) && offscreen(i)) {",
              "        if (i == 0u || (act_flags[i] & 0x10u)) act_flags[i] |= 0x40u;",
              "        else return;", "    }",
              "    act_flags[i] |= 0x20u;", "    set_anim(i, act_dir[i]);",
              "    inact_head = dl_remove(inact_head, i);", "    act_head = dl_push(act_head, i);",
              *(["    act_update_handle[i] = 0x8000u;",
                 "    if (act_update_addr[i]) ngc_vm_execute(act_update_addr[i], &act_update_handle[i]);"] if updates else []),
              "}", "",
              "static void deactivate(unsigned char i)", "{",
              "    if (!(act_flags[i] & 0x20u) || i == 0u) return;",
              "    act_flags[i] &= (unsigned char)~0x20u;",
              "    act_head = dl_remove(act_head, i);", "    inact_head = dl_push(inact_head, i);",
              *(["    if (!(act_update_handle[i] & 0x8000u)) ngc_vm_terminate(act_update_handle[i]);"] if updates else []),
              "}", "",
              "/* What the VM actor instructions do (vm_actor.c, actor.c), called by gbvm.c. */",
              "void ngc_actor_activate(unsigned char i)", "{",
              "    /* vm_actor_activate: the player is only shown again */",
              "    if (i == 0u) { act_flags[0] &= (unsigned char)~0x02u; return; }",
              "    act_flags[i] &= (unsigned char)~0x40u;", "    activate(i);", "}", "",
              "void ngc_actor_deactivate(unsigned char i)", "{",
              "    /* vm_actor_deactivate: the player is only hidden; DISABLED keeps the",
              "     * actor from being activated again by the scrolling */",
              "    if (i == 0u) { act_flags[0] |= 0x02u; return; }",
              "    act_flags[i] |= 0x40u;", "    deactivate(i);", "}", "",
              "void ngc_actor_set_frame_offset(unsigned char i, unsigned char offset)", "{",
              "    /* actor_set_frame_offset: frame_start + offset % (frame_end - frame_start).",
              "     * No animation set yet (empty range): GBVM divides by zero; nothing here. */",
              "    unsigned char span = (unsigned char)(act_fend[i] - act_fstart[i]);",
              "    if (span == 0u) return;",
              "    while (offset >= span) offset = (unsigned char)(offset - span);",
              "    act_frame[i] = (unsigned char)(act_fstart[i] + offset);", "}", "",
              "void ngc_actor_set_flags(unsigned char i, unsigned char flags, unsigned char mask)", "{",
              "    /* vm_actor_set_flags */",
              "    act_flags[i] |= (unsigned char)(mask & flags);",
              "    act_flags[i] &= (unsigned char)~(mask & (unsigned char)~flags);", "}", "",
              "void ngc_actor_set_anim_tick(unsigned char i, unsigned char tick)", "{",
              "    act_tick[i] = tick;", "}", "",
              "void ngc_actor_set_move_speed(unsigned char i, unsigned char speed)", "{",
              "    act_speed[i] = speed;", "}", "",
              "static void activate_in_row(unsigned char x, unsigned char y)", "{",
              "    unsigned char i = inact_head;", "    unsigned char nx;", "    unsigned char tx;",
              "    unsigned char x_end = (unsigned char)(x + 23u);",
              f"    while (i != {NIL}u) {{", "        nx = act_next[i];",
              "        if ((unsigned char)(act_y[i] >> 8) == y) {",
              "            tx = (unsigned char)(act_x[i] >> 8);",
              "            if (tx >= x && tx < x_end) activate(i);", "        }", "        i = nx;", "    }", "}", "",
              "static void activate_in_col(unsigned char x, unsigned char y)", "{",
              "    unsigned char i = inact_head;", "    unsigned char nx;", "    unsigned char ty;",
              f"    unsigned char y_end = (unsigned char)(y + {refres_h}u);",
              f"    while (i != {NIL}u) {{", "        nx = act_next[i];",
              "        if ((unsigned char)(act_x[i] >> 8) == x) {",
              "            ty = (unsigned char)(act_y[i] >> 8);",
              "            if (ty >= y && ty < y_end) activate(i);", "        }", "        i = nx;", "    }", "}", "",
              "static void render_rows(unsigned short x, unsigned short y)", "{",
              "    short tx = (short)((unsigned char)(x >> 3)) - 1;",
              "    short ty = (short)((unsigned char)(y >> 3)) - 1;", "    unsigned char n;",
              "    unsigned char row;", "    if (tx < 0) tx = 0;", "    if (ty < 0) ty = 0;",
              "    row = (unsigned char)ty;",
              f"    for (n = 0u; n != {refres_h}u && row != {height_px >> 3}u; ++n, ++row)",
              "        activate_in_row((unsigned char)tx, row);", "}", "",
              "/* GBVM scroll_update: clamp camera - half screen, registers, activation. */",
              "void ngc_actors_scroll(short cx, short cy)", "{",
              "    short x = (short)(cx - 80);", f"    short y = (short)(cy - {view_h >> 1});",
              "    short cur_col;", "    short cur_row;", "    short new_col;", "    short new_row;",
              "    short c;", "    short full_y;",
              "    ngc_scroll_set(x, y);                     /* same clamp; committed next blank */",
              ("    x = (short)ngc_scroll_base_next[0];" if camera_shake else "    x = (short)ngc_scroll_next[0];"),
              ("    y = (short)ngc_scroll_base_next[1];" if camera_shake else "    y = (short)ngc_scroll_next[1];"),
              "    cur_col = (unsigned char)(gb_scroll_x >> 3);", "    cur_row = (unsigned char)(gb_scroll_y >> 3);",
              "    new_col = (unsigned char)((unsigned short)x >> 3);",
              "    new_row = (unsigned char)((unsigned short)y >> 3);",
              "    gb_scroll_x = (unsigned short)x;", "    gb_scroll_y = (unsigned short)y;",
              ("    draw_x = ngc_scroll_next[0];" if camera_shake else "    draw_x = (unsigned short)x;"),
              ("    draw_y = ngc_scroll_next[1];" if camera_shake else "    draw_y = (unsigned short)y;"),
              "    full_y = new_row > 0 ? (short)(new_row - 1) : 0;",
              "    if (cur_col == new_col - 1) activate_in_col((unsigned char)(new_col + 21), (unsigned char)full_y);",
              "    else if (cur_col == new_col + 1) {",
              "        c = new_col > 0 ? (short)(new_col - 1) : 0;",
              "        activate_in_col((unsigned char)c, (unsigned char)full_y);",
              "    } else if (cur_col != new_col) { render_rows(draw_x, draw_y); return; }",
              "    c = new_col > 0 ? (short)(new_col - 1) : 0;",
              f"    if (cur_row == new_row - 1) activate_in_row((unsigned char)c, (unsigned char)(new_row + {refres_h - 2}));",
              "    else if (cur_row == new_row + 1) activate_in_row((unsigned char)c, (unsigned char)full_y);",
              "    else if (cur_row != new_row) render_rows(draw_x, draw_y);", "}", "",
              "/* locked: VM_ISLOCKED() (a script holds VM_LOCK, e.g. a scene init thread",
              " * until it ends): an offscreen actor is then only DISABLED, not deactivated. */",
              "void ngc_actors_update(unsigned char locked)", "{",
              "    unsigned char i = act_tail;", "    unsigned char it = (unsigned char)ngc_game_time;",
              "    unsigned char flags;", "    unsigned char pv;",
              f"    while (i != {NIL}u) {{", "        flags = act_flags[i];",
              "        if (act_tick[i] != 255u && ((unsigned char)ngc_game_time & act_tick[i]) == 0u) {",
              "            if (++act_frame[i] == act_fend[i]) {",
              "                if (flags & 0x04u) --act_frame[i];", "                else act_frame[i] = act_fstart[i];",
              "            }", "        }",
              "        if (flags & 0x01u) { i = act_prev[i]; continue; }",
              "        if ((it++ & 3u) == 0u) {",
              "            if (offscreen(i)) {", "                pv = act_prev[i];",
              "                if (locked || i == 0u || (flags & 0x10u)) act_flags[i] |= 0x40u;",
              "                else { act_flags[i] &= (unsigned char)~0x40u; deactivate(i); }",
              "                i = pv;", "                continue;", "            }",
              "            act_flags[i] &= (unsigned char)~0x40u;", "        }",
              "        i = act_prev[i];", "    }", "}", "",
              "/* actors_render: player first, then the prev chain; hidden or disabled skipped. */",
              "void ngc_actors_render(void)", "{", "    unsigned char i = 0u;", "    short sx;", "    short sy;",
              *(["    unsigned char ei;"] if emotes else []),
              "    ngc_sprites_begin();",
              *(["    if (ngc_emote_actor != 255u) {", "        ei = ngc_emote_actor;",
                 "        sx = (short)(act_x[ei] >> 5) - (short)gb_scroll_x;",
                 "        sy = (short)(act_y[ei] >> 5) - (short)gb_scroll_y;",
                 ("        sy += act_emote_origin[act_sheet[ei]];" if sprite_switches else
                  "        sy += act_emote_origin[ei];"),
                 "        if (ngc_emote_timer < 15u) sy += act_emote_offsets[ngc_emote_timer];",
                 "        ngc_sprite_draw(act_emote_frame, sx, sy);", "    }"] if emotes else []),
              f"    while (i != {NIL}u) {{",
              "        if (!(act_flags[i] & 0x42u)) {",
              "            sx = (short)(act_x[i] >> 5);", "            sy = (short)(act_y[i] >> 5);",
              "            if (!(act_flags[i] & 0x01u)) { sx -= (short)draw_x; sy -= (short)draw_y; }",
              "            ngc_sprite_draw(act_spr_order[act_order_base[i] + act_frame[i]], sx, sy);",
              "        }", "        i = act_prev[i];", "    }", "    ngc_sprites_end();", "}", "",
              "/* GBVM camera.c: camera_init state (LOCK X|Y, offsets and clamps 0,",
              " * camera_reset: deadzone 0); camera_update follows the player. 1/32 px. */",
              "unsigned short ngc_camera_x;", "unsigned short ngc_camera_y;",
              "static unsigned short cam_clamp_x;", "static unsigned short cam_clamp_y;",
              "static signed char cam_offset_x;", "static signed char cam_offset_y;",
              "static signed char cam_deadzone_x;", "static signed char cam_deadzone_y;",
              "static unsigned char cam_settings;", "",
              "void ngc_camera_update(void)", "{",
              "    unsigned short target;", "    short tolerance;", "    unsigned short lo;",
              "    unsigned short hi;", "    unsigned short cam;",
              "    if (cam_settings & 0x01u) {",
              "        target = (unsigned short)(act_x[0] + 256u - ((unsigned short)(short)cam_offset_x << 5));",
              "        tolerance = (short)((short)cam_deadzone_x << 5);",
              "        lo = ((unsigned short)tolerance > target) ? 0u : (unsigned short)(target - tolerance);",
              "        hi = (unsigned short)(target + tolerance);", "        cam = ngc_camera_x;",
              "        if (cam < lo) { cam = lo; if ((cam_settings & 0x08u) && cam > cam_clamp_x) cam = cam_clamp_x; }",
              "        else if (cam > hi) { cam = hi; if ((cam_settings & 0x04u) && cam < cam_clamp_x) cam = cam_clamp_x; }",
              "        ngc_camera_x = cam;", "        cam_clamp_x = cam;", "    }",
              "    if (cam_settings & 0x02u) {",
              "        target = (unsigned short)(act_y[0] + 256u - ((unsigned short)(short)cam_offset_y << 5));",
              "        tolerance = (short)((short)cam_deadzone_y << 5);",
              "        lo = ((unsigned short)tolerance > target) ? 0u : (unsigned short)(target - tolerance);",
              "        hi = (unsigned short)(target + tolerance);", "        cam = ngc_camera_y;",
              "        if (cam < lo) { cam = lo; if ((cam_settings & 0x20u) && cam > cam_clamp_y) cam = cam_clamp_y; }",
              "        else if (cam > hi) { cam = hi; if ((cam_settings & 0x10u) && cam < cam_clamp_y) cam = cam_clamp_y; }",
              "        ngc_camera_y = cam;", "        cam_clamp_y = cam;", "    }", "}", "",
              "/* load_scene (init_data): scene actors, player activated, others inactive;",
              " * then player_init. Camera reset as camera_init + camera_reset. */",
              "void ngc_actors_load(void)", "{", "    unsigned char i;",
              *(["    unsigned char k;"] if actor_states is not None or mutable_bounds else []),
              *([*([] if actor_states is not None else ["    act_player_anims = act_player_anim_sets;   /* load_scene: default set */"]),
                 "    ngc_player_anim_set = 0u;"] if states is not None else []),
              *(["    ngc_emote_actor = 255u;"] if emotes else []),
              f"    for (i = 0u; i < {n}u; ++i) {{",
              "        act_x[i] = act_init_x[i];", "        act_y[i] = act_init_y[i];",
              "        act_dir[i] = act_init_dir[i];", "        act_flags[i] = act_init_flags[i];",
              "        act_tick[i] = act_init_tick[i];", "        act_speed[i] = act_init_speed[i];",
              *(["        act_update_handle[i] = 0x8000u;"] if updates else []),
              *(["        for (k = 0u; k < 16u; ++k)",
                 "            act_state_anims[((unsigned short)i << 4) + k] = act_anims[((unsigned short)i << 4) + k];"]
                if actor_states is not None else []),
              *(["        act_order_base[i] = act_init_order_base[i];",
                 "        act_sheet[i] = act_init_sheet[i];"] if sprite_switches else []),
              *(["        for (k = 0u; k < 4u; ++k)",
                 "            act_bounds[((unsigned short)i << 2) + k] = act_init_bounds[((unsigned short)i << 2) + k];"]
                if mutable_bounds else []),
              "        act_frame[i] = 0u;",
              "        act_fstart[i] = 0u;", "        act_fend[i] = 0u;", "        act_anim[i] = 0u;",
              f"        act_next[i] = {NIL}u;", f"        act_prev[i] = {NIL}u;", "    }",
              f"    act_head = {NIL}u;", f"    inact_head = {NIL}u;", "    act_tail = 0u;",
              "    draw_x = 0u;", "    draw_y = 0u;", "    ngc_game_time = 0u;",
              "    ngc_camera_x = 0u;", "    ngc_camera_y = 0u;", "    cam_clamp_x = 0u;", "    cam_clamp_y = 0u;",
              "    cam_offset_x = 0;", "    cam_offset_y = 0;", "    cam_deadzone_x = 0;", "    cam_deadzone_y = 0;",
              "    cam_settings = 0x03u;",
              "    inact_head = dl_push(inact_head, 0u);", "    activate(0u);",
              f"    for (i = 1u; i < {n}u; ++i) inact_head = dl_push(inact_head, i);",
              "    /* player_init: clear HIDDEN, DISABLED, ANIM_NOLOOP, PINNED; set COLLISION */",
              "    act_flags[0] = (unsigned char)((act_flags[0] & (unsigned char)~0x47u) | 0x08u);",
              "    set_anim(0u, act_dir[0]);", "}", "",
              "/* core.c after camera_update: scroll_repaint, activate_persistent_actors,",
              " * actors_update, actors_render. */",
              "void ngc_actors_repaint(short cx, short cy)", "{", "    unsigned char i;",
              "    gb_scroll_x = 0x7FFFu;", "    gb_scroll_y = 0x7FFFu;",
              "    ngc_actors_scroll(cx, cy);",
              "    i = inact_head;",
              f"    while (i != {NIL}u) {{", "        unsigned char nx = act_next[i];",
              "        if (act_flags[i] & 0x11u) activate(i);", "        i = nx;", "    }",
              "    ngc_actors_update(0u);             /* no script has run yet: not locked */",
              "    ngc_actors_render();", "}", "",
              *(["/* EXCEPTION_LOAD (docs/M5_SAVE.md): data_manager.c load_scene without",
                 " * init_data keeps the restored actors and sets the active ones idle",
                 " * (actor_set_anim_idle), emote_actor = NULL, scroll_reset (game_time = 0);",
                 " * core.c then runs player_init. No sprite of this engine reserves tiles. */",
                 "void ngc_actors_resume(void)", "{", "    unsigned char i;",
                 *(["    ngc_emote_actor = 255u;"] if emotes else []),
                 f"    for (i = act_head; i != {NIL}u; i = act_next[i]) set_anim(i, act_dir[i]);",
                 "    ngc_game_time = 0u;",
                 "    /* player_init: idle, clear HIDDEN, DISABLED, ANIM_NOLOOP, PINNED; set COLLISION */",
                 "    set_anim(0u, act_dir[0]);",
                 "    act_flags[0] = (unsigned char)((act_flags[0] & (unsigned char)~0x47u) | 0x08u);", "}", ""]
                if resume else []),
              "/* Scripted proofs: the camera is given, not computed from the player. */",
              "void ngc_actors_start(short cx, short cy)", "{",
              "    ngc_actors_load();", "    ngc_actors_repaint(cx, cy);", "}", ""]
    if read_directions:
        lines += ["unsigned char ngc_actor_get_dir(unsigned char i)", "{", "    return act_dir[i];", "}", ""]
    if moves is not None:               # None: no script moves an actor; () moves without options
        unknown = set(moves) - set(MOVE_FEATURES)
        if unknown or len(bounds) != n or ("walls" in moves and len(collisions) != (width_px >> 3) * (height_px >> 3)):
            raise CompileError(f"{where}: movement data inconsistent ({sorted(unknown)}, {len(bounds)} boxes)")
        lines += _moves_source(n, moves, bounds, collisions, width_px >> 3, height_px >> 3, cancel, topdown is not None, push, split_moves,
                               state_walls=topdown is not None and getattr(topdown, "state", "topdown") != "pointnclick")
        if push:
            lines += _push_source(cancel)
    elif topdown is not None:
        lines += _collision_source(bounds, collisions, width_px >> 3, height_px >> 3,
                                   getattr(topdown, "state", "topdown") != "pointnclick", True)
    if mutable_bounds:
        # Collision generators keep their unchanged static tables in ordinary
        # scenes; remove that declaration when the mutable table already exists.
        immutable_bounds = []
        c_array(immutable_bounds, "act_bounds", [v for box in bounds for v in box], "short")
        for at in range(len(lines)):
            if lines[at:at + len(immutable_bounds)] == immutable_bounds:
                del lines[at:at + len(immutable_bounds)]
                break
    if set_bounds:
        lines += ["/* GBVM vm_actor.c vm_actor_set_bounds: left, right, top, bottom (1/32 px). */",
                  "void ngc_actor_set_bounds(unsigned char i, short l, short r, short t, short b)", "{",
                  "    short *box;", "    box = act_bounds + ((unsigned short)i << 2);",
                  "    box[0] = l;", "    box[1] = r;", "    box[2] = t;", "    box[3] = b;", "}", ""]
    if sprite_switches:
        lines += ["/* vm_actor_set_spritesheet: resident tiles, default animations, bounds, reset. */",
                  "void ngc_actor_set_spritesheet(unsigned char i, unsigned short sheet)", "{",
                  "    const unsigned char *src;", "    const short *box;",
                  "    unsigned char *dst;", "    short *bounds_dst;", "    unsigned char k;",
                  "    switch (sheet) {"]
        for index, s, box in sprite_switches:
            lines += [f"    case {index}u:", f"        act_sheet[i] = {s}u;",
                      f"        act_order_base[i] = {sheet_order_base[s]}u;",
                      f"        src = act_state_sets_{s}; box = act_sheet_bounds_{s}; break;"]
        lines += ["    default: return;", "    }",
                  "    dst = act_state_anims + ((unsigned short)i << 4);",
                  "    for (k = 16u; k != 0u; --k) *dst++ = *src++;",
                  "    bounds_dst = act_bounds + ((unsigned short)i << 2);",
                  "    for (k = 4u; k != 0u; --k) *bounds_dst++ = *box++;",
                  "    set_anim(i, act_anim[i]);", "}", ""]
    if stop_update:
        lines += ["/* GBVM vm_actor.c vm_actor_terminate_update: keep actor active. */",
                  "void ngc_actor_stop_update(unsigned char i)", "{",
                  *(["    if (!(act_update_handle[i] & 0x8000u)) ngc_vm_terminate(act_update_handle[i]);"]
                    if updates else ["    (void)i;  /* This scene has no actor update scripts. */"]),
                  "}", ""]
    if start_update:
        lines += ["/* GBVM vm_actor.c vm_actor_begin_update: restart a terminated update script. */",
                  "void ngc_actor_start_update(unsigned char i)", "{",
                  *(["    if (act_update_addr[i] && (act_update_handle[i] & 0x8000u))",
                     "        ngc_vm_execute(act_update_addr[i], &act_update_handle[i]);"]
                    if updates else ["    (void)i;  /* This scene has no actor update scripts. */"]),
                  "}", ""]
    if cancel:
        lines += _cancel_source()
    if topdown is not None:
        from .scene_topdown import topdown_source
        if len(bounds) != n or len(collisions) != (width_px >> 3) * (height_px >> 3):
            raise CompileError(f"{where}: topdown collision data inconsistent")
        if len(topdown) > 3:            # vendored state: (None, None, option, source(width, height, ...))
            lines += topdown[3](width_px >> 3, height_px >> 3, interactions, triggers, hits, view_h=view_h)
        else:
            lines += topdown_source(n, topdown[0], topdown[1], interactions, triggers, hits)
    return "\n".join(lines) + "\n"


def export_actors_rom(background, sheets, palettes, actors, steps, destination, max_delta=2, loop=False):
    """Diagnostic: GBVM actor bookkeeping driven by a scripted table.
    steps: [(camera x px, camera y px, [(x px, y px, dir, moving) per actor]), ...];
    step 0 is the scene's initial state. Each later frame, like core.c: the step's
    positions and directions are applied (stand-in for scripts), then
    scroll_update (+ activation), actors_update, actors_render, streaming,
    game_time++."""
    from .background_rom import write_export
    from .background_stream import stream_source
    from .scene_sprites import sprites_source
    if not steps or any(len(s[2]) != len(actors) for s in steps):
        raise CompileError("actors ROM: every step lists every actor")
    table = []
    for cx, cy, entries in steps:
        table += [cx & 0xFFFF, cy & 0xFFFF]
        for x, y, direction, moving in entries:
            table += [x & 0xFFFF, y & 0xFFFF, direction | (0x100 if moving else 0)]
    row, n = 2 + 3 * len(actors), len(actors)
    game = ['#include "game.h"', "void ngc_stream_load(short x, short y);", "void ngc_stream_update(void);",
            "void ngc_scroll_commit(void);", "void ngc_sprites_commit(void);",
            "void ngc_sprites_load(void);", "void ngc_actors_start(short cx, short cy);",
            "void ngc_actors_scroll(short cx, short cy);", "void ngc_actors_update(unsigned char locked);",
            "void ngc_actors_render(void);", "extern unsigned short ngc_game_time;",
            "void ngc_actor_set_pos(unsigned char i, unsigned short x, unsigned short y);",
            "void ngc_actor_set_dir(unsigned char i, unsigned char dir, unsigned char moving);"]
    c_array(game, "ngc_steps", table)
    game += ["static unsigned short ngc_step;", "",
             "void ngc_game_init(void)", "{", "    ngc_step = 0u;", "    ngc_sprites_load();",
             "    ngc_stream_load((short)ngc_steps[0] - 80, (short)ngc_steps[1] - 76);",
             "    ngc_actors_start((short)ngc_steps[0], (short)ngc_steps[1]);",
             "    ngc_scroll_commit();", "    ngc_sprites_commit();", "}",
             "/* Right after the VBlank interrupt: commit what the last frame computed",
             " * (scroll, shadow OAM, plane window), then the frame logic in the order of",
             " * GBVM core.c, drawn into the shadow OAM for the next commit. */",
             "void ngc_game_tick(void)", "{", "    const unsigned short *s;", "    unsigned char i;",
             "    ngc_scroll_commit();", "    ngc_sprites_commit();", "    ngc_stream_update();",
             (f"    if (++ngc_step == {len(steps)}u) ngc_step = 0u;" if loop else
              f"    if (ngc_step < {len(steps) - 1}u) ++ngc_step;"),
             f"    s = ngc_steps + ngc_step * {row}u + 2u;",
             f"    for (i = 0u; i < {n}u; ++i, s += 3) {{",
             "        ngc_actor_set_pos(i, (unsigned short)(s[0] << 5), (unsigned short)(s[1] << 5));",
             "        ngc_actor_set_dir(i, (unsigned char)s[2], (unsigned char)(s[2] >> 8));", "    }",
             f"    s = ngc_steps + ngc_step * {row}u;",
             "    ngc_actors_scroll((short)s[0], (short)s[1]);", "    ngc_actors_update(0u);",
             "    ngc_actors_render();", "    ++ngc_game_time;", "}"]
    init = [dict(a, x=x << 5, y=y << 5, dir=d) for a, (x, y, d, _) in zip(actors, steps[0][2])]
    files = {"src/game.h": "void ngc_game_init(void);\nvoid ngc_game_tick(void);\n",
             "src/game.c": "\n".join(game) + "\n",
             "src/scene_stream.c": stream_source(background, max_delta),
             "src/scene_sprites.c": sprites_source(sheets, background.tile_base + len(background.tiles), palettes),
             "src/scene_actors.c": actors_source(init, sheets, background.height)}
    report = {"background": background.report(), "actors": len(actors),
              "sheets": [s.name for s in sheets], "path_frames": len(steps), "path_loops": loop}
    return write_export(["streamed_background", "sprites", "actors"], files,
                        "GBVM actor bookkeeping proof over a streamed background, scripted table",
                        report, destination, "actors ROM")
