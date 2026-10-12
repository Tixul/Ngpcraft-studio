"""The scene engine of an exported GB Studio project: streamed background,
sprites and actors around the GBVM scripts, in the order of GBVM core.c.

Start (core.c after load_scene / load_player): load the actors, camera_update,
scroll_repaint (+ activation), activate_persistent_actors, actors_update,
actors_render; then the scene's init thread starts.
Each frame, first in the vertical blank what the last frame computed (scroll
registers, shadow OAM, plane window), then: the scripts until every thread
waits (process_VM), camera_update, scroll_update (+ activation),
actors_update, actors_render, game_time++.
"""
from .scene_actors import actors_source
from .background_stream import stream_source, view_of
from .compiler import CompileError
from .scene_sprites import PRIORITY_BEHIND, PRIORITY_BETWEEN, PRIORITY_FRONT, sprite_tile_count, sprites_source
from .scene_planes import BACK_PLANE, FRONT_PLANE, plane_report

FEATURES = ["streamed_background", "sprites", "actors"]

def declarations():
    """game.c's view of the scene engine; the camera at the export's position width (world.py)."""
    from .world import pos_t
    return [
    "void ngc_stream_load(short x, short y);", "void ngc_stream_update(void);",
    "void ngc_scroll_commit(void);", "void ngc_sprites_load(void);", "void ngc_sprites_commit(void);",
    "void ngc_actors_load(void);", "void ngc_actors_repaint(short cx, short cy);",
    "void ngc_actors_scroll(short cx, short cy);", "void ngc_actors_update(unsigned char locked);", "unsigned char ngc_vm_locked(void);",
    "void ngc_actors_render(void);", "void ngc_camera_update(void);",
    f"extern {pos_t()} ngc_camera_x;", f"extern {pos_t()} ngc_camera_y;",
    "extern unsigned short ngc_game_time;",
]

def start_lines(view_h, topdown=False):
    """topdown: the scene type state, "topdown" or "platform" (True = "topdown")."""
    state = "topdown" if topdown is True else topdown
    return [
    "    ngc_sprites_load();", "    ngc_actors_load();",
    *(["    ngc_input_init();", f"    ngc_{state}_init();"] if topdown else []), "    ngc_camera_update();",
    f"    ngc_stream_load((short)(ngc_camera_x >> 5) - 80, (short)(ngc_camera_y >> 5) - {view_h >> 1});",
    "    ngc_actors_repaint((short)(ngc_camera_x >> 5), (short)(ngc_camera_y >> 5));",
    "    ngc_scroll_commit();", "    ngc_sprites_commit();",
    ]

COMMIT = ["    ngc_scroll_commit();", "    ngc_sprites_commit();", "    ngc_stream_update();"]

AFTER_SCRIPTS = [
    "    ngc_camera_update();",
    "    ngc_actors_scroll((short)(ngc_camera_x >> 5), (short)(ngc_camera_y >> 5));",
    "    ngc_actors_update(ngc_vm_locked());", "    ngc_actors_render();", "    ++ngc_game_time;",
]


