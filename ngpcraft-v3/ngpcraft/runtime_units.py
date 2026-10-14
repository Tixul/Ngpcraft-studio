"""Trusted unit selection shared by emission and the Toshiba link audit.

The registry describes public linkage, not semantic liveness of static functions.
Hardware sources are original V3 implementations; no template code is copied.
Existing template-derived startup licensing remains in runtime/NOTICE.txt.
"""
from dataclasses import dataclass
from pathlib import Path

from .compiler import CompileError


@dataclass(frozen=True)
class Unit:
    name: str
    symbols: tuple
    source: str = ""
    declaration: str = ""
    feature: str = ""
    dependencies: tuple = ()
    files: tuple = ()
    provenance: tuple = ()
    license: str = "MIT; LICENSE; Copyright (c) 2026 NgpCraft Tixu (original V3)"
    tests: tuple = ()
    # Which emitter selects the unit: "ir" (prototype IR, ngpcraft/emitter.py)
    # or "gbvm" (GB Studio projects, ngpcraft/gbvm/emit.py).
    backend: str = "ir"
    # A feature unit whose code the generator puts in another unit's object
    # (e.g. the movement code of scene_actors.c): that object's name.
    object: str = ""

    @property
    def object_name(self):
        if self.object:
            return self.object
        if self.source:
            return "hardware.rel"
        return ("cart" if self.name == "cartridge" else self.name) + ".rel"


import json as _json
# GBVM platform.c file-scope variables stay public (probes read them by link address).
_PLATFORM_DATA = tuple("_" + v for v in _json.loads((Path(__file__).with_name("runtime") / "platform_runtime.json")
                       .read_text(encoding="utf-8"))["public_variables"] if v != "plat_events")

_GENRE_DATA = {s: tuple("_" + v for v in _json.loads((Path(__file__).with_name("runtime") / f"{s}_runtime.json")
                        .read_text(encoding="utf-8"))["public_variables"]) for s in ("shmup", "pointnclick", "adventure")}

UNITS = (
    Unit("game", ("_ngc_game_init", "_ngc_game_tick"),
         provenance=("Original V3 specialized IR emitter: ngpcraft/emitter.py",),
         tests=("tests/test_generated_runtime.py", "tests/test_compiler.py")),
    Unit("startup", ("_main",), dependencies=("game",),
         files=(("startup.c", "src/startup.c"), ("ngpc.lcf", "ngpc.lcf"), ("NOTICE.txt", "NOTICE.txt")),
         provenance=("Adapted minimal system initialization, VBlank and shutdown from NgpCraft_base_template/src/core/ngpc_sys.c",
                     "Frame wait reference: NgpCraft_base_template/src/core/ngpc_timing.c",
                     "Link layout adapted from NgpCraft_base_template/ngpc.lcf; source snapshots in docs/M0_RUNTIME_PROVENANCE.json"),
         license="MIT; runtime/NOTICE.txt; Copyright (c) 2026 NGPC Template NgpCraft Tixu",
         tests=("tests/test_rom.py", "tests/test_stack_gate.py", "tests/test_build_evidence.py")),
    Unit("cartridge", ("_ngc_cart_id", "_ngc_cart_system", "_ngc_entry",
                       "_ngc_license", "_ngc_reserved", "_ngc_title"),
         dependencies=("startup",), files=(("cart.c", "src/cart.c"),),
         provenance=("Existing original V3 BIOS cartridge header, extracted unchanged from ngpcraft/emitter.py",),
         tests=("tests/test_rom.py", "tests/test_runtime_units.py")),
    Unit("backdrop", ("_ngc_backdrop",), "backdrop.c",
         "void ngc_backdrop(short value);", "backdrop",
         provenance=("Original V3 register writes, extracted unchanged from ngpcraft/emitter.py",),
         tests=("tests/test_rom.py:EmulatorRomTests.test_colors_and_waits_on_tlcs900", "tests/test_runtime_units.py")),
    Unit("palette", ("_ngc_palette",), "palette.c",
         "void ngc_palette(unsigned char slot, const unsigned short *colors);", "palette",
         provenance=("Original V3 SCR1 palette writes, extracted unchanged from ngpcraft/emitter.py",),
         tests=("tests/test_rom.py:EmulatorRomTests.test_used_palette_loaded_without_backdrop_handler", "tests/test_runtime_units.py")),
)


