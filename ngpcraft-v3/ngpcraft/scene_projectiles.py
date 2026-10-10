"""Selected projectile pool attached to the real scene actor object.

Keeps the existing frame entry points, including modal UI and 30 fps calls.
Only launch is public: scene_link dispatches it, all tick work stays local.
Author-event selection and callback capacity remain the exporter's responsibility.
"""
import re
from .background_rom import c_array
from .compiler import CompileError
from .projectile_runtime import projectile_source, projectile_collision_source


def _once(text, old, new, where):
    if text.count(old) != 1:
        raise CompileError(f"{where}: actor integration anchor changed: {old!r}")
    return text.replace(old, new, 1)


def attach_projectiles(source, compiled, *, capacity, bounds, groups, view_h,
                       scripts=None, modal=False, resume=False, where="scene projectiles"):
    """scripts: actor index -> (bank byte, address word); zero address is valid.

    Caller must terminate the previous scene's contexts before actors_load.
    Existing actor bounds (including mutable sprite-switch bounds) are reused.
    """
    if not compiled.definitions:
        if scripts:
            raise CompileError(f"{where}: hit bindings without reachable projectiles")
        return source
    if (not isinstance(bounds, (tuple, list)) or not isinstance(groups, (tuple, list)) or
            len(bounds) != len(groups)):
        raise CompileError(f"{where}: actor bounds/groups count mismatch")
    for i, box in enumerate(bounds):
        if not isinstance(box, (tuple, list)) or len(box) != 4 or any(type(v) is not int or not -32768 <= v <= 32767 for v in box):
            raise CompileError(f"{where}: invalid actor bounds[{i}]")
        if box[0] > box[1] or box[2] > box[3]:
            raise CompileError(f"{where}: inverted actor bounds[{i}]")
    if view_h not in (144, 152):
        raise CompileError(f"{where}: unsupported viewport height {view_h!r}")
    scripts = {} if scripts is None else scripts
    if not isinstance(scripts, dict):
        raise CompileError(f"{where}: hit bindings must be an actor-index mapping")
    for i, binding in scripts.items():
        if (type(i) is not int or not 0 <= i < len(groups) or
                not isinstance(binding, (tuple, list)) or len(binding) != 2 or
                type(binding[0]) is not int or not 0 <= binding[0] <= 255 or
                type(binding[1]) is not int or not 0 <= binding[1] <= 65535):
            raise CompileError(f"{where}: invalid hit binding for actor {i!r}")
    pool = projectile_source(compiled.definitions, compiled.frames, capacity=capacity,
        width_tiles=20, height_tiles=view_h >> 3, definition_ids=compiled.definition_indices, where=where,
        collision_spread=compiled.collision_spread)
    bridge = projectile_collision_source(groups, where)
    # Only the VM launch adapter crosses the scene object's boundary.
    tail = "\n".join(pool + bridge)
    if "static const signed char state_sine[256]" in source:
        # Both kernels use the pinned GBVM sincos.h table in this scene object.
        # Keep one ROM table when a genre scene also launches projectiles.
        table = []
        from .scene_genres import _sine
        values = [int(v) for v in _sine().strip("{}").split(",")]
        c_array(table, "proj_sine", values, "signed char")
        declaration = "\n".join(table)
        tail = _once(tail, declaration, "", where).replace("proj_sine[", "state_sine[")
    # scene_link's C scanner uses one physical line per signature.
    tail = re.sub(r"^(?:unsigned char|void) ngc_projectile\w+\([^)]*\)",
        lambda m: re.sub(r"\s*\n\s*", " ", m[0]), tail, flags=re.M)
    tail = tail.replace("ngc_projectile_hit(const short *bounds,", "ngc_projectile_hit(const short *b,", 1)
    tail = re.sub(r"^(unsigned char|void) (ngc_projectile\w+)\(",
        lambda m: m[0] if m[2] in ("ngc_projectile_launch", "ngc_projectile_load_slot") else "static " + m[0],
        tail, flags=re.M)
    tail = tail.replace("unsigned char proj_head;", "static unsigned char proj_head;", 1)
    declarations = ["static void ngc_projectiles_init(void);",
        "static void ngc_projectiles_update(unsigned short time, short scroll_x, short scroll_y);",
        "static void ngc_projectiles_render(short scroll_x, short scroll_y);",
        "void ngc_actors_update(unsigned char locked);"]
    if not re.search(r"^static (?:const )?short act_bounds\[", source, re.M):
        c_array(declarations, "act_bounds", [v for box in bounds for v in box], "short")
    if scripts:
        declarations += ["void ngc_vm_projectile_reset(void);",
            "void ngc_vm_projectile_activate(unsigned char actor);",
            "void ngc_vm_projectile_detach(unsigned char actor);",
            "void ngc_vm_projectile_hit(unsigned char actor, unsigned char present, unsigned char bank, unsigned short addr, unsigned short group);"]
        for name, values, ctype in (
            ("proj_hit_present", [int(i in scripts) for i in range(len(groups))], "unsigned char"),
            ("proj_hit_bank", [scripts[i][0] if i in scripts else 0 for i in range(len(groups))], "unsigned char"),
            ("proj_hit_addr", [scripts[i][1] if i in scripts else 0 for i in range(len(groups))], "unsigned short")):
            c_array(declarations, name, values, ctype)
        source = _once(source, "    act_flags[i] |= 0x20u;", "    act_flags[i] |= 0x20u;\n    ngc_vm_projectile_activate(i);", where)
        source = _once(source, "    act_flags[i] &= (unsigned char)~0x20u;",
            "    ngc_vm_projectile_detach(i);\n    act_flags[i] &= (unsigned char)~0x20u;", where)
    source = _once(source, "void ngc_actors_update(unsigned char locked)\n{",
        "static void ngc_actors_update_base(unsigned char locked)\n{", where)
    # Retain C89 declarations: initialise immediately before the actor loop.
    source = _once(source, f"    for (i = 0u; i < {len(groups)}u; ++i) {{",
        "    ngc_projectiles_init();\n" + ("    ngc_vm_projectile_reset();\n" if scripts else "") +
        f"    for (i = 0u; i < {len(groups)}u; ++i) {{", where)
    if resume:
        # load_scene(FALSE) also runs projectiles_init; the actors' hit threads
        # (hscript_hit) are restored with the VM, not reset.
        source = _once(source, "void ngc_actors_resume(void)\n{\n    unsigned char i;\n",
            "void ngc_actors_resume(void)\n{\n    unsigned char i;\n    ngc_projectiles_init();\n", where)
    source = _once(source, "    ngc_sprites_end();",
        "    ngc_projectiles_render((short)draw_x, (short)draw_y);\n    ngc_sprites_end();", where)
    tail += "\nstatic void ngc_projectile_script(unsigned char actor, unsigned char group)\n{\n"
    tail += ("    ngc_vm_projectile_hit(actor, proj_hit_present[actor], proj_hit_bank[actor], proj_hit_addr[actor], (unsigned short)group);\n"
             if scripts else "    (void)actor; (void)group;\n")
    tail += "}\n\nvoid ngc_actors_update(unsigned char locked)\n{\n"
    tail += "    ngc_actors_update_base(locked);\n    ngc_projectiles_update(ngc_game_time, (short)draw_x, (short)draw_y);\n}\n"
    if modal:
        # ui.c ui_run_modal / ui_run_menu: actors_update, actors_render,
        # projectiles_render; no projectiles_update (core.c only).
        tail += "\nvoid ngc_actors_update_modal(unsigned char locked)\n{\n    ngc_actors_update_base(locked);\n}\n"
    return "\n".join(declarations) + "\n" + source + "\n" + tail