def engine_files(scene, moves=None, updates=None, cancel=False, topdown=None, interactions=None, triggers=None, hits=None,
                 shadow=False, text=False, track_front=False, stop_update=False, start_update=False, actor_states=None, read_positions=False, camera_shake=False, read_directions=False, push=False, split_moves=True, sprite_states=None, projectile_groups=None, projectile_scripts=None, projectile_modal=False, resume=False, camera_control=False, set_bounds=False, camera_properties=False, scroll_bounds=False,
                 parallax_export=False, patch=False):
    """scene: scene_build.EngineScene -> {path: C source}, report.
    patch: recycled palettes go through the GBVM runtime's ngc_palette_patch
    (fade shadow and fades; emit.palette_patch_source).
    moves: None, or the movement features the scripts use (scene_actors.MOVE_FEATURES)."""
    bg = scene.background
    render_sheets = scene.sheets + scene.emote_sheets
    view_h, top = view_of(bg)
    second = scene.second_background
    shared_text = bool(text and (second is not None or bg.complement_cells or bg.priority_cells))
    from .sprite_behind import behind_tiles, check_behind_exact
    behind = behind_tiles(render_sheets)
    behind_report = check_behind_exact(scene.background_source, bg, f"scene {scene.name}") if behind else None
    if second is not None and scene.parallax:
        raise CompileError(f"scene {scene.name}: GB Studio parallax and the NGPC second background both need the "
                           "second plane: keep one (the second background already scrolls at its own speed; "
                           "docs/M6_PARALLAX.md)")
    if second is not None:
        from .second_stream import second_stream_source
        streaming = second_stream_source(second[0], scroll_x=second[1], scroll_y=second[2],
                                         shadow=shadow, reserve_ui=shared_text,
                                         camera_shake=camera_shake, scroll_bounds=scroll_bounds,
                                         behind=second[3])
    else:
        streaming = stream_source(bg, shadow=shadow, front_plane=FRONT_PLANE,
                                  reserve_ui=shared_text, track_front=track_front, camera_shake=camera_shake,
                                  scroll_bounds=scroll_bounds, parallax=scene.parallax,
                                  parallax_export=parallax_export, where=f"scene {scene.name}", patch=patch)
    files = {"src/scene_stream.c": streaming,
             "src/scene_sprites.c": sprites_source(render_sheets, bg.tile_base + len(bg.tiles), scene.palettes,
                                                   where=f"scene {scene.name}", top=top, shadow=shadow,
                                                   priority=PRIORITY_BETWEEN if bg.priority_cells or (text and not shared_text) else PRIORITY_FRONT,
                                                   shared_text=shared_text,
                                                   # second background behind: a GB priority sprite goes
                                                   # between the planes, behind the main one only.
                                                   behind_level=PRIORITY_BETWEEN if second is not None and second[3]
                                                   else PRIORITY_BEHIND, recycle=scene.sprite_recycle, patch=patch),
             "src/scene_actors.c": actors_source(list(scene.actors), scene.sheets, bg.height, view_h=view_h,
                                                 moves=moves, bounds=scene.bounds, collisions=scene.collisions,
                                                 width_px=bg.width, updates=updates, cancel=cancel,
                                                 topdown=topdown, interactions=interactions, triggers=triggers, hits=hits,
                                                 stop_update=stop_update, start_update=start_update, actor_states=actor_states, read_positions=read_positions, camera_shake=camera_shake,
                                                 read_directions=read_directions, push=push, split_moves=split_moves,
                                                 sprite_switches=scene.sprite_switches, sprite_states=sprite_states,
                                                 emotes=scene.emotes, emote_origins=scene.emote_origins, set_bounds=set_bounds,
                                                 resume=resume)}
    if camera_control:
        import re
        from .world import pos_t
        source = files["src/scene_actors.c"]
        for old, new, ctype in (("cam_settings", "ngc_camera_settings", "unsigned char"),
                               ("cam_clamp_x", "ngc_camera_clamp_x", pos_t()),
                               ("cam_clamp_y", "ngc_camera_clamp_y", pos_t())):
            declaration = f"static {ctype} {old};"
            if source.count(declaration) != 1:
                raise CompileError(f"camera state declaration changed: {old}")
            source = re.sub(rf"\b{old}\b", new, source.replace(declaration, f"{ctype} {old};"))
        files["src/scene_actors.c"] = source
    if camera_properties:
        # Camera Property Set (VM memory writes): camera.c's names, public.
        # GBVM resets the deadzones at each scene load (camera_reset) and the
        # offsets at boot only (camera_init): game.c resets those.
        import re
        source = files["src/scene_actors.c"]
        reset = "    cam_offset_x = 0;\n    cam_offset_y = 0;\n"
        if source.count(reset) != 1:
            raise CompileError("camera offset reset changed")
        source = source.replace(reset, "")
        for old, new in (("cam_deadzone_x", "camera_deadzone_x"), ("cam_deadzone_y", "camera_deadzone_y"),
                         ("cam_offset_x", "camera_offset_x"), ("cam_offset_y", "camera_offset_y")):
            declaration = f"static signed char {old};"
            if source.count(declaration) != 1:
                raise CompileError(f"camera state declaration changed: {old}")
            source = re.sub(rf"\b{old}\b", new, source.replace(declaration, f"signed char {old};"))
        files["src/scene_actors.c"] = source
    if scene.projectiles is not None and scene.projectiles.definitions:
        from .scene_projectiles import attach_projectiles
        if projectile_groups is None:
            raise CompileError(f"scene {scene.name}: projectile actor groups must be resolved explicitly")
        files["src/scene_actors.c"] = attach_projectiles(files["src/scene_actors.c"], scene.projectiles,
            capacity=scene.projectile_capacity, bounds=scene.bounds, groups=projectile_groups,
            view_h=view_h, scripts=projectile_scripts, modal=projectile_modal, resume=resume,
            where=f"scene {scene.name}: projectiles")
    elif projectile_scripts:
        raise CompileError(f"scene {scene.name}: projectile hit bindings without reachable projectiles")
    elif projectile_modal:
        # Another scene of the export has projectiles: same modal entry point.
        files["src/scene_actors.c"] += ("\nvoid ngc_actors_update_modal(unsigned char locked)\n{\n"
                                        "    ngc_actors_update(locked);\n}\n")
    dynamic = None
    if (bg.complement_cells and scene.background_source is not None and
            scene.background_source.priority_report()["priority_opaque_pixels"]):
        from .sprite_priority import masked_sprites_source, frame_mask_cache_layout, priority_mask_plan
        if scene.sprite_recycle:
            raise CompileError(f"scene {scene.name}: recycled sprite palettes with the GB sprite priority mask "
                               "are not delivered (docs/PALETTE_RECYCLE.md)")
        slots = min(64, sum(max(scene.sheets[s].max_sprites() for s in
                               {a["sheet"], *(entry[1] for entry in scene.sprite_switches)}) for a in scene.actors)
                    + (4 if scene.emotes else 0)
                    + (scene.projectile_capacity * max(scene.sheets[scene.sheet_ids.index(s)].max_sprites()
                        for s in scene.projectiles.sheet_ids) if scene.projectiles is not None and scene.projectiles.definitions else 0))
        cache_base = bg.tile_base + len(bg.tiles) + sprite_tile_count(render_sheets)
        try:
            files["src/scene_sprites.c"] = masked_sprites_source(files["src/scene_sprites.c"], render_sheets,
                scene.background_source, cache_base, slots, top=top)
        except CompileError as exc:
            raise CompileError(f"scene {scene.name}: {exc}") from exc
        dynamic = {"cache_tile_base": cache_base, "cache_tiles": slots,
                   "shadow_bytes": slots * 16, "cache_metadata_bytes": slots * 8, "cache_control_bytes": 1, "row_transfer_bytes": 11}
        dynamic.update(frame_mask_cache_layout(render_sheets, slots, shared_text))
        dynamic["mask_rom"] = priority_mask_plan(scene.background_source)
        if dynamic["mask_rom"]["strategy"] == "preexpanded_rows":
            dynamic["row_transfer_bytes"] = 10
    if topdown is not None:
        from .scene_topdown import input_source
        files["src/scene_input.c"] = input_source(topdown[2])
    if scene.replace_plan is not None:
        from .replace_tiles import replace_source
        files["src/scene_stream.c"] += "\n" + replace_source(bg, scene.replace_plan)
    report = {"scene": scene.name, "background": bg.report(), "planes": plane_report(bg, text=text, second=second is not None),
              "sheets": [s.name for s in scene.sheets],
              "sprite_tiles": sprite_tile_count(render_sheets),
              "actors": list(scene.names), "view": {"height": view_h, "top_band": top},
              "warnings": ([f"scene {scene.name}: background {bg.width}x{bg.height} px shown with black "
                            f"bands of {top} px above and below; replace or adapt it to 152 px "
                            "to use the whole NGPC screen (ECARTS 1)"] if top else [])}
    if scene.logo:
        report["logo"] = {"player": "spritesheet_none", "player_tiles": 0,
                          "state_update": "empty (GBVM logo.c)", "actors_supported": False,
                          "physical_console_validated": False}
    if scene.background_source is not None:
        report["background_priority"] = scene.background_source.priority_report()
        if dynamic is not None:
            report["background_priority"]["status"] = "emitted_dynamic"
            report["dynamic_priority"] = dynamic
        elif bg.priority_cells:
            report["background_priority"]["status"] = "emitted"
        elif report["background_priority"]["priority_opaque_pixels"] and bg.complement_cells:
            report["background_priority"]["reason"] = (
                f"scene {scene.name}: priority mask conflicts with A4 colour complement on SCR1")
            from .background_priority import priority_backdrop_audit
            report["background_priority"]["backdrop_audit"] = priority_backdrop_audit(scene.background_source)
    if behind_report is not None:
        report["sprite_priority"] = {**behind_report, "hardware_sprites": behind}
    if scene.parallax:
        from .parallax import bands
        slices, lines = bands(scene.parallax, bg.height // 8, view_h, top, f"scene {scene.name}")
        report["parallax"] = {"steps": [list(s) for s in scene.parallax],
                              "slices": [{"rows": [r0, r1], "shift": shift} for r0, r1, shift in slices],
                              "split_lines": lines, "register": "SCR2 x (0x8034)",
                              "interrupts_per_frame": len(lines), "ram_bytes": 6 + 4 * len(slices)}
    if second is not None:
        report["second_background"] = {**second[0].report(), "status": "emitted", "runtime_supported": True,
                                       "sidecar_supported": True, "scroll_x": second[1], "scroll_y": second[2],
                                       "behind": second[3]}
    if scene.projectiles is not None and scene.projectiles.definitions:
        from .projectile_runtime import projectile_ram_bytes
        report["projectiles"] = {"definition_indices": list(scene.projectiles.definition_indices),
            "resident_sheet_ids": list(scene.projectiles.sheet_ids), "capacity": scene.projectile_capacity,
            "pool_ram_bytes": projectile_ram_bytes(scene.projectile_capacity),
            "hit_handle_ram_bytes": 2 * len(scene.actors) if projectile_scripts else 0,
            "max_hardware_sprites": scene.projectile_capacity * max(scene.sheets[scene.sheet_ids.index(s)].max_sprites()
                for s in scene.projectiles.sheet_ids),
            "oam_order": "emote, player, actors, projectiles; whole metasprite culling at 64 slots"}
    if scene.emotes:
        report["emotes"] = {"resident": [s.name for s in scene.emote_sheets],
                            "tiles": 4 * len(scene.emotes), "hardware_sprites": 4, "ram_bytes": 4,
                            "timer": "shared between all VM contexts", "total_calls": 60}
    if scene.sprite_switches:
        report["actor_spritesheets"] = {
            "reachable_project_indices": [e[0] for e in scene.sprite_switches],
            "resident_sheet_ids": list(scene.sheet_ids),
            "ram_bytes": 28 * len(scene.actors),  # sheet/order words, bounds 4 words, animations 16
            "max_hardware_sprites": sum(max(scene.sheets[s].max_sprites() for s in
                {a["sheet"], *(e[1] for e in scene.sprite_switches)}) for a in scene.actors)}
    if actor_states is not None:
        report["actor_animation_states"] = {"states": list(actor_states),
                                            "ram_bytes": 16 * len(scene.actors)}
    if text:
        report["ui_palette_base"] = (len(second[0].front.palettes[0]) if second is not None else
                                     len(bg.palettes[0]) if shared_text else 0)
        # SCR2 palettes of the decor: Text Draw on the background takes the next one.
        report["scr2_palettes"] = (len(second[0].main.palettes[0]) if second is not None else
                                   next((len(p) for plane, p in zip(bg.planes, bg.palettes) if plane == BACK_PLANE), 0))
    if scene.palette_entries:
        # docs/M4_PALETTES.md: K2GE words of the slots a script rewrites.
        report["palette_entries"] = [list(e) for e in scene.palette_entries]
    if scene.color_variants is not None:
        # native_variants.py (PLAN_MEGAMAN L5): what ngc_bg_variant writes.
        plane, sets = scene.color_variants
        report["color_variants"] = {"plane": plane, "sets": [[list(words), backdrop] for words, backdrop in sets]}
    return files, report