UNITS += (
    Unit("save_state_audio", ("_ngc_state_bytes_audio", "_ngc_state_put_audio",
                             "_ngc_state_get_audio", "_ngc_state_check_audio"),
         feature="audio_save_state", dependencies=("audio", "save_state_game"),
         object="audio.rel", backend="gbvm",
         provenance=("GBVM load_save.c music_current_track and restart in data_load; "
                     "Over Rev id_music.c deferred start; ngpcraft/save_emit.py",),
         tests=("tests/test_music_save.py",)),
    Unit("gbvm", ("_ngc_vm_start", "_script_memory", "_script_runner_update"),
         feature="gbvm", dependencies=("game",),
         provenance=("GBVM src/core/vm.c at commit bd6f41c (pinned by GB Studio 4.3.2): scheduler and "
                     "instruction handlers vendored by tools/extract_gbvm_runtime.py into "
                     "ngpcraft/runtime/gbvm_runtime.json, C89 adaptations listed there",
                     "Opcode decoder, native table and NGPC handlers: original V3, ngpcraft/gbvm/emit.py"),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_gbvm_backend.py",), backend="gbvm"),
)


UNITS += (
    Unit("scene_background", ("_ngc_background_load",), feature="static_background",
         dependencies=("game",),
         provenance=("Original V3 static asset compiler and specialized loader: ngpcraft/background.py, ngpcraft/background_rom.py",
                     "SNK K2GETechRef sections 4-3-5 (pixels), 4-4 (planes), 4-12 (palettes); no template routine copied"),
         tests=("tests/test_background.py",), backend="background"),
    Unit("scene_stream", ("_ngc_stream_load", "_ngc_scroll_set", "_ngc_scroll_commit", "_ngc_stream_update",
                      "_ngc_scroll", "_ngc_scroll_next"),
         feature="streamed_background", dependencies=("game",),
         provenance=("Specialized streamer: ngpcraft/background_stream.py; edge streaming and self-healing "
                     "re-blit ported from NgpCraft_engine_v2 optional/ngpc_mapstream/ngpc_mapstream.c (corrected)",
                     "Scroll clamp and reset/repaint semantics: GBVM src/core/scroll.c at commit bd6f41c",
                     "SNK K2GETechRef sections 4-4 (planes, scroll registers 0x8032-0x8035), 6 (VRAM access timing)"),
         tests=("tests/test_background_stream.py",), backend="background"),
    Unit("scene_second_background", ("_ngc_front_scroll",), feature="second_background",
         dependencies=("scene_stream",), object="scene_stream.rel",
         provenance=("Independent plane kernels reuse ngpcraft/background_stream.py; "
                     "compile-time dual composition: ngpcraft/second_stream.py",),
         tests=("tests/test_second_stream.py",), backend="background"),
    Unit("scene_sprites", ("_ngc_sprites_load", "_ngc_sprites_begin", "_ngc_sprite_draw",
                           "_ngc_sprites_end", "_ngc_sprites_commit", "_ngc_oam_used",
                           "_ngc_soam", "_ngc_scol", "_ngc_soam_count"),
         feature="sprites", dependencies=("game",),
         provenance=("Sprite compiler ported from GB Studio 4.3.2 compileSprites.ts, readSpriteData.ts "
                     "optimiseTiles, sprites/helpers.ts: ngpcraft/sprites.py",
                     "Renderer semantics: GBVM src/core/actor.c actors_render at commit bd6f41c; "
                     "specialized C: ngpcraft/scene_sprites.py",
                     "SNK K2GETechRef section 4-3 (sprite VRAM 0x8800/0x8C00, PR.C, position chain)",
                     "Shadow OAM block copy (LDIRW/LDIR) adapted from NgpCraft_base_template "
                     "optional/ngpc_soam/ngpc_soam_flush.asm (MIT)"),
         tests=("tests/test_sprites.py", "tests/test_scene_sprites.py"), backend="background"),
    Unit("scene_actors", ("_ngc_actors_start", "_ngc_actors_load", "_ngc_actors_repaint",
                          "_ngc_actors_scroll", "_ngc_actors_update", "_ngc_actors_render",
                          "_ngc_actor_set_pos", "_ngc_actor_set_dir", "_ngc_game_time",
                          "_ngc_camera_update", "_ngc_camera_x", "_ngc_camera_y",
                          "_ngc_actor_activate", "_ngc_actor_deactivate", "_ngc_actor_set_frame_offset",
                          "_ngc_actor_set_flags", "_ngc_actor_set_anim_tick", "_ngc_actor_set_move_speed",
                          "_act_x", "_act_y"),
         feature="actors", dependencies=("game",),
         provenance=("Specialized C: ngpcraft/scene_actors.py; semantics of GBVM src/core/actor.c, "
                     "include/actor.h, include/linked_list.h, src/core/scroll.c (activation), "
                     "src/core/data_manager.c load_scene, src/core/camera.c and src/core/core.c at commit bd6f41c",
                     "Line-by-line oracle for the probes: tools/gbvm_actors.py"),
         tests=("tests/test_scene_actors.py",), backend="background"),
    Unit("scene_input", ("_ngc_input_init", "_ngc_input_update", "_ngc_joy", "_ngc_joy_pressed", "_ngc_recent_joy",
                         "_ngc_last_joy"),
         feature="input", dependencies=("game",),
         provenance=("NgpCraft_base_template src/core/ngpc_input.c hardware read; GBVM input.c edge and recent direction semantics",),
         license="MIT; runtime/NOTICE.txt; Copyright (c) 2026 NGPC Template NgpCraft Tixu; GBVM_LICENSE.txt",
         tests=("tests/test_topdown.py",), backend="background"),
    Unit("scene_topdown", ("_ngc_topdown_init", "_ngc_topdown_update"), feature="topdown",
         dependencies=("scene_actors", "scene_input"), object="scene_actors.rel",
         provenance=("GBVM src/states/topdown.c, collision.c and actor.c; specialized ngpcraft/scene_topdown.py",),
         tests=("tests/test_topdown.py",), backend="background"),
    Unit("scene_platform", ("_ngc_platform_boot", "_ngc_platform_init", "_ngc_platform_update") + _PLATFORM_DATA, feature="platform",
         dependencies=("scene_actors", "scene_input"), object="scene_actors.rel",
         provenance=("GBVM src/states/platform.c at commit bd6f41c, vendored by tools/extract_platform.py "
                     "(ngpcraft/runtime/platform_runtime.json); bindings ngpcraft/scene_platform.py",),
         license="MIT; GBVM_LICENSE.txt",
         tests=("tests/test_platform.py",), backend="background"),
    Unit("scene_shmup", ("_ngc_shmup_boot", "_ngc_shmup_init", "_ngc_shmup_update") + _GENRE_DATA["shmup"], feature="shmup",
         dependencies=("scene_actors", "scene_input"), object="scene_actors.rel",
         provenance=("GBVM src/states/shmup.c at commit bd6f41c, vendored by tools/extract_genres.py "
                     "(ngpcraft/runtime/shmup_runtime.json); bindings ngpcraft/scene_genres.py",),
         license="MIT; GBVM_LICENSE.txt",
         tests=("tests/test_genres.py",), backend="background"),
    Unit("scene_pointnclick", ("_ngc_pointnclick_init", "_ngc_pointnclick_update") + _GENRE_DATA["pointnclick"],
         feature="pointnclick", dependencies=("scene_actors", "scene_input"), object="scene_actors.rel",
         provenance=("GBVM src/states/pointnclick.c at commit bd6f41c, vendored by tools/extract_genres.py "
                     "(ngpcraft/runtime/pointnclick_runtime.json); bindings ngpcraft/scene_genres.py",),
         license="MIT; GBVM_LICENSE.txt",
         tests=("tests/test_genres.py",), backend="background"),
    Unit("scene_adventure", ("_ngc_adventure_boot", "_ngc_adventure_init", "_ngc_adventure_update")
         + tuple(v for v in _GENRE_DATA["adventure"] if v != "_adv_events"),
         feature="adventure", dependencies=("scene_actors", "scene_input"), object="scene_actors.rel",
         provenance=("GBVM src/states/adventure.c at commit bd6f41c, vendored by tools/extract_genres.py "
                     "(ngpcraft/runtime/adventure_runtime.json); bindings ngpcraft/scene_genres.py",),
         license="MIT; GBVM_LICENSE.txt",
         tests=("tests/test_adventure.py",), backend="background"),
    # Loaded by platform.c or adventure.c (PLATFORM / ADVENTURE_ANIM_OVERRIDES_SET).
    Unit("scene_anim_states", ("_ngc_player_load_animations", "_ngc_player_anim_set"), feature="anim_states",
         dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM src/core/data_manager.c load_animations; GB Studio compileSprites.ts statesOrder and "
                     "generateGBVMData.ts animations_lookup; specialized ngpcraft/scene_actors.py",),
         tests=("tests/test_platform.py", "tests/test_engine_fields.py"), backend="background"),
    Unit("scene_stack", ("_ngc_scene_push", "_ngc_scene_pop", "_ngc_scene_stack_reset"), feature="scene_stack",
         dependencies=("scene_actors", "gbvm"), object="scene_actors.rel",
         provenance=("GBVM src/core/vm_scene.c and data_manager.c scene_stack at commit bd6f41c, on the scene "
                     "engine (ngpcraft/gbvm/emit.py stack_source, NGPC_FUNCTIONS)",),
         tests=("tests/test_scene_stack.py",), backend="gbvm"),
    Unit("gbvm_input_events", ("_events_update",), feature="input_events",
         dependencies=("gbvm", "scene_input"), object="gbvm.rel",
         provenance=("GBVM src/core/events.c (events_init, events_update) and src/core/vm_gameboy.c "
                     "(vm_context_prepare, vm_input_attach, vm_input_detach) at commit bd6f41c, vendored by "
                     "tools/extract_gbvm_runtime.py; core.c process_VM ordering in ngpcraft/gbvm/emit.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_input_scripts.py",), backend="gbvm"),
    # Debug builds only (export --debug, docs/M7_DEBUG.md): vm.c globals and
    # VM_STEP made public, the SCRIPT_CTX layout table; the script bank.
    Unit("gbvm_debug", ("_CTXS", "_first_ctx", "_executing_ctx", "_VM_STEP", "_ngc_debug_ctx_layout"),
         feature="debug", dependencies=("gbvm",), object="gbvm.rel",
         provenance=("GB Studio 4.3.2 debug build: scriptBuilderBase addDebugSymbol, binjgb debugger "
                     "(_first_ctx, _executing_ctx, _VM_STEP); ngpcraft/debug_build.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_debug_build.py",), backend="gbvm"),
    Unit("game_debug", ("_ngc_bank_0", "_ngc_banks"), feature="debug", dependencies=("gbvm_debug",), object="game.rel",
         provenance=("ngpcraft/debug_build.py: the first script bank and the bank table of game.c made public",),
         license="MIT", tests=("tests/test_debug_build.py",), backend="gbvm"),
    Unit("gbvm_link_exchange", ("_ngc_com_buf", "_ngc_com_count"), feature="link_exchange",
         dependencies=("gbvm",), object="gbvm.rel",
         provenance=("GBVM src/core/vm_sio.c vm_sio_exchange at commit bd6f41c; BIOS COM vectors 0x10..0x1A "
                     "and A5|len|data|sum framing from NgpCraft_base_template optional/ngpc_link (Over Rev "
                     "two-player mode); ngpcraft/gbvm/link.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_link.py",), backend="gbvm"),
    Unit("gbvm_ngpc_clock", ("_ngc_rtc_ptr", "_ngc_rtc_raw"), feature="ngpc_clock",
         dependencies=("gbvm",), object="gbvm.rel",
         provenance=("SNK SDK SysCall.txt VECT_RTCGET by SWI 1 (XHL3 = 7 BCD bytes); GBVM src/core/vm_rtc.c "
                     "at commit bd6f41c (VM_RTC_* on the NGPC calendar); ngpcraft/gbvm/ngpc_system.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_ngpc_system.py",), backend="gbvm"),
    Unit("gbvm_music_routines", ("_music_events_update",), feature="music_routines",
         dependencies=("gbvm",), object="gbvm.rel",
         provenance=("GBVM src/core/music_manager.c (music_init_events, music_events_update) and "
                     "src/core/vm_music.c vm_music_routine at commit bd6f41c; core.c process_VM ordering "
                     "in ngpcraft/gbvm/emit.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_music_routine.py",), backend="gbvm"),
    Unit("gbvm_timer_events", ("_timers_update",), feature="timer_events",
         dependencies=("gbvm", "scene_input"), object="gbvm.rel",
         provenance=("GBVM src/core/events.c (timers_init, timers_update) and src/core/vm_gameboy.c "
                     "(vm_timer_prepare, vm_timer_set, vm_timer_stop, vm_timer_reset) at commit bd6f41c, vendored by "
                     "tools/extract_gbvm_runtime.py; core.c process_VM ordering in ngpcraft/gbvm/emit.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_gbvm_timers.py",), backend="gbvm"),
    Unit("gbvm_actor_interactions", ("_ngc_vm_interact",), feature="actor_interactions",
         dependencies=("gbvm_scene",), object="gbvm.rel",
         provenance=("GBVM topdown.c script_execute with one zero thread-local argument, compileData.ts locked actors scripts",),
         tests=("tests/test_topdown.py",), backend="gbvm"),
    Unit("scene_triggers", ("_ngc_vm_trigger",), feature="triggers",
         dependencies=("scene_actors", "gbvm_scene"), object="gbvm.rel",
         provenance=("GBVM trigger.c and trigger.h, compileData.ts combineScripts and ScriptBuilder.ifParamValue; specialized ngpcraft/scene_triggers.py",),
         tests=("tests/test_triggers.py",), backend="gbvm"),
    # collision_update: the bookkeeping (iframes, registration) runs with or without
    # scripts (shmup.c reads player_iframes); player_collisions adds the scripts.
    Unit("scene_player_collisions", ("_ngc_collision_update",), feature="collision_update",
         dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM actor.c actors_handle_player_collision, topdown.c / platform.c registration and core.c ordering; ngpcraft/scene_collisions.py",),
         tests=("tests/test_player_collisions.py",), backend="background"),
    Unit("scene_projectiles", ("_ngc_projectile_launch",), feature="projectiles",
         dependencies=("scene_actors", "scene_sprites"), object="scene_actors.rel",
         provenance=("GBVM bd6f41c projectiles.c; selected ngpcraft/projectile_runtime.py and scene_projectiles.py actor/frame attachment",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_projectile_scenes.py", "tests/test_projectile_events.py"), backend="background"),
    Unit("scene_projectile_slots", ("_ngc_projectile_load_slot",), feature="projectile_slots",
         dependencies=("scene_projectiles",), object="scene_actors.rel",
         provenance=("GBVM bd6f41c vm_projectiles.c vm_projectile_load_type and projectiles.c projectile_defs "
                     "as a slot map; ngpcraft/projectile_runtime.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_projectile_slots.py",), backend="background"),
    Unit("scene_update_pause", ("_ngc_pause_state_update",), feature="scene_update_pause",
         dependencies=("gbvm",), object="gbvm.rel",
         provenance=("GBVM src/core/core.c pause_state_update (process_VM, load_scene) at commit bd6f41c; "
                     "ngpcraft/gbvm/emit.py pause_state_game",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_scene_update_pause.py",), backend="background"),
    Unit("camera_properties", ("_camera_deadzone_x", "_camera_deadzone_y", "_camera_offset_x", "_camera_offset_y"),
         feature="camera_properties", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM src/core/camera.c camera_init / camera_reset at commit bd6f41c; "
                     "ngpcraft/scene_engine.py publication",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_camera_properties.py",), backend="background"),
    Unit("scroll_bounds", ("_ngc_scroll_x_min", "_ngc_scroll_y_min", "_ngc_scroll_x_max", "_ngc_scroll_y_max"),
         feature="scroll_bounds", dependencies=("scene_stream",), object="scene_stream.rel",
         provenance=("GBVM src/core/scroll.c scroll_update bounds, data_manager.c load_scene at commit bd6f41c; "
                     "ngpcraft/background_stream.py scroll_bounds_clamp",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_camera_set_bounds.py",), backend="background"),
    Unit("replace_tiles", ("_ngc_replace_tile_xy",), feature="replace_tiles",
         dependencies=("scene_stream",), object="scene_stream.rel",
         provenance=("GBVM bd6f41c vm_gameboy.c vm_replace_tile_xy; GB Studio compileImages.ts tile identity; "
                     "ngpcraft/replace_tiles.py specialised writes of prepared NGPC tiles",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_replace_tiles.py",), backend="background"),
    Unit("projectile_modal_frame", ("_ngc_actors_update_modal",), feature="projectile_modal",
         dependencies=("scene_projectiles",), object="scene_actors.rel",
         provenance=("GBVM bd6f41c ui.c ui_run_modal / ui_run_menu: actors_update, actors_render, "
                     "projectiles_render, no projectiles_update; ngpcraft/scene_projectiles.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_projectile_events.py",), backend="background"),
    Unit("gbvm_projectile_hits", ("_ngc_vm_projectile_hit", "_ngc_vm_projectile_reset", "_ngc_vm_projectile_detach", "_ngc_vm_projectile_activate"),
         feature="projectile_hits", dependencies=("gbvm_scene",), object="gbvm.rel",
         provenance=("GBVM bd6f41c projectiles.c per-actor hscript_hit and script_execute; "
                     "ngpcraft/gbvm/projectiles.py explicit script presence and bank-offset mapping",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_projectile_vm.py", "tests/test_projectile_events.py"), backend="gbvm"),
    Unit("gbvm_player_collisions", ("_ngc_vm_hit",), feature="player_collisions",
         dependencies=("gbvm_scene",), object="gbvm.rel",
         provenance=("GBVM actor.c player hit argument and actor argument zero; compileData.ts unlocked combined callbacks",),
         tests=("tests/test_player_collisions.py",), backend="gbvm"),
    Unit("scene_actor_move_init", ("_ngc_actor_move_init",),
         feature="actor_move_init", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM vm_actor.c movement initialisation on actor arrays",),
         tests=("tests/test_actor_moves.py", "tests/test_actor_push.py"), backend="background"),
    Unit("scene_actor_moves", ("_ngc_actor_move_x", "_ngc_actor_move_y",
                               "_ngc_actor_move_xy", "_ngc_actor_move_dir_x", "_ngc_actor_move_dir_y",
                               "_ngc_actor_set_anim_moving"),
         feature="actor_moves", dependencies=("scene_actor_move_init",), object="scene_actors.rel",
         provenance=("Specialized C in scene_actors.c: ngpcraft/scene_actors.py _moves_source; semantics of "
                     "GBVM src/core/vm_actor.c (vm_actor_move_to_init/_x/_y/_xy/_set_dir_x/_set_dir_y, "
                     "vm_actor_set_anim_moving, check_collision_horizontal/vertical), src/core/collision.c, "
                     "src/core/actor.c actor_overlapping_bb at commit bd6f41c",
                     "Line-by-line oracle for the probes: tools/gbvm_actors.py Movement"),
         tests=("tests/test_actor_moves.py",), backend="background"),
    Unit("scene_actor_push", ("_ngc_actor_push_step",),
         feature="actor_push", dependencies=("scene_actor_move_init",), object="scene_actors.rel",
         provenance=("GBVM vm_actor.c vm_actor_move_to state machine on D-2 actor arrays; "
                     "shared movement initialisation and collisions in ngpcraft/scene_actors.py",),
         tests=("tests/test_actor_push.py",), backend="background"),
    Unit("scene_actor_directions", ("_ngc_actor_get_dir",),
         feature="actor_directions", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM vm_actor.c vm_actor_get_dir on D-2 actor arrays",),
         tests=("tests/test_actor_push.py",), backend="background"),
    Unit("scene_actor_move_cancel", ("_ngc_actor_move_cancel",),
         feature="actor_move_cancel", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM src/core/vm_actor.c vm_actor_move_cancel and movement interrupt checks; "
                     "specialized C in ngpcraft/scene_actors.py; oracle tools/gbvm_actors.py Movement",),
         tests=("tests/test_actor_move_cancel.py",), backend="background"),
    Unit("platform_callbacks", ("_ngc_vm_platform_reset", "_ngc_vm_platform_event"), feature="platform_callbacks",
         dependencies=("gbvm_scene", "scene_platform"), object="gbvm.rel",
         provenance=("GBVM states/platform.c plat_callback_attach/detach/reset/execute at bd6f41c; selected original bodies with sparse slot and bank-offset bindings in gbvm/platform_callbacks.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_platform_callbacks.py",), backend="gbvm"),
    Unit("adventure_callbacks", ("_ngc_vm_adventure_reset", "_ngc_vm_adventure_event"), feature="adventure_callbacks",
         dependencies=("gbvm_scene", "scene_adventure"), object="gbvm.rel",
         provenance=("GBVM states/adventure.c adv_callback_attach/detach/reset/execute at bd6f41c; selected original "
                     "bodies with sparse slot and bank-offset bindings in gbvm/platform_callbacks.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_adventure.py",), backend="gbvm"),
    Unit("camera_settings", ("_ngc_camera_settings",), dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM camera.c shared settings; existing scene load and selected camera instructions",),
         tests=("tests/test_scene_switch.py", "tests/test_camera_control.py"), backend="background"),
    Unit("camera_control", ("_ngc_camera_clamp_x", "_ngc_camera_clamp_y"),
         feature="camera_control", dependencies=("gbvm_scene", "camera_settings"), object="scene_actors.rel",
         provenance=("GBVM bd6f41c vm_camera.c, camera.c; selected camera state publication by scene_engine.py",),
         tests=("tests/test_camera_control.py",), backend="background"),
    Unit("camera_shake", ("_ngc_scroll_offset_x", "_ngc_scroll_offset_y"),
         feature="camera_shake", dependencies=("gbvm", "vblank_clock", "camera_shake_stream"), object="gbvm.rel",
         provenance=("GBVM vm_camera.c camera_shake_frames at bd6f41c; GBDK 4.3.0 rand recurrence; "
                     "ngpcraft/gbvm/emit.py",), tests=("tests/test_camera_shake.py",), backend="gbvm"),
    # GBDK's 16-bit sys_time, counted in the VBlank; only as a dependency.
    Unit("vblank_clock", ("_ngc_sys_time",), dependencies=("startup",), object="startup.rel",
         provenance=("GBDK 16-bit sys_time VBlank clock; selected startup.c counter",),
         tests=("tests/test_camera_shake.py", "tests/test_gbvm_rate_limit.py"), backend="gbvm"),
    Unit("rate_limit", (), feature="rate_limit", dependencies=("gbvm", "vblank_clock"), object="gbvm.rel",
         provenance=("GBVM src/core/vm.c vm_rate_limit_const at bd6f41c (tools/extract_gbvm_runtime.py)",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_gbvm_rate_limit.py",), backend="gbvm"),
    Unit("camera_shake_stream", ("_ngc_scroll_base_next",), feature="camera_shake", dependencies=("scene_stream",), object="scene_stream.rel",
         provenance=("GBVM scroll.c scroll_update logical vs draw coordinates; background_stream.py",),
         tests=("tests/test_camera_shake.py",), backend="background"),
    Unit("scene_actor_positions", ("_ngc_actor_get_pos",),
         feature="actor_positions", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM src/core/vm_actor.c vm_actor_get_pos at commit bd6f41c; "
                     "specialized ngpcraft/scene_actors.py; GB Studio ifActorRelativeToActor "
                     "and ifActorAtPositionByScriptValues",),
         tests=("tests/test_actor_conditions.py",), backend="background"),
    Unit("scene_actor_emotes", ("_ngc_actor_emote_step", "_ngc_emote_actor", "_ngc_emote_timer"),
         feature="actor_emotes", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM vm_actor.c vm_actor_emote, actor.c emote offsets/metasprites at bd6f41c; "
                     "scene-resident NGPC emote tiles, specialized scene_actors.py",),
         tests=("tests/test_actor_emotes.py",), backend="background"),
    Unit("scene_actor_spritesheets", ("_ngc_actor_set_spritesheet",),
         feature="actor_spritesheets", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM vm_actor.c vm_actor_set_spritesheet, actor.h actor_reset_anim at bd6f41c; "
                     "NGPC scene-resident tiles; specialized scene_actors.py",),
         tests=("tests/test_actor_spritesheets.py",), backend="background"),
    Unit("scene_actor_anim_states", ("_ngc_actor_set_anim_set",),
         feature="actor_anim_states", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM src/core/vm_actor.c vm_actor_set_anim_set, data_manager.c load_animations, "
                     "actor.c actor_set_frames at commit bd6f41c; GB Studio compileSprites.ts statesOrder; "
                     "specialized ngpcraft/scene_actors.py",),
         tests=("tests/test_actor_states.py",), backend="background"),
    Unit("scene_actor_stop_update", ("_ngc_actor_stop_update",),
         feature="actor_stop_update", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM src/core/vm_actor.c vm_actor_terminate_update and vm.c script_terminate "
                     "at commit bd6f41c; specialized ngpcraft/scene_actors.py",),
         tests=("tests/test_actor_stop_update.py",), backend="background"),
    Unit("gbvm_sprites_visible", ("_ngc_sprites_hidden", "_ngc_sprites_hide_all"),
         feature="sprites_visible", dependencies=("gbvm_scene",), object="gbvm.rel",
         provenance=("GBVM src/core/vm_gameboy.c vm_set_sprites_visible and interrupts.c "
                     "hide_sprites at commit bd6f41c; K2GE priority clear in ngpcraft/gbvm/emit.py",),
         tests=("tests/test_batch2_events.py",), backend="background"),
    Unit("scene_actor_set_bounds", ("_ngc_actor_set_bounds",),
         feature="actor_set_bounds", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM src/core/vm_actor.c vm_actor_set_bounds at commit bd6f41c; "
                     "specialized ngpcraft/scene_actors.py",),
         tests=("tests/test_actor_collision_box.py",), backend="background"),
    Unit("scene_actor_start_update", ("_ngc_actor_start_update",),
         feature="actor_start_update", dependencies=("scene_actors",), object="scene_actors.rel",
         provenance=("GBVM src/core/vm_actor.c vm_actor_begin_update at commit bd6f41c; "
                     "specialized ngpcraft/scene_actors.py",),
         tests=("tests/test_batch2_events.py",), backend="background"),
    Unit("gbvm_actor_updates", ("_ngc_vm_prepare", "_ngc_vm_execute", "_ngc_vm_terminate"),
         feature="actor_updates", dependencies=("gbvm_scene",), object="gbvm.rel",
         provenance=("GBVM actor.c activation and deactivation; vm.c script_execute and script_terminate "
                     "at commit bd6f41c; compileData.ts actorsMovement and compileEntityEvents.ts",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_actor_updates.py",), backend="gbvm"),
    Unit("gbvm_frame_wait", ("_ngc_vm_frame_end",), dependencies=("gbvm",), object="gbvm.rel",
         provenance=("GBVM src/core/core.c process_VM: wait_vbl_done() ends every frame, at commit bd6f41c; "
                     "NGPC wait in ngpcraft/gbvm/emit.py FRAME_WAIT_SUPPORT",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_scene_switch.py", "tests/test_scene_cuts.py", "tests/test_gbvm_backend.py"), backend="gbvm"),
    Unit("gbvm_fades", (), feature="fades", dependencies=("gbvm_frame_wait",), object="gbvm.rel",
         provenance=("GBVM fade_manager.c; NGPC palettes in ngpcraft/gbvm/emit.py FADE_SUPPORT_TEMPLATE",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_gbvm_backend.py",), backend="gbvm"),
    Unit("palette_patch", ("_ngc_palette_patch",), feature="palette_recycle", dependencies=("gbvm_scene",),
         object="gbvm.rel",
         provenance=("Original V3: recycled background and sprite palettes written through the fade shadow "
                     "(ngpcraft/gbvm/emit.py palette_patch_source, ngpcraft/palette_recycle.py, "
                     "ngpcraft/sprite_recycle.py); GBVM fade_manager.c fade_timer/fade_running semantics",),
         tests=("tests/test_palette_recycle.py", "tests/test_sprite_recycle.py"), backend="gbvm"),
    Unit("scene_changes", ("_ngc_scene", "_ngc_scene_keep"), feature="scene_changes",
         dependencies=("scene_actors", "gbvm_scene_changes", "camera_settings"), object="scene_actors.rel",
         provenance=("GBVM src/core/core.c EXCEPTION_CHANGE_SCENE and src/core/data_manager.c load_scene at "
                     "commit bd6f41c (what is reloaded, what is kept); scenes linked by ngpcraft/scene_link.py, "
                     "kept state ngpcraft/gbvm/emit.py keep_source",),
         tests=("tests/test_scene_switch.py", "tests/test_scene_cuts.py"), backend="background"),
    Unit("gbvm_scene_changes", ("_ngc_vm_run", "_ngc_vm_kill", "_ngc_vm_exception", "_ngc_palette_show",
                                "_ngc_palette_shadow"), feature="scene_changes", dependencies=("gbvm_scene",),
         object="gbvm.rel",
         provenance=("GBVM src/core/vm.c vm_raise, script_runner_init(FALSE), script_execute and "
                     "src/core/palette.c palette_init/load_bkg_palette, fade_manager.c at commit bd6f41c; "
                     "scriptBuilder.ts sceneSwitchUsingScriptValues; NGPC direct palette publication "
                     "without fades in ngpcraft/gbvm/emit.py c_scenes",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_scene_switch.py", "tests/test_scene_cuts.py", "tests/test_second_stream.py"), backend="gbvm"),
    Unit("gbvm_scene_fade_cuts", ("_ngc_palette_hidden",), feature="scene_fade_cuts",
         dependencies=("gbvm_scene_changes", "gbvm_fades"), object="gbvm.rel",
         provenance=("Original V3: a scene change without fade, in a project with fades, publishes in VBlank "
                     "(ngpcraft/gbvm/emit.py FADE_CUT_SOURCE); GBVM fade_manager.c fade_timer semantics",),
         tests=("tests/test_scene_switch.py",), backend="gbvm"),
    Unit("gbvm_text", ("_ngc_ui_start", "_ngc_ui_scene_load", "_ngc_ui_update", "_ngc_ui_commit"),
         feature="text", dependencies=("gbvm_scene", "scene_input", "gbvm_frame_wait"), object="gbvm.rel",
         provenance=("GBVM src/core/ui.c and src/core/vm_ui.c at commit bd6f41c (ui_init, ui_update, "
                     "ui_draw_text_buffer_char, ui_run_modal, vm_load_text, vm_display_text, vm_overlay_*), "
                     "vendored by tools/extract_gbvm_runtime.py",
                     "K2GE parts (map cells, tiles, one bit plane VWF printer from ui_a.s, itoa_fmt from "
                     "vm_ui_a.s): original V3, ngpcraft/gbvm/emit_ui.py; data: ngpcraft/ui_text.py, ngpcraft/fonts.py"),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_text_events.py",), backend="gbvm"),
    Unit("frame_rate_30", ("_ngc_frame",), feature="frame_rate_30", dependencies=("startup", "gbvm"),
         object="startup.rel",
         provenance=("startup.c VBlank counter made public for the fixed 30 per second cadence "
                     "(ngpcraft.json ngpc.frame_rate; ngc_vm_frame_end in ngpcraft/gbvm/emit.py)",),
         tests=("tests/test_frame_rate.py",), backend="gbvm"),
    Unit("gbvm_text_shared", ("_ngc_ui_sprite_cut", "_ngc_ui_vblank"), feature="text_shared",
         dependencies=("gbvm_text",), object="gbvm.rel",
         provenance=("Case B logical map and R2 sprite mask: ngpcraft/gbvm/emit_ui.py; "
                     "Timer0 split adapted from MIT NgpCraft_base_template ngpc_raster_chain.c, ngpcraft/runtime/text_split.c",),
         license="MIT; runtime/NOTICE.txt; Copyright (c) 2026 NGPC Template NgpCraft Tixu",
         tests=("tests/test_text_split.py",), backend="gbvm"),
    Unit("gbvm_text_cut", ("_ngc_ui_cut_vblank",), feature="text_cut",
         dependencies=("gbvm_text",), object="gbvm.rel",
         provenance=("GBVM interrupts.c simple_LCD_isr overlay_cut_scanline on SCR1 (case A): "
                     "ngpcraft/gbvm/emit_ui.py; Timer0 split adapted from MIT NgpCraft_base_template "
                     "ngpc_raster_chain.c, ngpcraft/runtime/text_split.c",),
         license="MIT; runtime/NOTICE.txt; Copyright (c) 2026 NGPC Template NgpCraft Tixu",
         tests=("tests/test_scanline_cutoff.py",), backend="gbvm"),
    Unit("parallax", ("_ngc_parallax_x", "_ngc_parallax_y", "_ngc_parallax_line", "_ngc_parallax_n", "_ngc_parallax_init",
                      "_ngc_parallax_vblank"), feature="parallax", dependencies=("startup",),
         files=(("parallax.c", "src/parallax.c"),), backend="background",
         provenance=("GBVM src/core/parallax.c and scroll.c scroll_viewport slices (GB Studio compileParallax) "
                     "on SCR2; Timer0 chain adapted from MIT NgpCraft_base_template ngpc_raster_chain.c, "
                     "ngpcraft/runtime/parallax.c",),
         license="MIT; runtime/NOTICE.txt; Copyright (c) 2026 NGPC Template NgpCraft Tixu; GBVM_LICENSE.txt",
         tests=("tests/test_parallax.py",)),
    Unit("game_text", ("_ngc_ui_modal_logic", "_ngc_ui_modal_commit"), feature="text",
         dependencies=("game", "scene_actors"), object="game.rel",
         provenance=("GBVM src/core/ui.c ui_run_modal frame (camera, actors, render, vertical blank) on the "
                     "scene engine: ngpcraft/gbvm/emit_ui.py game_lines",),
         tests=("tests/test_text_events.py",), backend="gbvm"),
    Unit("gbvm_scene", ("_ngc_vm_locked",), feature="gbvm_scene", dependencies=("gbvm", "scene_actors", "gbvm_frame_wait"),
         object="gbvm.rel",
         provenance=("GBVM include/vm.h VM_ISLOCKED at commit bd6f41c, for actors_update (src/core/actor.c)",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_actor_moves.py",), backend="gbvm"),
)


# Save state writer and reader (docs/M5_SAVE.md step 3, ngpcraft/save_emit.py).
# Not selected by any export until the save events are delivered.
UNITS += (
    Unit("save_state_vm", ("_ngc_state_bytes_vm", "_ngc_state_put_vm", "_ngc_state_get_vm",
                           "_ngc_state_bytes_gbvm", "_ngc_state_put_gbvm", "_ngc_state_get_gbvm"),
         feature="save_state", dependencies=("gbvm",), object="gbvm.rel", backend="gbvm",
         provenance=("GBVM src/core/load_save.c save_points at commit bd6f41c; VM snapshot NV1 "
                     "ngpcraft/runtime/save_vm.c (oracle tools/save_vm.py); raw bytes of the other saved "
                     "variables, ngpcraft/save_emit.py",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_save_vm.py", "tests/test_save_emit.py")),
    Unit("save_state_actors", ("_ngc_state_bytes_scene_actors", "_ngc_state_put_scene_actors",
                               "_ngc_state_get_scene_actors"),
         feature="save_state", dependencies=("scene_actors",), object="scene_actors.rel", backend="gbvm",
         provenance=("GBVM load_save.c save_points actors, lists, scene, scene stack; classification "
                     "ngpcraft/save_state.py, writer ngpcraft/save_emit.py",),
         tests=("tests/test_save_emit.py",)),
    Unit("save_state_game", ("_ngc_save_length", "_ngc_save_produce", "_ngc_save_restore", "_ngc_save_signature"),
         feature="save_state", dependencies=("game",), object="game.rel", backend="gbvm",
         provenance=("Producer given to the journal and reader of a load: ngpcraft/save_emit.py game_source",),
         tests=("tests/test_save_emit.py",)),
    Unit("save_journal", ("_ngc_journal_save", "_ngc_journal_clear", "_ngc_journal_find", "_ngc_save_put",
                          "_ngc_save_get", "_ngc_journal_forget"),
         feature="save_journal", dependencies=("save_flash", "save_state_game"),
         files=(("save_journal.c", "src/save_journal.c"),), backend="gbvm",
         provenance=("Journal of records over two 8 KiB flash blocks, three slots: ngpcraft/runtime/save_journal.c "
                     "(oracle tools/save_journal.py), generalised from Over Rev v31 ngpc_flash.c (MIT, NgpCraft)",),
         tests=("tests/test_save_journal.py", "tests/test_save_journal_c.py", "tests/test_save_rom.py")),
    Unit("save_flash", ("_ngc_flash_ready", "_ngc_flash_block", "_ngc_flash_program", "_ngc_flash_erase"),
         feature="save_journal", files=(("save_flash.c", "src/save_flash.c"),), backend="gbvm",
         provenance=("Flash driver adapted from Over Rev v31 ngpc_flash.c / ngpc_flash_asm.asm (MIT, NgpCraft): "
                     "VECT_FLASHWRITE page writes, StarGunner AMD erase stub run from RAM 0x6E00, interrupts "
                     "disabled; blocks from the BIOS cartridge size at 0x6C58 (FlashMem.txt)",),
         tests=("tests/test_save_rom.py",)),
    Unit("scene_actor_resume", ("_ngc_actors_resume",), feature="save_load", dependencies=("scene_actors",),
         object="scene_actors.rel", backend="background",
         provenance=("GBVM src/core/data_manager.c load_scene(init_data FALSE) and actor.c player_init at commit "
                     "bd6f41c; specialized ngpcraft/scene_actors.py",),
         tests=("tests/test_save_rom.py",)),
    Unit("gbvm_save_load", ("_ngc_vm_load",), feature="save_load", dependencies=("gbvm_scene_changes",),
         object="gbvm.rel", backend="gbvm",
         provenance=("GBVM src/core/core.c EXCEPTION_LOAD (fade_out_modal, data_load) at commit bd6f41c; "
                     "ngpcraft/gbvm/emit.py c_scenes",),
         license="MIT; GBVM_LICENSE.txt; Copyright (c) 2020 Toxa",
         tests=("tests/test_save_rom.py",)),
)


# Physical clock opt-in for selected native audio; driver service stays in main.
UNITS += (
    Unit("audio_clock", ("_g_vb_counter",), feature="audio_clock",
         dependencies=("startup",), object="startup.rel", backend="audio-lab",
         provenance=("Original V3 VBlank clock exposed by tools/music_rom.py; "
                     "audio driver reads it from the main thread, never runs in the ISR",),
         tests=("tests/test_music_rom.py",)),
    Unit("native_sfx", ("_ngc_sfx_play",), feature="native_sfx", dependencies=("audio",),
         object="audio.rel", backend="gbvm", provenance=("Explicit SFX timelines; Over Rev transport; GBVM priority slot",),
         tests=("tests/test_sfx_events.py",)),
    Unit("text_sound_cut", ("_ngc_sfx_cut",), feature="text_sound_cut", dependencies=("native_sfx",),
         object="audio.rel", backend="gbvm",
         provenance=("GBVM music_manager.h music_play_sfx with a NULL sample (Text Remove Sound): "
                     "Over Rev Sfx_Stop",),
         tests=("tests/test_text_sound.py",)),
    Unit("music_routine_queue", ("_ngc_music_routine_poll", "_ngc_music_routine_clear"),
         feature="music_routine_queue", dependencies=("audio",), object="audio.rel", backend="gbvm",
         provenance=("GBVM music_manager.c routine_queue (hUGETrackerRoutine, music_events_poll); "
                     "native stream FE 07 (ngpcraft/music_hold.py)",),
         tests=("tests/test_music_routine.py",)),
    Unit("music_mute", ("_ngc_music_mute",), feature="music_mute", dependencies=("audio",),
         object="audio.rel", backend="gbvm",
         provenance=("GBVM vm_music.c vm_music_mute / music_manager.c music_global_mute_mask on "
                     "Over Rev Bgm_Update voice ownership (s_bgm_ch_used_by_sfx, s_bgm_restore_ch)",),
         tests=("tests/test_mute_channel.py",)),
    Unit("audio_loading", ("_ngc_audio_set_loading",), feature="audio_loading",
         dependencies=("audio",), object="audio.rel", backend="gbvm",
         provenance=("Deferred music start after screen load: Over Rev/src/id_music.c",),
         tests=("tests/test_music_events.py",)),
    Unit("audio", ("_ngc_audio_init", "_ngc_music_play_track", "_ngc_music_stop_track", "_ngc_audio_service"),
         feature="native_audio", dependencies=("audio_clock",), backend="native-audio",
         provenance=("Selected frozen Over Rev/src/audio/sounds.c declarations: ngpcraft/music_library.py",
                     "Multi-song lifecycle adapted from Over Rev/src/id_music.c and src/id_snd.c",
                     "Only selected instrument/factory tables and stream extensions are emitted"),
         license="MIT; MUSIC_NOTICE.txt; Copyright (c) 2026 NGPC Template NgpCraft Tixu",
         tests=("tests/test_music_library.py",)),
)


def select_units(features, target="rom"):
    if target not in ("module", "rom"):
        raise CompileError("target must be module or rom")
    validate_registry()
    by_name = {unit.name: unit for unit in UNITS}
    selected = set()
    visiting = set()

    def visit(name):
        if name in visiting:
            raise CompileError("runtime unit dependency cycle: " + name)
        if name in selected:
            return
        if name not in by_name:
            raise CompileError("unknown runtime unit: " + name)
        visiting.add(name)
        for dependency in by_name[name].dependencies:
            visit(dependency)
        visiting.remove(name)
        selected.add(name)

    visit("cartridge" if target == "rom" else "game")
    for unit in UNITS:
        if unit.feature and unit.feature in features:
            visit(unit.name)
    # A translated engine plugin (translated.py) replaces a selected unit: same entry
    # points and object, its own public data, provenance and licence.
    swaps = {}
    if any(f.startswith("plugin:") for f in features):
        from dataclasses import replace
        from .translated import unit_replacements
        for feature, (name, data, provenance, licence) in unit_replacements().items():
            if feature in features:
                if name not in selected or name in swaps:
                    raise CompileError(f"{feature}: replaces unit {name}, not selected or already replaced")
                unit = by_name[name]
                swaps[name] = replace(unit, symbols=tuple(s for s in unit.symbols if s.startswith("_ngc_")) + data,
                                      provenance=provenance, license=licence,
                                      tests=("tests/test_translated_plugins.py",))
    return tuple(swaps[unit.name] if unit.name in swaps else unit for unit in UNITS if unit.name in selected)


def public_symbols(features, target="rom"):
    return {symbol for unit in select_units(features, target) for symbol in unit.symbols}


def hardware_source(unit):
    return (Path(__file__).parent / "runtime" / unit.source).read_text(encoding="utf-8")


def linked_objects(features):
    return {unit.object_name for unit in select_units(features, "rom")}


def validate_registry():
    names = set()
    symbols = set()
    destinations = set()
    for unit in UNITS:
        if unit.name in names or symbols.intersection(unit.symbols):
            raise CompileError("duplicate runtime unit or public symbol: " + unit.name)
        names.add(unit.name)
        symbols.update(unit.symbols)
        if not unit.provenance or not unit.license or not unit.tests:
            raise CompileError("incomplete runtime provenance/license/tests: " + unit.name)
        for source, destination in unit.files:
            if destination in destinations:
                raise CompileError("duplicate runtime destination: " + destination)
            destinations.add(destination)
    for unit in UNITS:
        if any(dependency not in names for dependency in unit.dependencies):
            raise CompileError("unknown runtime dependency: " + unit.name)


def emit_unit_files(units):
    runtime = Path(__file__).parent / "runtime"
    files = {destination: (runtime / source).read_text(encoding="utf-8")
             for unit in units for source, destination in unit.files}
    if "src/startup.c" in files and any(unit.name == "gbvm_text_cut" for unit in units):
        startup = files["src/startup.c"]
        startup = startup.replace('#include "game.h"', '#include "game.h"\nvoid ngc_ui_cut_vblank(void);')
        startup = startup.replace('    ++ngc_frame;', '    ngc_ui_cut_vblank();\n    ++ngc_frame;')
        files["src/startup.c"] = startup
    if "src/startup.c" in files and any(unit.name == "parallax" for unit in units):
        startup = files["src/startup.c"]
        startup = startup.replace('#include "game.h"', '#include "game.h"\nvoid ngc_parallax_vblank(void);\n'
                                  'extern unsigned char ngc_parallax_n;')
        startup = startup.replace('    ++ngc_frame;', '    ngc_parallax_vblank();\n    ++ngc_frame;')
        # RAM is random at power on: no slice until a scene loads its parallax rows.
        startup = startup.replace('    __asm("di");\n', '    __asm("di");\n    ngc_parallax_n = 0u;\n')
        if startup.count("ngc_parallax_n = 0u;") != 1 or startup.count("ngc_parallax_vblank();") != 1:
            raise CompileError("startup.c: parallax anchors changed")
        files["src/startup.c"] = startup
    if "src/startup.c" in files and any(unit.name == "gbvm_text_shared" for unit in units):
        startup = files["src/startup.c"]
        startup = startup.replace('#include "game.h"', '#include "game.h"\nvoid ngc_ui_vblank(void);')
        startup = startup.replace('    ++ngc_frame;', '    ngc_ui_vblank();\n    ++ngc_frame;')
        files["src/startup.c"] = startup
    if "src/startup.c" in files and any(unit.name == "frame_rate_30" for unit in units):
        startup = files["src/startup.c"]
        if startup.count("static volatile unsigned char ngc_frame;") != 1:
            raise CompileError("startup.c: VBlank counter declaration changed")
        files["src/startup.c"] = startup.replace("static volatile unsigned char ngc_frame;",
                                                 "volatile unsigned char ngc_frame;  /* ngc_vm_frame_end, 30 per second */")
    if "src/startup.c" in files and any(unit.name == "vblank_clock" for unit in units):
        startup = files["src/startup.c"]
        startup = startup.replace("typedef void __interrupt NgcInterrupt(void);",
                                  "typedef void __interrupt NgcInterrupt(void);\nvolatile unsigned short ngc_sys_time;")
        startup = startup.replace("    ++ngc_frame;", "    ++ngc_frame;\n    ++ngc_sys_time;")
        startup = startup.replace("    ngc_frame = 0u;", "    ngc_frame = 0u;\n    ngc_sys_time = 0u;")
        files["src/startup.c"] = startup
    return files


def describe_unit(unit):
    return {"id": unit.name, "public_symbols": list(unit.symbols),
            "dependencies": list(unit.dependencies), "object": unit.object_name,
            "files": [destination for source, destination in unit.files],
            "provenance": list(unit.provenance), "license": unit.license,
            "tests": list(unit.tests),
            "reason": "reachable feature: " + unit.feature if unit.feature else "required target ABI"}


def validate_export_selection(manifest):
    """Check selection before invoking a compiler, including module exports.

    Old 0.1 exports may omit descriptive runtime_units metadata. Their source
    selection is still checked against the trusted registry.
    """
    targets = {"ngpc-rom": "rom", "ngpc-c-module": "module"}
    target = manifest.get("target")
    if target not in targets:
        raise CompileError("unsupported export target")
    features = manifest.get("features")
    if not isinstance(features, list) or any(not isinstance(f, str) for f in features):
        raise CompileError("export features must be a list of strings")
    units = select_units(features, targets[target])
    expected = sorted({"src/" + unit.object_name[:-4] + ".c" for unit in units},
                      key=lambda path: (path != "src/cart.c", path))
    if manifest.get("sources") != expected:
        raise CompileError("export sources do not match selected runtime units")
    if "runtime_units" in manifest and manifest["runtime_units"] != [describe_unit(unit) for unit in units]:
        raise CompileError("runtime unit metadata changed; export again into a clean directory")
