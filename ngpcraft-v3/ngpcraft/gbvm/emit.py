"""Project -> NGPC ROM export on the ported GBVM (decision D-2).

src/game.c   the bytecode of the compiled scripts, and the frame loop of
             GBVM's core.c (run the scheduler while it is busy, once a frame);
src/gbvm.c   the GBVM functions vendored by tools/extract_gbvm_runtime.py,
             ONLY those the scripts reach, a decoder with ONLY the opcodes the
             scripts contain and vm_rpn with ONLY the RPN cases they use
             (AGENTS.md: no universal runtime), plus the NGPC pieces below;
GBVM_LICENSE.txt  GBVM's MIT license.

Scope today: init, actor updates, interactions, triggers and player
collision callbacks. Without EVENT_SWITCH_SCENE only the start scene is
emitted (other scenes are compiled to diagnose unsupported events); with it,
every scene reachable from the start scene (generate_scenes).
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

from ..compiler import CompileError
from ..project import load_model as load_gbstudio_model, option_long_press, frame_rate
from ..runtime_units import describe_unit, emit_unit_files, select_units
from .assembler import (CAMERA_PROPERTIES, ENGINE_MEMORY, NATIVES, SCROLL_BOUNDS, TEXT_MEMORY, UNREAD_MEMORY, assemble_bank,
                        load_model)
from .sprite_assets import DEFAULT_FRAME, sprite_assets, emote_assets, bank_assets, global_projectile_symbols
from .stack_budget import stack_budget
from .platform_callbacks import slots as callback_slots, support as callback_support, bodies as callback_bodies
from .platform_callbacks import natives as callback_natives
from .builder import js_object_keys
from .projectiles import FUNCTIONS as PROJECTILE_FUNCTIONS, atan2_source, hit_support
from ..parallax import share_timer0
from ..projectiles import scene_registry
from ..replace_tiles import replace_ops, tileset_options
from .custom_events import MAX_NESTED_SCRIPT_DEPTH
from .events import compile_scene_init, compile_actor_update, compile_actor_interaction, compile_trigger, compile_collision
from ..scene_topdown import build_interactions, input_source, validate_topdown
from ..scene_platform import platform_source, validate_platform
from ..scene_triggers import build_triggers
from ..scene_collisions import build_collisions, collision_groups, collision_capacity
from ..scene_genres import needs_player_collisions
from ..scene_updates import build_updates, context_capacity
from ..scene_build import build_engine_scene, engine_blocker
from ..scene_engine import AFTER_SCRIPTS, COMMIT, DECLARATIONS, FEATURES, engine_files, start_lines
from ..scene_graph import scene_graph
from ..scene_link import link_scenes
from ..project_music import music_options, select_music, attach_music
from ..palette_load import changed_slots
from ..projectile_runtime import SLOT_BASE

RUNTIME = Path(__file__).resolve().parents[1] / "runtime" / "gbvm_runtime.json"

# Opcode decoders, one per delivered instruction. GBVM's VM_STEP (vm.c)
# copies the operand bytes onto the stack, big-endian words, so a handler
# receives the fields in reverse order of the bytes (vm.i). Checked against
# the frozen model by tests/test_gbvm_backend.py.
DECODERS = {
    "VM_CAMERA_MOVE_TO": (("AFTER_LOCK", "SPEED", "IDX"), ("vm_camera_move_to",),
                           "cond = *ctx->PC++; n = *ctx->PC++; a = ngc_read_word(ctx); vm_camera_move_to(ctx, (INT16)a, n, cond);"),
    "VM_LOAD_PALETTE": (("OPTIONS", "MASK"), ("vm_load_palette",),
                        "cond = *ctx->PC++; n = *ctx->PC++; vm_load_palette(ctx, n, cond);"),
    "VM_CAMERA_SET_POS": (("IDX",), ("vm_camera_set_pos",),
                           "a = ngc_read_word(ctx); vm_camera_set_pos(ctx, (INT16)a);"),
    "VM_MUSIC_PLAY": (("TRACK", "TRACK_BANK"), ("vm_music_play",),
                      "a = ngc_read_word(ctx); bank = *ctx->PC++; vm_music_play(ctx, bank, a);"),
    "VM_MUSIC_STOP": ((), ("vm_music_stop",), "vm_music_stop(ctx);"),
    # EVENT_SET_MUSIC_ROUTINE (docs/M5_MUSIC_ROUTINE.md).
    "VM_MUSIC_ROUTINE": (("ADDR", "BANK", "ROUTINE"), ("vm_music_routine",),
                         "a = ngc_read_word(ctx); bank = *ctx->PC++; n = *ctx->PC++; "
                         "vm_music_routine(ctx, n, bank, (UBYTE *)(ngc_banks[bank] + a));"),
    # EVENT_MUTE_CHANNEL (docs/M5_MUTE_CHANNEL.md).
    "VM_MUSIC_MUTE": (("MASK",), ("vm_music_mute",), "vm_music_mute(ctx, *ctx->PC++);"),
    "VM_SFX_PLAY": (("PRIO", "MASK", "ADDR", "BANK"), ("vm_sfx_play",),
                    "cond = *ctx->PC++; n = *ctx->PC++; a = ngc_read_word(ctx); bank = *ctx->PC++; vm_sfx_play(ctx, bank, a, n, cond);"),
    # EVENT_TEXT_SET_SOUND_EFFECT (docs/M5_TEXT_SOUND.md): ADDR is a native effect index.
    "VM_SET_TEXT_SOUND": (("MASK", "ADDR", "BANK"), ("vm_set_text_sound",),
                          "n = *ctx->PC++; a = ngc_read_word(ctx); bank = *ctx->PC++; vm_set_text_sound(ctx, bank, a, n);"),
    "VM_REPLACE_TILE_XY": (("START_IDX", "TILEDATA", "TILEDATA_BANK", "Y", "X"), ("vm_replace_tile_xy",),
                           "a = ngc_read_word(ctx); b = ngc_read_word(ctx); bank = *ctx->PC++; cond = *ctx->PC++; "
                           "n = *ctx->PC++; vm_replace_tile_xy(ctx, n, cond, bank, b, (INT16)a);"),
    # dialogueFrameSetTiles only (emit_ui.frame_tilesets checks the form).
    "VM_REPLACE_TILE": (("LEN", "START_IDX", "TILEDATA", "TILEDATA_BANK", "TARGET_TILE_IDX"), ("vm_replace_tile",),
                        "n = *ctx->PC++; a = ngc_read_word(ctx); b = ngc_read_word(ctx); bank = *ctx->PC++; "
                        "c = ngc_read_word(ctx); vm_replace_tile(ctx, (INT16)c, bank, b, (INT16)a, n);"),
    "VM_PROJECTILE_LAUNCH": (("IDX", "TYPE"), ("vm_projectile_launch",),
                             "a = ngc_read_word(ctx); n = *ctx->PC++; vm_projectile_launch(ctx, n, (INT16)a);"),
    # Load Projectile Slot: bytes ADDR (global table, assembler symbol), BANK, SRC, DEST.
    "VM_PROJECTILE_LOAD_TYPE": (("ADDR", "BANK", "SRC_TYPE", "DEST_TYPE"), ("vm_projectile_load_type",),
                                "a = ngc_read_word(ctx); bank = *ctx->PC++; cond = *ctx->PC++; n = *ctx->PC++; "
                                "(void)bank; vm_projectile_load_type(ctx, n, cond, a);"),
    "VM_ACTOR_GET_ANGLE": (("DEST", "IDX"), ("vm_actor_get_angle",),
                          "a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_actor_get_angle(ctx, (INT16)b, (INT16)a);"),
    "VM_CALL_NATIVE": (("PTR", "BANK"), ("vm_call_native",),
                       "a = ngc_read_word(ctx); bank = *ctx->PC++; vm_call_native(ctx, bank, a);"),
    "VM_LOCK": ((), ("vm_lock",), "vm_lock(ctx);"),
    "VM_UNLOCK": ((), ("vm_unlock",), "vm_unlock(ctx);"),
    "VM_IDLE": ((), ("vm_idle",), "vm_idle(ctx);"),
    "VM_RESERVE": (("N",), ("vm_reserve",), "vm_reserve(ctx, (INT8)*ctx->PC++);"),
    "VM_SET_CONST": (("VAL", "IDX"), ("vm_set_const",),
                     "a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_set_const(ctx, (INT16)b, a);"),
    "VM_MEMSET": (("COUNT", "VALUE", "DEST"), ("vm_memset",),
                  "a = ngc_read_word(ctx); b = ngc_read_word(ctx); c = ngc_read_word(ctx); vm_memset(ctx, (INT16)c, (INT16)b, (INT16)a);"),
    "VM_GET_TLOCAL": (("IDXB", "IDXA"), ("vm_get_tlocal",),
                      "a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_get_tlocal(ctx, (INT16)b, (INT16)a);"),
    "VM_SET": (("IDXB", "IDXA"), ("vm_set",),
               "a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_set(ctx, (INT16)b, (INT16)a);"),
    "VM_SET_INDIRECT": (("IDXB", "IDXA"), ("vm_set_indirect",),
                        "a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_set_indirect(ctx, (INT16)b, (INT16)a);"),
    "VM_GET_INDIRECT": (("IDXB", "IDXA"), ("vm_get_indirect",),
                        "a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_get_indirect(ctx, (INT16)b, (INT16)a);"),
    "VM_PUSH_VALUE_IND": (("IDX",), ("vm_push_value_ind",),
                          "a = ngc_read_word(ctx); vm_push_value_ind(ctx, (INT16)a);"),
    "VM_INVOKE": (("PARAMS", "N", "ADDR", "BANK"), ("vm_invoke",),
                  "a = ngc_read_word(ctx); n = *ctx->PC++; b = ngc_read_word(ctx); bank = *ctx->PC++; "
                  "vm_invoke(ctx, bank, ngc_native(b), n, (INT16)a);"),
    "VM_SET_SPRITE_MODE": (("MODE",), ("vm_set_sprite_mode",), "vm_set_sprite_mode(ctx, *ctx->PC++);"),
    # A label is an address in the context's bank (assembler: offset in the
    # bank array); ngc_addr adds the bank's start.
    "VM_JUMP": (("LABEL",), ("vm_jump",), "a = ngc_read_word(ctx); vm_jump(ctx, ngc_addr(ctx, a));"),
    # Actor Effects loops: bytes N, LABEL (bank offset, like VM_JUMP), IDX.
    "VM_LOOP": (("N", "LABEL", "IDX"), ("vm_loop",),
                "n = *ctx->PC++; a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_loop(ctx, (INT16)b, ngc_addr(ctx, a), n);"),
    "VM_IF": (("N", "LABEL", "IDXB", "IDXA", "CONDITION"), ("vm_if",),
              "n = *ctx->PC++; a = ngc_read_word(ctx); b = ngc_read_word(ctx); c = ngc_read_word(ctx); "
              "cond = *ctx->PC++; vm_if(ctx, cond, (INT16)c, (INT16)b, ngc_addr(ctx, a), n);"),
    "VM_IF_CONST": (("N", "LABEL", "B", "IDXA", "CONDITION"), ("vm_if_const",),
                    "n = *ctx->PC++; a = ngc_read_word(ctx); b = ngc_read_word(ctx); c = ngc_read_word(ctx); "
                    "cond = *ctx->PC++; vm_if_const(ctx, cond, (INT16)c, (INT16)b, ngc_addr(ctx, a), n);"),
    "VM_RAND": (("LIMIT", "MIN", "IDX"), ("vm_rand",),
                "a = ngc_read_word(ctx); b = ngc_read_word(ctx); c = ngc_read_word(ctx); vm_rand(ctx, (INT16)c, b, a);"),
    "VM_INIT_RNG": (("IDX",), ("vm_init_rng",), "a = ngc_read_word(ctx); vm_init_rng(ctx, (INT16)a);"),
    # The SIZE cases (.dw value, label) follow; vm_switch reads them at PC.
    "VM_SWITCH": (("N", "SIZE", "IDX"), ("vm_switch",),
                  "n = *ctx->PC++; cond = *ctx->PC++; a = ngc_read_word(ctx); vm_switch(ctx, (INT16)a, cond, n);"),
    # vm_rpn reads its own operands (script_cmds: 0 argument bytes).
    "VM_RPN": ((), ("vm_rpn",), "vm_rpn(ctx);"),
    "VM_FADE": (("FLAGS",), ("fade_in", "fade_out", "fade_update", "fade_in_modal", "fade_out_modal", "vm_fade"),
                "vm_fade(ctx, *ctx->PC++);"),
    # Actor instructions (vm_actor.c), on the scene engine's actors (M2).
    "VM_ACTOR_ACTIVATE": (("ACTOR",), ("vm_actor_activate",),
                          "a = ngc_read_word(ctx); vm_actor_activate(ctx, (INT16)a);"),
    "VM_ACTOR_DEACTIVATE": (("ACTOR",), ("vm_actor_deactivate",),
                            "a = ngc_read_word(ctx); vm_actor_deactivate(ctx, (INT16)a);"),
    "VM_ACTOR_GET_DIR": (("DEST", "IDX"), ("vm_actor_get_dir",),
                         "a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_actor_get_dir(ctx, (INT16)b, (INT16)a);"),
    "VM_ACTOR_MOVE_TO": (("IDX",), ("vm_actor_move_to",),
                         "a = ngc_read_word(ctx); vm_actor_move_to(ctx, (INT16)a);"),
    "VM_ACTOR_GET_POS": (("IDX",), ("vm_actor_get_pos",),
                         "a = ngc_read_word(ctx); vm_actor_get_pos(ctx, (INT16)a);"),
    "VM_ACTOR_SET_POS": (("IDX",), ("vm_actor_set_pos",),
                         "a = ngc_read_word(ctx); vm_actor_set_pos(ctx, (INT16)a);"),
    "VM_ACTOR_SET_DIR": (("DIR", "ACTOR"), ("vm_actor_set_dir",),
                         "n = *ctx->PC++; a = ngc_read_word(ctx); vm_actor_set_dir(ctx, (INT16)a, n);"),
    "VM_ACTOR_SET_ANIM_FRAME": (("ACTOR",), ("vm_actor_set_anim_frame",),
                                "a = ngc_read_word(ctx); vm_actor_set_anim_frame(ctx, (INT16)a);"),
    "VM_ACTOR_SET_MOVE_SPEED": (("SPEED", "ACTOR"), ("vm_actor_set_move_speed",),
                                "n = *ctx->PC++; a = ngc_read_word(ctx); vm_actor_set_move_speed(ctx, (INT16)a, n);"),
    "VM_ACTOR_SET_ANIM_TICK": (("TICK", "ACTOR"), ("vm_actor_set_anim_tick",),
                               "n = *ctx->PC++; a = ngc_read_word(ctx); vm_actor_set_anim_tick(ctx, (INT16)a, n);"),
    "VM_ACTOR_SET_FLAGS": (("MASK", "FLAGS", "ACTOR"), ("vm_actor_set_flags",),
                           "n = *ctx->PC++; cond = *ctx->PC++; a = ngc_read_word(ctx); "
                           "vm_actor_set_flags(ctx, (INT16)a, cond, n);"),
    "VM_ACTOR_MOVE_TO_INIT": (("ATTR", "IDX"), ("vm_actor_move_to_init",),
                              "n = *ctx->PC++; a = ngc_read_word(ctx); vm_actor_move_to_init(ctx, (INT16)a, n);"),
    "VM_ACTOR_MOVE_TO_X": (("ATTR", "IDX"), ("vm_actor_move_to_x",),
                           "n = *ctx->PC++; a = ngc_read_word(ctx); vm_actor_move_to_x(ctx, (INT16)a, n);"),
    "VM_ACTOR_MOVE_TO_Y": (("ATTR", "IDX"), ("vm_actor_move_to_y",),
                           "n = *ctx->PC++; a = ngc_read_word(ctx); vm_actor_move_to_y(ctx, (INT16)a, n);"),
    "VM_ACTOR_MOVE_TO_XY": (("ATTR", "IDX"), ("vm_actor_move_to_xy",),
                            "n = *ctx->PC++; a = ngc_read_word(ctx); vm_actor_move_to_xy(ctx, (INT16)a, n);"),
    "VM_ACTOR_MOVE_TO_SET_DIR_X": (("IDX",), ("vm_actor_move_to_set_dir_x",),
                                   "a = ngc_read_word(ctx); vm_actor_move_to_set_dir_x(ctx, (INT16)a);"),
    "VM_ACTOR_MOVE_TO_SET_DIR_Y": (("IDX",), ("vm_actor_move_to_set_dir_y",),
                                   "a = ngc_read_word(ctx); vm_actor_move_to_set_dir_y(ctx, (INT16)a);"),
    "VM_ACTOR_SET_ANIM_MOVING": (("IDX",), ("vm_actor_set_anim_moving",),
                                 "a = ngc_read_word(ctx); vm_actor_set_anim_moving(ctx, (INT16)a);"),
    "VM_ACTOR_EMOTE": (("AVATAR", "AVATAR_BANK", "ACTOR"), ("vm_actor_emote",),
                        "a = ngc_read_word(ctx); n = *ctx->PC++; b = ngc_read_word(ctx); vm_actor_emote(ctx, (INT16)b, n, a);"),
    "VM_ACTOR_SET_SPRITESHEET": (("SHEET", "SHEET_BANK", "ACTOR"), ("vm_actor_set_spritesheet",),
                                "a = ngc_read_word(ctx); n = *ctx->PC++; b = ngc_read_word(ctx); vm_actor_set_spritesheet(ctx, (INT16)b, n, a);"),
    "VM_ACTOR_SET_ANIM_SET": (("OFFSET", "ACTOR"), ("vm_actor_set_anim_set",),
                             "a = ngc_read_word(ctx); b = ngc_read_word(ctx); vm_actor_set_anim_set(ctx, (INT16)b, a);"),
    "VM_SET_SPRITE_VISIBLE": (("MODE",), ("vm_set_sprites_visible",),
                              "vm_set_sprites_visible(ctx, *ctx->PC++);"),
    "VM_ACTOR_BEGIN_UPDATE": (("ACTOR",), ("vm_actor_begin_update",),
                              "a = ngc_read_word(ctx); vm_actor_begin_update(ctx, (INT16)a);"),
    # EVENT_ACTOR_SET_COLLISION_BOX: params ID, LEFT, RIGHT, TOP, BOTTOM (vm_actor.h act_set_bounds_t).
    "VM_ACTOR_SET_BOUNDS": (("IDX",), ("vm_actor_set_bounds",),
                            "a = ngc_read_word(ctx); vm_actor_set_bounds(ctx, (INT16)a);"),
    "VM_ACTOR_TERMINATE_UPDATE": (("ACTOR",), ("vm_actor_terminate_update",),
                                  "a = ngc_read_word(ctx); vm_actor_terminate_update(ctx, (INT16)a);"),
    "VM_ACTOR_MOVE_CANCEL": (("ACTOR",), ("vm_actor_move_cancel",),
                             "a = ngc_read_word(ctx); vm_actor_move_cancel(ctx, (INT16)a);"),
    "VM_POP": (("N",), ("vm_pop",), "(void)vm_pop(ctx, *ctx->PC++);"),
    # The SIZE parameter bytes follow (a scene far pointer): vm_raise records
    # where and skips them; script_runner_update returns RUNNER_EXCEPTION.
    "VM_RAISE": (("SIZE", "CODE"), ("vm_raise",),
                 "n = *ctx->PC++; cond = *ctx->PC++; vm_raise(ctx, cond, n);"),
    # EVENT_SAVE_DATA / EVENT_IF_SAVED_DATA (docs/M5_SAVE.md). Bytes of
    # VM_SAVE_PEEK: SLOT, COUNT, SOUR, DEST, RES (vm.i); one word is read in
    # the call, the only operand read there.
    "VM_POLL_LOADED": (("IDX",), ("vm_poll_loaded",), "a = ngc_read_word(ctx); vm_poll_loaded(ctx, (INT16)a);"),
    "VM_SAVE_PEEK": (("SLOT", "COUNT", "SOUR", "DEST", "RES"), ("vm_save_peek",),
                     "n = *ctx->PC++; a = ngc_read_word(ctx); b = ngc_read_word(ctx); c = ngc_read_word(ctx); "
                     "vm_save_peek(ctx, (INT16)ngc_read_word(ctx), (INT16)c, (INT16)b, a, n);"),
    # Threads (EVENT_THREAD_START / STOP, docs/M4_THREADS.md). Bytes NARGS,
    # HTHREAD, THREADPROC, BANK (vm.i); the procedure is an offset in its bank,
    # like VM_CALL_FAR. NARGS thread-local operands follow, read by the handler.
    "VM_BEGINTHREAD": (("NARGS", "HTHREAD", "THREADPROC", "BANK"), ("vm_beginthread",),
                       "n = *ctx->PC++; a = ngc_read_word(ctx); b = ngc_read_word(ctx); bank = *ctx->PC++; "
                       "vm_beginthread(ctx, bank, (UBYTE *)(ngc_banks[bank] + b), (INT16)a, n);"),
    "VM_TERMINATE": (("IDX",), ("script_terminate", "vm_terminate"),
                     "a = ngc_read_word(ctx); vm_terminate(ctx, (INT16)a);"),
    # EVENT_CLEAR_DATA (docs/M5_SAVE.md): a "slot cleared" journal record.
    "VM_SAVE_CLEAR": (("SLOT",), ("vm_save_clear",), "vm_save_clear(ctx, *ctx->PC++);"),
    # Custom scripts (EVENT_CALL_CUSTOM_EVENT): arguments, far call and return.
    "VM_PUSH_CONST": (("VAL",), ("vm_push",), "a = ngc_read_word(ctx); vm_push(ctx, a);"),
    "VM_PUSH_VALUE": (("IDX",), ("vm_push_value",), "a = ngc_read_word(ctx); vm_push_value(ctx, (INT16)a);"),
    "VM_PUSH_REFERENCE": (("IDX",), ("vm_push_reference",),
                          "a = ngc_read_word(ctx); vm_push_reference(ctx, (INT16)a);"),
    # The address is an offset in the target bank (assembler.py), like a label.
    "VM_CALL_FAR": (("ADDR", "BANK"), ("vm_call_far",),
                    "a = ngc_read_word(ctx); bank = *ctx->PC++; "
                    "vm_call_far(ctx, bank, (UBYTE *)(ngc_banks[bank] + a));"),
    # Input scripts (EVENT_SET_INPUT_SCRIPT): the script address is an offset
    # in its bank, like VM_CALL_FAR; bytes ADDR (big-endian), BANK, SLOT.
    "VM_CONTEXT_PREPARE": (("ADDR", "BANK", "SLOT"), ("vm_context_prepare",),
                           "a = ngc_read_word(ctx); bank = *ctx->PC++; n = *ctx->PC++; "
                           "vm_context_prepare(ctx, n, bank, (UBYTE *)(ngc_banks[bank] + a));"),
    # Bytes SLOT then MASK (vm.i); vm_input_attach(THIS, mask, slot).
    "VM_INPUT_ATTACH": (("SLOT", "MASK"), ("vm_input_attach",),
                        "cond = *ctx->PC++; n = *ctx->PC++; vm_input_attach(ctx, n, cond);"),
    "VM_INPUT_DETACH": (("MASK",), ("vm_input_detach",), "vm_input_detach(ctx, *ctx->PC++);"),
    # vm_input_wait rewinds PC onto itself until a button of MASK is newly held.
    "VM_INPUT_WAIT": (("MASK",), ("vm_input_wait",), "vm_input_wait(ctx, *ctx->PC++);"),
    # Timer scripts (EVENT_SET_TIMER_SCRIPT): bytes ADDR (big-endian), BANK, TIMER
    # for PREPARE, like VM_CONTEXT_PREPARE; INTERVAL then TIMER for SET.
    "VM_TIMER_PREPARE": (("ADDR", "BANK", "TIMER"), ("vm_timer_prepare",),
                         "a = ngc_read_word(ctx); bank = *ctx->PC++; n = *ctx->PC++; "
                         "vm_timer_prepare(ctx, n, bank, (UBYTE *)(ngc_banks[bank] + a));"),
    "VM_TIMER_SET": (("INTERVAL", "TIMER"), ("vm_timer_set",),
                     "cond = *ctx->PC++; n = *ctx->PC++; vm_timer_set(ctx, n, cond);"),
    "VM_TIMER_STOP": (("TIMER",), ("vm_timer_stop",), "vm_timer_stop(ctx, *ctx->PC++);"),
    "VM_TIMER_RESET": (("TIMER",), ("vm_timer_reset",), "vm_timer_reset(ctx, *ctx->PC++);"),
    # The label is an offset in the bank (ngc_addr), like VM_JUMP's.
    "VM_RATE_LIMIT_CONST": (("LABEL", "IDX", "N"), ("vm_rate_limit_const",),
                            "a = ngc_read_word(ctx); b = ngc_read_word(ctx); c = ngc_read_word(ctx); "
                            "vm_rate_limit_const(ctx, c, (INT16)b, ngc_addr(ctx, a));"),
    # Scene stack (EVENT_SCENE_PUSH_STATE ...): no operand.
    "VM_SCENE_PUSH": ((), ("vm_scene_push",), "vm_scene_push(ctx);"),
    "VM_SCENE_POP": ((), ("ngc_scene_raise", "vm_scene_pop"), "vm_scene_pop(ctx);"),
    "VM_SCENE_POP_ALL": ((), ("ngc_scene_raise", "vm_scene_pop_all"), "vm_scene_pop_all(ctx);"),
    "VM_SCENE_STACK_RESET": ((), ("vm_scene_stack_reset",), "vm_scene_stack_reset(ctx);"),
    # VM_RET_FAR and VM_RET_FAR_N are one opcode (N = 0 for VM_RET_FAR).
    "VM_RET_FAR": ((), ("vm_ret_far",), "vm_ret_far(ctx, *ctx->PC++);"),   # vm.i: .db OP, 0
    "VM_RET_FAR_N": (("N",), ("vm_ret_far",), "vm_ret_far(ctx, *ctx->PC++);"),
}
# Text instructions (vm_ui.c, docs/M2_TEXT.md): emit_ui.py.
from .emit_ui import (UI_DECODERS, UI_FUNCTIONS, overlay_cut_live, check_tiles, check_window_commands, frame_tilesets, modal_frame,
                      narrow_windows, ui_support)
from .emit_ui import DECLARATIONS as UI_DECLARATIONS
from ..ui_text import font_table, scene_ui
DECODERS.update(UI_DECODERS)
from . import link as link_cable                     # EVENT_LINK_* (docs/M6_LINK.md)
DECODERS.update(link_cable.DECODERS)
# Movement instructions and their ACTOR_ATTR_* operand (vm.i), read by
# move_features to emit only the movement code the scripts use.
MOVE_INSTRUCTIONS = ("VM_ACTOR_MOVE_TO_INIT", "VM_ACTOR_MOVE_TO_X", "VM_ACTOR_MOVE_TO_Y", "VM_ACTOR_MOVE_TO_XY",
                     "VM_ACTOR_MOVE_TO_SET_DIR_X", "VM_ACTOR_MOVE_TO_SET_DIR_Y", "VM_ACTOR_SET_ANIM_MOVING")
ACTOR_INSTRUCTIONS = {m for m in DECODERS if m.startswith("VM_ACTOR_")}
INPUT_FUNCTIONS = ("vm_context_prepare", "vm_input_attach", "vm_input_detach")
SCENE_STACK_API = (
    "/* Scene engine (scene_actors.c, keep_source): data_manager.c scene_stack. */\n"
    "void ngc_scene_push(void);\nconst unsigned char * ngc_scene_pop(unsigned char all);\n"
    "void ngc_scene_stack_reset(void);\n"
)
SCENE_INSTRUCTIONS = ("VM_SCENE_PUSH", "VM_SCENE_POP", "VM_SCENE_POP_ALL", "VM_SCENE_STACK_RESET")




def input_slots(text):
    """Input script slots a set of scripts prepares (VM_CONTEXT_PREPARE SLOT):
    each can hold one running thread plus one terminated, not yet reaped."""
    return sorted({int(m) for m in re.findall(r"^        VM_CONTEXT_PREPARE\s+(\d+),", text, re.M)})


INPUT_MACROS = ("VM_CONTEXT_PREPARE", "VM_INPUT_ATTACH", "VM_INPUT_DETACH")
TIMER_MACROS = ("VM_TIMER_PREPARE", "VM_TIMER_SET", "VM_TIMER_STOP", "VM_TIMER_RESET")
TIMER_FUNCTIONS = ("vm_timer_prepare", "vm_timer_set", "vm_timer_stop", "vm_timer_reset")


def timer_slots(text):
    """Timer slots a set of scripts prepares (VM_TIMER_PREPARE TIMER): each can
    hold one running thread plus one terminated, not yet reaped (events.c)."""
    return sorted({int(m) for m in re.findall(r"^        VM_TIMER_PREPARE\s+(\d+),", text, re.M)})


MUSIC_EVENTS_SOURCE = """/* music_manager.c music_init_events / music_events_update on the native
 * driver's routine queue (audio.c ngc_music_routine_poll, docs/M5_MUSIC_ROUTINE.md). */
static void music_init_events(UBYTE preserve) {
    UBYTE i;
    ngc_music_routine_clear();
    if (preserve) {
        for (i = 0; i < 4u; i++)
            music_events[i].handle = 0;
    } else {
        ngc_clear((UBYTE *)music_events, sizeof(music_events));
    }
}
void music_events_update(void) {
    unsigned char data;
    script_event_t * event;
    SCRIPT_CTX * ctx;
    while (ngc_music_routine_poll(&data)) {
        event = &music_events[data & 0x03u];
        if (!event->script_addr) return;
        if ((event->handle == 0) || ((event->handle & SCRIPT_TERMINATED) != 0)) {
            /* script_execute(..., 1, (uint16_t)(data >> 4)): one argument. */
            ctx = script_execute(event->script_bank, event->script_addr, &event->handle, 1);
            if (ctx) *(ctx->stack_ptr++) = (UWORD)(data >> 4);
        }
    }
}
"""


def routine_slots(text):
    """Music routines a set of scripts attaches (VM_MUSIC_ROUTINE ROUTINE & 3):
    each can hold one running thread plus one terminated, not yet reaped."""
    return sorted({int(m) & 3 for m in re.findall(r"^        VM_MUSIC_ROUTINE\s+(\d+),", text, re.M)})


def routine_scripts(text):
    """Routine scripts: script_execute pushes one argument (data >> 4)."""
    return {m: 1 for m in re.findall(r"^        VM_MUSIC_ROUTINE\s+\d+,\s*___bank_(\w+),", text, re.M)}


def macro_opcode(model, macro):
    """The opcode of a real instruction: a vm.i macro, or a composite whose
    first line is `.db OP_...` (VM_SIO_EXCHANGE: #<(SIZE << 1)); None for a
    composite that expands into other macros."""
    if macro in model["macros"]:
        return model["opcodes"][model["macros"][macro]["bytes"][0]["opcode"]]
    spec = model["composite_macros"][macro] if macro in model["composite_macros"] else None
    if spec and spec["lines"] and spec["lines"][0].startswith(".db OP_"):
        return model["opcodes"][spec["lines"][0][4:].split(",")[0].strip()]
    return None


def link_slot_warnings(texts, variables):
    """vm_sio_exchange copies SIZE consecutive script_memory words. GB Studio
    gives a slot only to the variables some event uses, in id order: from
    variable N the copy reaches the next allocated slots, not N+1... A transfer
    whose slots are not the consecutive variables (or run past the last one,
    into the VM stacks) is reported, located (docs/M6_LINK.md)."""
    by_slot = {i: name for name, i in variables.items()}
    found = []
    for text in texts:
        for src, dst, size in re.findall(r"^\s*VM_SIO_EXCHANGE\s+(VAR_\w+),\s*(VAR_\w+),\s*(\d+)\s*$", text, re.M):
            for role, name in (("sends", src), ("receives into", dst)):
                start = variables[name]
                slots = [by_slot[start + j] if start + j in by_slot else "beyond the last variable"
                         for j in range(int(size))]
                m = re.fullmatch(r"VAR_VARIABLE_(\d+)", name)
                expected = [f"VAR_VARIABLE_{int(m[1]) + j}" for j in range(int(size))] if m else None
                if slots != expected:
                    found.append(f"Link Transfer {role} {', '.join(slots)} (the next {size} variable slots from "
                                 f"{name}, as in GB Studio: only variables used by an event have one); "
                                 "use every variable of the packet in an event (docs/M6_LINK.md)")
    return list(dict.fromkeys(found))


def link_features(macros):
    """EVENT_LINK_*: the cable is opened; Link Transfer adds the exchange."""
    return ((["link_cable"] if {"VM_SIO_SET_MODE", "VM_SIO_EXCHANGE"} & set(macros) else []) +
            (["link_exchange"] if "VM_SIO_EXCHANGE" in macros else []))


def thread_sites(text):
    """EVENT_THREAD_START sites of a set of scripts (VM_BEGINTHREAD): each can
    hold one running thread plus one terminated, not yet reaped. A site run
    again before its thread ends starts another one; past the pool,
    script_execute returns NULL as in GBVM (docs/M4_THREADS.md)."""
    return len(re.findall(r"^        VM_BEGINTHREAD\s", text, re.M))


def after_scripts(tick_frames):
    """AFTER_SCRIPTS (scene_engine.py). At 30 per second (tick_frames 2) a tick
    covers two pictures: actors_update (animation, activation) runs for each,
    game_time advances by 2; camera and render once (docs/M2_PERF_PISTES.md)."""
    if tick_frames == 1:
        return AFTER_SCRIPTS
    update = "    ngc_actors_update(ngc_vm_locked());"
    out = []
    for line in AFTER_SCRIPTS:
        if line == update:
            out += [update, "    ++ngc_game_time;", update, "    --ngc_game_time;"]
        elif line == "    ++ngc_game_time;":
            out.append("    ngc_game_time += 2u;")
        else:
            out.append(line)
    if out == AFTER_SCRIPTS or len(out) != len(AFTER_SCRIPTS) + 3:
        raise CompileError("frame_rate 30: AFTER_SCRIPTS changed")
    return out


def scene_frame_lines(text):
    """AFTER_SCRIPTS without game_time, with core.c's ui_update. GBVM calls it
    after actors_render, but the Game Boy hides sprites below the window from
    WY, which the VBlank takes from win_pos_y after ui_update: the window the
    sprites are cut by is the updated one. The scene engine cuts sprites when
    rendering (R2), so ui_update runs just before actors_render; actors read
    nothing of the UI (docs/M4_TEXT_MODES.md)."""
    lines = list(AFTER_SCRIPTS[:-1])
    if text:
        render = lines.index("    ngc_actors_render();")
        lines.insert(render, "    ngc_ui_update();")
    return lines


def picture_frames(frame, tick_frames):
    """One GBVM frame of the tick (scripts, buttons, state, camera, actors, text,
    game_time). At 30 per second the tick is two pictures: the whole frame runs
    twice, as in GB Studio, and only the second renders into the shadow OAM
    (the picture shown); docs/M2_PERF_PISTES.md section 3."""
    if tick_frames == 1:
        return frame
    render = "    ngc_actors_render();"
    first = [line for line in frame if line != render]
    if len(first) != len(frame) - 1:
        raise CompileError("frame_rate 30: the frame must render exactly once")
    return first + frame


def input_game_lines(topdown, inputs, text=False, timers=False, music=False):
    """game.c pieces for the buttons: (declarations, init, tick). The tick is
    core.c process_VM after the scripts: input_update; then, VM not locked,
    events_update when a button is down (input scripts, first) and the state
    update (topdown). Without input scripts, the tick line is unchanged.
    text: ui.c reads the buttons too (fast-forward, modal waits).
    topdown: the scene type states, "topdown", "platform" or a list of them
    (True = "topdown"); with several scenes each update dispatches on ngc_scene
    (scene_link.py) and only the current scene's state runs.
    music: music routines (docs/M5_MUSIC_ROUTINE.md), core.c music_events_update
    every frame after the timers."""
    if timers or music:
        # core.c: after the state update, VM not locked; timers every 16th frame.
        if timers:
            decls, init, tick = (input_game_lines(topdown, True, text) if inputs
                                 else input_game_lines(topdown, False, True))
        else:
            decls, init, tick = input_game_lines(topdown, inputs, text)
        decls = decls + (["void timers_update(void);"] if timers else []) + \
            (["void music_events_update(void);"] if music else [])
        lines = (["        if (((unsigned char)ngc_game_time & 0x0Fu) == 0u) timers_update();"] if timers else []) + \
            (["        music_events_update();"] if music else [])
        if "    if (!ngc_vm_locked()) {" in tick:
            tick = tick[:-1] + lines + ["    }"]
        else:
            states = [line for line in tick if line.startswith("    if (!ngc_vm_locked()) ngc_")]
            tick = [line for line in tick if line not in states] + ["    if (!ngc_vm_locked()) {"] + \
                [line.replace("    if (!ngc_vm_locked()) ", "        ") for line in states] + lines + ["    }"]
        return decls, init, tick
    if not topdown and not inputs:
        if text:
            return (["void ngc_input_init(void);", "void ngc_input_update(void);"], ["    ngc_input_init();"],
                    ["    ngc_input_update();"])
        return [], [], []
    states = (["topdown"] if topdown is True else [topdown] if isinstance(topdown, str)
              else list(topdown) if topdown else [])
    decls = ["void ngc_input_init(void);", "void ngc_input_update(void);"]
    for state in states:
        decls += [f"void ngc_{state}_init(void);", f"void ngc_{state}_update(void);"]
        if state in BOOT_STATES:
            decls.append(f"void ngc_{state}_boot(void);")
    if inputs:
        decls += ["void events_update(void);", "extern unsigned char ngc_joy;"]
    init = [] if states else ["    ngc_input_init();"]     # start_lines inits it with the state
    if not inputs:
        return decls, init, ["    ngc_input_update();",
                             *[f"    if (!ngc_vm_locked()) ngc_{state}_update();" for state in states]]
    tick = ["    ngc_input_update();", "    if (!ngc_vm_locked()) {",
            "        if (ngc_joy != 0u) events_update();   /* core.c: must be the first */",
            *[f"        ngc_{state}_update();" for state in states], "    }"]
    return decls, init, tick


# Scene type states with state code (GBVM src/states/*.c), in dispatch order.
SCENE_STATES = ("topdown", "platform", "shmup", "pointnclick", "adventure")
# States with engine fields set once at boot (script_engine_init): ngc_<state>_boot.
BOOT_STATES = ("platform", "shmup", "adventure")


class SceneState(tuple):
    """engine_files `topdown` tuple of a scene whose state code is generated
    from a vendored GBVM state file: (None, None, option, source(w, h,
    interactions, triggers, hits, view_h)[, statesOrder]). `state` names it;
    a plain tuple (grid, mask, option) is the top-down state."""
    def __new__(cls, state, items):
        self = super().__new__(cls, items)
        self.state = state
        return self


def scene_states(used):
    """Scene type states present in a multi-scene export, for input_game_lines."""
    return [s for s in SCENE_STATES if used[s]]


def platform_state(project, scene, callbacks=False):
    """engine_files `topdown` tuple of a platformer scene: (None, None, option,
    platform source(width, height)[, statesOrder when animation sets load])."""
    from ..scene_platform import states_order, uses_anim_states
    return SceneState("platform", (None, None, option_long_press(project),
                      lambda w, h, inter, trig, hit, view_h=None: platform_source(project, scene, w, h, inter, trig, hit, callbacks),
                      *([states_order(project)] if uses_anim_states(project) else [])))


def genre_state(project, scene, callbacks=False):
    """engine_files `topdown` tuple of a Shoot'em up or Point & Click scene
    (docs/ETUDE_M5_SHMUP_PNC.md): GBVM shmup.c / pointnclick.c vendored; an
    Adventure scene (adventure.c), with statesOrder when it loads animation sets."""
    from ..scene_genres import genre_source, state_name, uses_anim_states
    from ..scene_platform import states_order
    state = state_name(scene)
    return SceneState(state, (None, None, option_long_press(project),
                      lambda w, h, inter, trig, hit, view_h=152: genre_source(project, scene, w, h, inter, trig, hit, view_h,
                                                                              callbacks),
                      *([states_order(project)] if uses_anim_states(project, scene) else [])))


def scene_state(project, scene, callbacks=False, adventure_callbacks=False):
    """The `topdown` tuple of a scene with state code, None otherwise."""
    if callbacks and scene["type"] != "PLATFORM":
        raise CompileError("platform state callbacks require a PLATFORM scene")
    if adventure_callbacks and scene["type"] != "ADVENTURE":
        raise CompileError("adventure state callbacks require an ADVENTURE scene")
    if scene["type"] == "TOPDOWN":
        grid = engine_field(project, "topdown_grid")
        mask = validate_topdown(project, scene, grid, engine_field(project, "INPUT_TOPDOWN_INTERACT"))
        return (grid, mask, option_long_press(project))
    if scene["type"] == "PLATFORM":
        validate_platform(project, scene)
        return platform_state(project, scene, callbacks)
    if scene["type"] in ("SHMUP", "POINTNCLICK", "ADVENTURE"):
        return genre_state(project, scene, adventure_callbacks)
    return None


def state_of(topdown):
    """Scene type state carried by the engine_files `topdown` tuple."""
    return None if topdown is None else getattr(topdown, "state", "topdown")


def validate_logo_scripts(scene, macros, text):
    """Bounded LOGO contract (M4_LOGO): no inherited player bounds or sheet.

    Scene switch uses SET_POS/SET_DIR on actor zero before raising the
    exception. These and their reads do not need a sprite or collision box.
    """
    if scene["type"] != "LOGO":
        return
    allowed = {"VM_ACTOR_SET_POS", "VM_ACTOR_SET_DIR", "VM_ACTOR_GET_POS",
               "VM_ACTOR_GET_DIR", "VM_ACTOR_GET_ANGLE"}
    unsupported = sorted((ACTOR_INSTRUCTIONS & set(macros)) - allowed)
    if unsupported:
        raise CompileError(f"scene {scene['name']}: LOGO actor instructions are not delivered: {', '.join(unsupported)}")
    if set(macros) & set(UI_DECODERS):
        raise CompileError(f"scene {scene['name']}: LOGO overlay/dialogue is not delivered yet (fullscreen LCD mode)")
    if re.search(r"\bEXCEPTION_(SAVE|LOAD)\b", text):
        raise CompileError(f"scene {scene['name']}: LOGO save/load is not delivered yet")
# RPN cases that call library functions GBVM takes from GBDK (atan2, isqrt,
# randw, abs): not delivered.
RPN_NOT_DELIVERED = { "VM_OP_ISQRT", "VM_OP_ABS"}

# Scene engine API (scene_actors.c) for the vm_actor.c handlers below.
ACTOR_API = (
    "/* Scene engine (scene_actors.c): what vm_actor.c does to an actor, on the\n"
    " * NGPC actor arrays. VM_REF_TO_PTR as in vm.h. */\n"
    "#ifndef VM_REF_TO_PTR\n"
    "#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))\n"
    "#endif\n"
    "void ngc_actor_activate(unsigned char i);\nvoid ngc_actor_deactivate(unsigned char i);\n"
    "void ngc_actor_set_pos(unsigned char i, unsigned short x, unsigned short y);\n"
    "void ngc_actor_set_dir(unsigned char i, unsigned char dir, unsigned char moving);\n"
    "void ngc_actor_set_frame_offset(unsigned char i, unsigned char offset);\n"
    "void ngc_actor_set_flags(unsigned char i, unsigned char flags, unsigned char mask);\n"
    "void ngc_actor_set_anim_tick(unsigned char i, unsigned char tick);\n"
    "void ngc_actor_set_move_speed(unsigned char i, unsigned char speed);\n"
    "void ngc_actor_move_init(unsigned char i, unsigned short *p, unsigned char attr);\n"
    "unsigned char ngc_actor_move_x(unsigned char i, unsigned short *p, unsigned char attr);\n"
    "unsigned char ngc_actor_move_y(unsigned char i, unsigned short *p, unsigned char attr);\n"
    "unsigned char ngc_actor_move_xy(unsigned char i, unsigned short *p, unsigned char attr);\n"
    "void ngc_actor_move_dir_x(unsigned char i, const unsigned short *p);\n"
    "void ngc_actor_move_dir_y(unsigned char i, const unsigned short *p);\n"
    "void ngc_actor_set_anim_moving(unsigned char i);\n"
    "unsigned char ngc_actor_move_cancel(unsigned char i);\n"
)


def _move_step(axis):
    """vm_actor_move_to_x / _y / _xy, VM side: waitable every frame; the PC goes
    back to the instruction while the actor has not arrived (scene_actors.c
    ngc_actor_move_<axis> returns 1)."""
    return (ACTOR_API +
            f"static void vm_actor_move_to_{axis}(SCRIPT_CTX * THIS, INT16 idx, UBYTE attr) {{\n"
            "    UWORD * params;\n"
            "    THIS->waitable = 1;\n"
            "    params = VM_REF_TO_PTR(idx);\n"
            f"    if (ngc_actor_move_{axis}((UBYTE)params[0], params + 1, attr))\n"
            "        THIS->PC -= (INSTRUCTION_SIZE + sizeof(idx) + sizeof(attr));\n}\n")

NGPC_FUNCTIONS = {
    "vm_music_play": "static void vm_music_play(SCRIPT_CTX *THIS, UBYTE bank, UWORD track)\n{\n    (void)THIS; (void)bank;\n    ngc_music_play_track(track);\n}\n",
    "vm_sfx_play": "static void vm_sfx_play(SCRIPT_CTX *THIS, UBYTE bank, UWORD id, UBYTE mask, UBYTE priority)\n{\n    (void)THIS; (void)bank; (void)mask; ngc_sfx_play(id, priority);\n}\n",
    "vm_music_stop": "static void vm_music_stop(SCRIPT_CTX *THIS)\n{\n    (void)THIS;\n    ngc_music_stop_track();\n}\n",
    # vm_scene.c on the scene engine's stack (keep_source): same order and effects.
    "vm_scene_push": SCENE_STACK_API +
        "static void vm_scene_push(SCRIPT_CTX * THIS) {\n    (void)THIS;\n    ngc_scene_push();\n}\n",
    "ngc_scene_raise": SCENE_STACK_API +
        "/* vm_scene.c raise_change_scene_exception: the popped item's far pointer\n"
        " * (bank, scene index); ngc_scene_pop has restored PLAYER.pos and dir. An\n"
        " * empty stack (GBVM would underflow) raises nothing. */\n"
        "static void ngc_scene_raise(const UBYTE * scene) {\n"
        "    if (!scene) return;\n"
        "    vm_exception_code = EXCEPTION_CHANGE_SCENE_VALUE;\n"
        "    vm_exception_params_length = 3;\n"
        "    vm_exception_params_bank = 1;\n"
        "    vm_exception_params_offset = scene;\n}\n",
    "vm_scene_pop": "static void vm_scene_pop(SCRIPT_CTX * THIS) {\n    (void)THIS;\n"
                    "    ngc_scene_raise(ngc_scene_pop(0u));\n}\n",
    "vm_scene_pop_all": "static void vm_scene_pop_all(SCRIPT_CTX * THIS) {\n    (void)THIS;\n"
                        "    ngc_scene_raise(ngc_scene_pop(1u));\n}\n",
    "vm_scene_stack_reset": SCENE_STACK_API +
        "static void vm_scene_stack_reset(SCRIPT_CTX * THIS) {\n    (void)THIS;\n    ngc_scene_stack_reset();\n}\n",
    "vm_set_sprite_mode": (
        "/* NGPC (docs/ECARTS_GBSTUDIO.md section 3): hardware sprites are 8x8 only; an\n"
        " * 8x16 sheet is split at build time. There is no mode register to set. */\n"
        "static void vm_set_sprite_mode(SCRIPT_CTX * THIS, UBYTE mode) {\n"
        "    (void)THIS; (void)mode;\n}\n"),
    # vm_actor.c, transcribed on the scene engine API (same order and effects).
    "vm_actor_activate": ACTOR_API +
        "static void vm_actor_activate(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UBYTE * n_actor = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_activate(*n_actor);\n}\n",
    "vm_actor_deactivate": ACTOR_API +
        "static void vm_actor_deactivate(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UBYTE * n_actor = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_deactivate(*n_actor);\n}\n",
    "vm_actor_get_dir": ACTOR_API +
        "unsigned char ngc_actor_get_dir(unsigned char i);\n"
        "static void vm_actor_get_dir(SCRIPT_CTX * THIS, INT16 idx, INT16 dest) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    UWORD * A = VM_REF_TO_PTR(dest);\n"
        "    *A = ngc_actor_get_dir((UBYTE)params[0]);\n}\n",
    "vm_actor_move_to": ACTOR_API +
        "unsigned char ngc_actor_push_step(unsigned char i, unsigned short *p, unsigned char *flags);\n"
        "/* vm_actor.c legacy state machine: waitable, initialise on flags=0. */\n"
        "static void vm_actor_move_to(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params;\n"
        "    THIS->waitable = 1;\n"
        "    params = VM_REF_TO_PTR(idx);\n"
        "    if (ngc_actor_push_step((UBYTE)params[0], params + 1, &THIS->flags))\n"
        "        THIS->PC -= (INSTRUCTION_SIZE + sizeof(idx));\n}\n",
    "vm_actor_get_pos": ACTOR_API +
        "void ngc_actor_get_pos(unsigned char i, unsigned short *p);\n"
        "/* vm_actor.c: act_set_pos_t ID cast to UBYTE, then X and Y. */\n"
        "static void vm_actor_get_pos(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_get_pos((UBYTE)params[0], params);\n}\n",
    "vm_actor_set_pos": ACTOR_API +
        "/* act_set_pos_t: ID, X, Y (words) */\n"
        "static void vm_actor_set_pos(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_pos((UBYTE)params[0], params[1], params[2]);\n}\n",
    "vm_actor_set_dir": ACTOR_API +
        "static void vm_actor_set_dir(SCRIPT_CTX * THIS, INT16 idx, UBYTE dir) {\n"
        "    UBYTE * n_actor = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_dir(*n_actor, dir, 0u);  /* actor_set_dir(actor, dir, FALSE) */\n}\n",
    "vm_actor_set_anim_frame": ACTOR_API +
        "/* act_set_frame_t: ID, FRAME (words) */\n"
        "static void vm_actor_set_anim_frame(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_frame_offset((UBYTE)params[0], (UBYTE)params[1]);\n}\n",
    "vm_actor_set_move_speed": ACTOR_API +
        "static void vm_actor_set_move_speed(SCRIPT_CTX * THIS, INT16 idx, UBYTE speed) {\n"
        "    UBYTE * n_actor = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_move_speed(*n_actor, speed);\n}\n",
    "vm_actor_set_anim_tick": ACTOR_API +
        "static void vm_actor_set_anim_tick(SCRIPT_CTX * THIS, INT16 idx, UBYTE tick) {\n"
        "    UBYTE * n_actor = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_anim_tick(*n_actor, tick);\n}\n",
    "vm_actor_set_flags": ACTOR_API +
        "static void vm_actor_set_flags(SCRIPT_CTX * THIS, INT16 idx, UBYTE flags, UBYTE mask) {\n"
        "    UBYTE * n_actor = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_flags(*n_actor, flags, mask);\n}\n",
    "vm_actor_emote": ACTOR_API +
        "unsigned char ngc_actor_emote_step(unsigned char i, unsigned short emote, unsigned char *flags);\n"
        "static void vm_actor_emote(SCRIPT_CTX * THIS, INT16 idx, UBYTE bank, UWORD emote) {\n"
        "    UBYTE *n_actor = VM_REF_TO_PTR(idx); (void)bank;\n"
        "    if (ngc_actor_emote_step(*n_actor, emote, &THIS->flags)) {\n"
        "        THIS->waitable = 1; THIS->PC -= 6;\n"
        "    }\n}\n",
    "vm_actor_set_spritesheet": ACTOR_API +
        "void ngc_actor_set_spritesheet(unsigned char i, unsigned short sheet);\n"
        "static void vm_actor_set_spritesheet(SCRIPT_CTX * THIS, INT16 idx, UBYTE bank, UWORD sheet) {\n"
        "    UBYTE *n_actor = VM_REF_TO_PTR(idx); (void)bank;\n"
        "    ngc_actor_set_spritesheet(*n_actor, sheet);\n}\n",
    "vm_actor_set_anim_set": ACTOR_API +
        "void ngc_actor_set_anim_set(unsigned char i, unsigned short set);\n"
        "/* vm_actor.c: load_animations, then actor_reset_anim (keep current direction). */\n"
        "static void vm_actor_set_anim_set(SCRIPT_CTX * THIS, INT16 idx, UWORD offset) {\n"
        "    UBYTE * n_actor = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_anim_set(*n_actor, offset);\n}\n",
    # vm_gameboy.c vm_set_sprites_visible + interrupts.c: the VBlank applies
    # hide_sprites every frame (LCDC OBJ off). K2GE has no global sprite switch:
    # while hidden, game.c clears every sprite's priority (PR.C = 0) instead of
    # copying the frame; the next copy restores them (docs/M4_BATCH2_EVENTS.md).
    "vm_set_sprites_visible":
        "UBYTE ngc_sprites_hidden;\n"
        "static void vm_set_sprites_visible(SCRIPT_CTX * THIS, UBYTE mode) {\n"
        "    (void)THIS;\n"
        "    ngc_sprites_hidden = mode;\n}\n"
        "void ngc_sprites_hide_all(void) {\n"
        "    volatile UBYTE * attr = (volatile UBYTE *)0x8801;\n"
        "    UBYTE i;\n"
        "    for (i = 64u; i != 0u; --i) { *attr = 0u; attr += 4; }\n}\n",
    "vm_actor_begin_update": ACTOR_API +
        "void ngc_actor_start_update(unsigned char i);\n"
        "/* vm_actor.c: script_execute the update script when it is terminated. */\n"
        "static void vm_actor_begin_update(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_start_update((UBYTE)params[0]);\n}\n",
    "vm_actor_set_bounds": ACTOR_API +
        "void ngc_actor_set_bounds(unsigned char i, short l, short r, short t, short b);\n"
        "/* vm_actor.c vm_actor_set_bounds: the actor's collision box (1/32 px). */\n"
        "static void vm_actor_set_bounds(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    INT16 * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_bounds((UBYTE)params[0], params[1], params[2], params[3], params[4]);\n}\n",
    "vm_actor_terminate_update": ACTOR_API +
        "void ngc_actor_stop_update(unsigned char i);\n"
        "/* vm_actor.c: terminate only hscript_update; actor flags stay intact. */\n"
        "static void vm_actor_terminate_update(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_stop_update((UBYTE)params[0]);\n}\n",
    "vm_actor_move_cancel": ACTOR_API +
        "static void vm_actor_move_cancel(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UBYTE * n_actor;\n"
        "    THIS->waitable = 1;\n"
        "    n_actor = VM_REF_TO_PTR(idx);\n"
        "    if (ngc_actor_move_cancel(*n_actor))\n"
        "        THIS->PC -= (INSTRUCTION_SIZE + sizeof(idx));\n}\n",
    # act_move_to_t: ID, X, Y (words); the split instructions take ATTR as operand.
    "vm_actor_move_to_init": ACTOR_API +
        "static void vm_actor_move_to_init(SCRIPT_CTX * THIS, INT16 idx, UBYTE attr) {\n"
        "    UWORD * params;\n"
        "    THIS->waitable = TRUE;\n"
        "    params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_move_init((UBYTE)params[0], params + 1, attr);\n}\n",
    "vm_actor_move_to_x": _move_step("x"),
    "vm_actor_move_to_y": _move_step("y"),
    "vm_actor_move_to_xy": _move_step("xy"),
    "vm_actor_move_to_set_dir_x": ACTOR_API +
        "static void vm_actor_move_to_set_dir_x(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_move_dir_x((UBYTE)params[0], params + 1);\n}\n",
    "vm_actor_move_to_set_dir_y": ACTOR_API +
        "static void vm_actor_move_to_set_dir_y(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_move_dir_y((UBYTE)params[0], params + 1);\n}\n",
    "vm_actor_set_anim_moving": ACTOR_API +
        "static void vm_actor_set_anim_moving(SCRIPT_CTX * THIS, INT16 idx) {\n"
        "    UWORD * params = VM_REF_TO_PTR(idx);\n"
        "    ngc_actor_set_anim_moving((UBYTE)params[0]);\n}\n",
}


def move_features(text, model):
    """None if no script moves an actor, else the movement features the
    ACTOR_ATTR_* operands ask for (scene_actors.MOVE_FEATURES)."""
    from .assembler import _Expr
    found, features = False, set()
    for line in text.split("\n"):
        m = re.fullmatch(r"        (VM_ACTOR_[A-Z_]+)\s+(.*)", line.split(";", 1)[0].rstrip())
        if m and m[1] == "VM_ACTOR_MOVE_TO":
            # Closed event catalogue: actorPush writes CHECK_COLL (0x22) into
            # the local ATTR word; this legacy opcode has no ATTR operand.
            found = True
            features |= {"walls", "actors"}
            continue
        if not m or m[1] not in MOVE_INSTRUCTIONS:
            continue
        found = True
        args = [a.strip() for a in m[2].split(",")]
        if len(args) == 2:
            attr = _Expr(re.sub(r"\^!(\w+)!", r"\1", args[1]), model["constants"], m[1]).value()
            features |= {name for name, bit in (("walls", 0x02), ("actors", 0x20), ("relative", 0x18))
                         if attr & bit}
    return tuple(sorted(features)) if found else None

FRAME_WAIT_SUPPORT = r"""/* NGPC: wait for the start of the next vertical blank. RAS.V (0x8009) is the
 * line being drawn, readable during the blank, which starts at line 152
 * (K2GE; docs corpus final/SPRMUX_HW.md section 8). GB: wait_vbl_done. */
static void wait_vbl_done(void)
{
    while (*(volatile UBYTE *)0x8009 >= 152u) { }
    while (*(volatile UBYTE *)0x8009 < 152u) { }
}
/* core.c ends every frame with wait_vbl_done(). The startup frame counter
 * alone cannot distinguish a timely tick from a tick that crossed VBlank.
 * End normal, modal and scene-load ticks at the next blank before their
 * next commit; a long tick misses a picture but cannot commit mid-picture. */
#if NGC_TICK_FRAMES == 2
/* startup.c VBlank counter, public in a 30 per second export (runtime_units.py). */
extern volatile unsigned char ngc_frame;
static UBYTE ngc_tick_frame;
#endif
void ngc_vm_frame_end(void)
{
    /* Every scene frame ends at the next blank, as core.c does. A long
     * non-modal tick can also cross a VBlank; the startup counter alone
     * would then immediately begin a commit in the visible picture. */
    wait_vbl_done();
#if NGC_TICK_FRAMES == 2
    /* Fixed cadence: the next commit two pictures after the previous one.
     * A tick longer than two pictures commits at the next blank (counted
     * by the probes); at power-on the random previous value only shortens
     * the first wait. */
    while ((UBYTE)(ngc_frame - ngc_tick_frame) < 2u) wait_vbl_done();
    ngc_tick_frame = ngc_frame;
#endif
}
"""

# Fade manager, NGPC side (fade_manager.c keeps the logic; these replace its
# Game Boy hardware parts). Globals as in fade_manager.c:13-23.
FADE_SUPPORT_TEMPLATE = r"""/* fade_manager.c globals. */
static UBYTE fade_running;
static UBYTE fade_frames_per_step;
static UBYTE fade_timer;
static UBYTE fade_style;
static const UBYTE fade_speeds[] = {0x0, 0x1, 0x3, 0x7, 0xF, 0x1F, 0x3F};
static UBYTE fade_frame;
static FADE_DIRECTION fade_direction;
#define fade_isfading() (fade_running)
/* NGPC palette RAM, RGB444 (colours converted at build time, ECARTS
 * section 4): sprite palettes 0x8200, SCR1 0x8280, SCR2 0x8300 (3 x 16 x 4
 * colours), then the backdrop 0x83E0. The scene loader fills it; the fade
 * writes it, stepped. */
static UWORD ngc_palette_shadow[193];
/* Fade step -> colour (docs/ECARTS_GBSTUDIO.md section 13, decided 2026-09-29).
 * GB Studio (CGBFadeToWhiteStep / CGBFadeToBlackStep) sets or clears bits of
 * each 5-bit channel: on 4 bits only 4 levels remain and most of the change
 * is in the first step. NGPC: the same steps at the same times, each step
 * moving every 4-bit channel by an equal amount towards white or black
 * (tables ngc_fade_white / ngc_fade_black, generated by emit.fade_tables).
 * A step must reach the palette RAM during the vertical blank (47 lines),
 * or the picture is cut: computing 193 colours takes about 130 lines on the
 * TLCS-900 (measured 2026-09-29, cut at line 82). So the next step of the
 * running fade is computed ahead into ngc_palette_staged while the screen is
 * drawn, and a step only copies 193 words. Same steps, same frames. */
%(tables)s
static UWORD ngc_palette_staged[193];
static UBYTE ngc_staged_step;          /* 0xFF: nothing staged */
static UBYTE ngc_staged_style;
static void ngc_fade_compute(UBYTE index)
{
    const UBYTE * lut;
    const UWORD * src;
    UWORD * dst;
    UWORD c;
    UBYTE n;
    lut = fade_style ? ngc_fade_black[index] : ngc_fade_white[index];
    src = ngc_palette_shadow;
    dst = ngc_palette_staged;
    for (n = 193u; n != 0u; --n) {
        c = *src++;
        *dst++ = (UWORD)(lut[c & 0x0Fu] | ((UWORD)lut[(c >> 4) & 0x0Fu] << 4) | ((UWORD)lut[(c >> 8) & 0x0Fu] << 8));
    }
    ngc_staged_step = index;
    ngc_staged_style = fade_style;
}
static void ApplyPaletteChangeColor(UBYTE index)
{
    const UWORD * src;
    volatile UWORD * dst;
    UBYTE n;
    if (ngc_staged_step != index || ngc_staged_style != fade_style) ngc_fade_compute(index);
    src = ngc_palette_staged;
    dst = (volatile UWORD *)0x8200;
    for (n = 192u; n != 0u; --n) *dst++ = *src++;
    *(volatile UWORD *)0x83E0 = *src;
    /* Stage the step the running fade shows next (fade_update). */
    if (fade_direction == FADE_IN) {
        if (index > FADED_IN_FRAME) ngc_fade_compute((UBYTE)(index - 1u));
    } else if (index < FADED_OUT_FRAME) {
        ngc_fade_compute((UBYTE)(index + 1u));
    }
}
"""

FADED_OUT_FRAME = 5    # fade_manager.c: steps 0 (scene colours) .. 5 (white or black)


def fade_tables():
    """step -> 4-bit level -> faded level, equal moves towards 15 or 0."""
    white = [[c + ((15 - c) * t) // FADED_OUT_FRAME for c in range(16)] for t in range(FADED_OUT_FRAME + 1)]
    black = [[(c * (FADED_OUT_FRAME - t)) // FADED_OUT_FRAME for c in range(16)] for t in range(FADED_OUT_FRAME + 1)]
    return white, black


def _c_table(name, rows):
    body = ",\n    ".join("{" + ", ".join(f"{v}u" for v in row) + "}" for row in rows)
    return f"static const UBYTE {name}[{len(rows)}][16] = {{\n    {body}\n}};"


def fade_support():
    white, black = fade_tables()
    return FADE_SUPPORT_TEMPLATE.replace("%(tables)s", _c_table("ngc_fade_white", white) + "\n" +
                                          _c_table("ngc_fade_black", black))


GAME_H = ("/* Generated C89. */\n#ifndef NGC_GAME_H\n#define NGC_GAME_H\nvoid ngc_game_init(void);\n"
          "void ngc_game_tick(void);\n#endif\n")
GBVM_H = ("/* Generated C89: the GBVM entry points the game calls. */\n"
          "#ifndef NGC_GBVM_H\n#define NGC_GBVM_H\n"
          "extern unsigned short script_memory[];\n"
          "void ngc_vm_start(const unsigned char * const * banks, unsigned char bank, unsigned short addr);\n"
          "unsigned char script_runner_update(void);\n#endif\n")


NGPC_FUNCTIONS.update(PROJECTILE_FUNCTIONS)
# vm_load_save.c vm_save_peek + load_save.c data_peek, on the flash journal
# (docs/M5_SAVE.md): the slot's last record of this build; the variables are
# at offset 12 of the NV1 snapshot (save_vm.c SV_HEADER). GBVM copies past the
# blob without a check; here a range outside the record reads as not saved.
NGPC_FUNCTIONS["vm_save_peek"] = """#ifndef VM_REF_TO_PTR
#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))
#endif
const volatile unsigned char * ngc_journal_find(unsigned char slot, unsigned short * length);
static void vm_save_peek(SCRIPT_CTX * THIS, INT16 idxA, INT16 idxB, INT16 idxC, UWORD count, UBYTE slot) {
    INT16 * res = VM_REF_TO_PTR(idxA);
    INT16 * dest = VM_REF_TO_PTR(idxB);
    const volatile unsigned char * p;
    unsigned short length;
    unsigned short at;
    if (idxC < 0) idxC = 0;
    *res = 0;
    if (slot > 2u) return;
    p = ngc_journal_find(slot, &length);
    if (!p || (unsigned short)idxC >= (unsigned short)(length >> 1)) return;
    at = (unsigned short)(12u + (unsigned short)idxC + (unsigned short)idxC);
    if (count > (unsigned short)(length >> 1) || at > (unsigned short)(length - count - count)) return;
    while (count != 0u) {
        *dest++ = (INT16)((unsigned short)p[at] | ((unsigned short)p[at + 1u] << 8));
        at = (unsigned short)(at + 2u);
        --count;
    }
    *res = 1;
}
"""
# vm_load_save.c vm_save_clear + load_save.c data_clear, on the flash journal
# (docs/M5_SAVE.md): GBVM zeroes the slot's signature; the journal appends a
# "slot cleared" record (Over Rev's RESET SAVE is a new record too), so the
# slot reads as not saved. data_slot_address gives NULL past the slots.
NGPC_FUNCTIONS["vm_save_clear"] = """unsigned char ngc_journal_clear(unsigned char slot);
static void vm_save_clear(SCRIPT_CTX * THIS, UBYTE slot) {
    (void)THIS;
    if (slot < 3u) (void)ngc_journal_clear(slot);
}
"""
# vm_gameboy.c vm_replace_tile_xy (non-logo scenes): the tile index is read as a
# byte (*(UINT8 *)); the scene writes its prepared NGPC tiles (replace_tiles.py).
NGPC_FUNCTIONS["vm_replace_tile_xy"] = """#ifndef VM_REF_TO_PTR
#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))
#endif
void ngc_replace_tile_xy(unsigned char x, unsigned char y, unsigned short tileset, unsigned char idx);
static void vm_replace_tile_xy(SCRIPT_CTX * THIS, UBYTE x, UBYTE y, UBYTE tileset_bank, UWORD tileset, INT16 idx_start_tile) {
    (void)tileset_bank;
    ngc_replace_tile_xy(x, y, tileset, *(UBYTE *)(VM_REF_TO_PTR(idx_start_tile)));
}
"""
# vm_gameboy.c vm_replace_tile, compiled for dialogueFrameSetTiles only (target
# .FRAME_TILE_ID, start 0, .FRAME_LENGTH tiles, emit_ui.frame_tilesets): each
# scene's UI holds that frame converted to its palettes (emit_ui frame support).
# Without any dialogue in the export the frame is never shown: nothing to load.
NGPC_FUNCTIONS["vm_replace_tile"] = """#ifndef VM_REF_TO_PTR
#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))
#endif
NGC_FRAME_DECLARATION
static void vm_replace_tile(SCRIPT_CTX * THIS, INT16 idx_target_tile, UBYTE tileset_bank, UWORD tileset, INT16 idx_start_tile, UBYTE length) {
    INT16 * A = VM_REF_TO_PTR(idx_start_tile);
    INT16 * B = VM_REF_TO_PTR(idx_target_tile);
    (void)tileset_bank;
    if ((UBYTE)*B != 0xC0u || *A != 0 || length != 9u) return;
    NGC_FRAME_CALL
}
"""


def _c_bytes(data):
    rows = [", ".join(f"0x{b:02X}u" for b in data[i:i + 12]) for i in range(0, len(data), 12)]
    return "\n    " + ",\n    ".join(rows) + "\n"


def constant_rpn_pairs(texts, model=None, rpn=None):
    """A closed bytecode shape: every RPN is the same small number of pairs.

    Analysis runs after assembly has validated the reachable scripts. Any
    other operation, label inside an expression, or differing count keeps
    the selected original interpreter. The bytecode itself stays unchanged.
    """
    if rpn is not None and set(rpn) != {"VM_OP_INT16", "VM_OP_REF_SET"}:
        return 0
    model = model or load_model()
    composites = model["composite_macros"]

    def contains_rpn(name, visited):
        if name == "VM_RPN" or name.startswith(".R_"):
            return True
        if name in visited:
            return True  # A recursive expansion cannot establish the shape.
        spec = composites.get(name)
        return bool(spec and any(contains_rpn(line.split()[0], visited | {name})
                                 for line in spec["lines"] if line.split()))

    counts = set()
    for text in texts:
        block = None
        for line in text.splitlines():
            code = line.split(";", 1)[0].strip()
            if not code:
                continue
            if code == "VM_RPN":
                if block is not None:
                    return 0
                block = []
            elif block is None and contains_rpn(code.split()[0], set()):
                # Composite expansions may contain different RPN blocks, even
                # if their selected cases are identical. Retain the original.
                return 0
            elif block is not None:
                if code == ".R_STOP":
                    if not block or len(block) > 8 or len(block) % 2:
                        return 0
                    if block != [".R_INT16", ".R_REF_SET"] * (len(block) // 2):
                        return 0
                    counts.add(len(block) // 2)
                    block = None
                else:
                    block.append(code.split()[0])
        if block is not None:
            return 0
    return next(iter(counts)) if len(counts) == 1 else 0


def constant_rpn_source(pairs):
    """Exact specialized interpreter; keep even the popped scratch writes."""
    lines = ["static void vm_rpn(SCRIPT_CTX * THIS) {",
             "    const UBYTE * pc = THIS->PC;", "    UWORD * sp = THIS->stack_ptr;",
             "    INT16 idx;", "    /* Uniform constant pairs proved from all reachable RPN blocks. */"]
    for k in range(pairs):
        lines += [f"    *sp = (UWORD)NGC_LE16(pc + {6*k+1});",
                  f"    idx = NGC_LE16(pc + {6*k+4});",
                  "    *((idx < 0) ? sp + idx : script_memory + idx) = *sp;"]
    lines += [f"    THIS->PC = pc + {6*pairs+1};", "    THIS->stack_ptr = sp;", "}"]
    return "\n".join(lines) + "\n"


def rpn_source(functions, rpn, constant_pairs=0):
    """Keep the selected original interpreter unless a closed shape is proved."""
    if constant_pairs:
        return constant_rpn_source(constant_pairs)
    return "".join(p["text"] for p in functions["vm_rpn"] if "case" not in p or p["case"] in rpn)


RUNNER_RESET_NOTE = ("Continue the same non-waitable quantum after the reset point: "
                     "waitable and exception are already zero, so do not store them again")


def runner_source(source):
    reset = "        ctx->waitable = FALSE;\n"
    repeat = "            if (!(ctx->waitable) && (counter--)) continue;"
    if source.count(reset) != 1 or source.count(repeat) != 1:
        raise CompileError("script_runner_update: reset or quantum continuation changed")
    return source.replace(reset, reset + "ngc_runner_instruction:\n").replace(
        repeat, "            if (!(ctx->waitable) && (counter--)) goto ngc_runner_instruction;")


def runtime_adaptations(runtime, handlers, natives, rpn, constant_pairs=0):
    result = {f: list(runtime["adaptations"][f])
              for f in handlers + natives + ["script_runner_init", "script_execute"]
              if runtime["adaptations"].get(f)}
    for fn in handlers:
        if fn in INLINE_FUNCTIONS:
            result.setdefault(fn, []).append(INLINE_NOTE)
    if "vm_rpn" in handlers and constant_pairs:
        result["vm_rpn"].append(f"All reachable RPN blocks have {constant_pairs} constant assignment pairs: exact specialized interpreter; bytecode, scratch writes and quantum unchanged")
    result["script_runner_update"] = list(runtime["adaptations"]["script_runner_update"]) + [RUNNER_RESET_NOTE]
    return result


def rpn_cases(runtime, rpn):
    """Assembler RPN uses -> the vm_rpn cases (vm.h VM_OP_*). An operator is
    recorded by value (`operator N`) and named here from vm.h's defines;
    an unknown value stays as is and is refused."""
    by_value = {}
    for name, value in runtime["c_defines"].items():
        if name.startswith("VM_OP_") and re.fullmatch(r"\d+", value) and name != "VM_OP_STOP":
            if int(value) in by_value:
                raise CompileError(f"vm.h: operators {by_value[int(value)]} and {name} share the value {value}")
            by_value[int(value)] = name
    cases = set()
    for use in rpn:
        m = re.fullmatch(r"operator (-?\d+)", use)
        cases.add(by_value.get(int(m[1]), use) if m else use)
    return cases


# C variable behind each engine memory index (assembler.ENGINE_MEMORY); the
# camera settings are the linked scene_actors.c variable (scene_link.py publish).
MEMORY_C = {"fade_frames_per_step": "fade_frames_per_step", "camera_settings": "ngc_camera_settings"}
# Platformer variables are public in scene_actors.c under their GBVM names.
MEMORY_C.update({name: f"ngc_{name}" for name in SCROLL_BOUNDS})
MEMORY_C.update({name: name for name in ENGINE_MEMORY if name not in MEMORY_C})
# Memory read through an address rather than a variable of that name:
# name -> ((offset from its index, address), ...).
# DIV_REG (VM_RANDOMIZE), the Game Boy's free 16384 Hz divider, has no NGPC
# register: the K2GE line counter RAS.V (0x8009, 0..198, docs/ECARTS_GBSTUDIO.md
# §18) is the sub-frame value read at the same place. game_time: GBVM's frame
# byte is the low byte of the scene engine's tick counter (little-endian).
# joypads + 1 (ifInput: joypads.joy0) is the scene engine's ngc_joy, already
# in GBDK's button order (scene_topdown.input_source, ECARTS §2).
MEMORY_POINTER = {"DIV_REG": ((0, "(UBYTE *)0x8009"),), "game_time": ((0, "(UBYTE *)&ngc_game_time"),),
                  "joypads": ((1, "(UBYTE *)&ngc_joy"),),
                  # The NGPC Color always runs the colour branch (a CGB in colour mode).
                  "_is_CGB": ((0, "(UBYTE *)&ngc_is_cgb"),),
                  # Neither a Super Game Boy nor a Game Boy Advance (ECARTS §12).
                  "_is_SGB": ((0, "(UBYTE *)&ngc_is_sgb"),), "_is_GBA": ((0, "(UBYTE *)&ngc_is_gba"),),
                  # core.c pause_state_update, read by game.c (pause_state_game).
                  "pause_state_update": ((0, "(UBYTE *)&ngc_pause_state_update"),),
                  # GBVM current_scene is a far pointer {bank, address}; here a scene
                  # is bank 0 at its table index (assembler symbols ___bank_X, _X).
                  "current_scene": ((0, "ngc_current_scene_ref()"), (1, "ngc_current_scene_ref() + 1"))}
# Scene symbol values for scripts: the reachable scenes are their table index;
# any other scene (an ifCurrentSceneIs on a scene never entered) is 0xFFFF, never
# the current one, and a scene change to it is no change (ngc_vm_exception).
UNREACHED_SCENE = 0xFFFF


def scene_symbols(project, index_by_symbol):
    out = {s["symbol"]: UNREACHED_SCENE for s in project.scenes}
    out.update(index_by_symbol)
    return out


def engine_memory(text, macros, model):
    """Engine memory a game reads or writes: named in its scripts
    (_setConstMemInt8...) or inside a composite macro it uses (VM_RANDOMIZE
    reads _DIV_REG and _game_time)."""
    lines, seen = [text], set()

    def expand(name):
        spec = model["composite_macros"].get(name)
        if name in seen or not spec:
            return
        seen.add(name)
        for line in spec["lines"]:
            lines.append(line)
            if line.split():
                expand(line.split()[0])
    for macro in macros:
        expand(macro)
    joined = "\n".join(lines)
    return sorted(n for n in ENGINE_MEMORY if re.search(rf"\b_{n}\b", joined))


def rand_support(used):
    """GBDK 4.3.0 rand / randw / initrand (libc/asm/sm83/rand.s), one 16-bit
    seed, independently expressed in C (no GBDK code). Only the used ones."""
    c = ["static UWORD ngc_rand_seed;",
         "/* seed = 17 * seed + 0x5c93 (mod 65536), as rand.s computes it. */",
         "static void ngc_rand_step(void)", "{",
         "    ngc_rand_seed = (UWORD)((UWORD)(ngc_rand_seed << 4) + ngc_rand_seed + 0x5C93u);", "}"]
    if "rand" in used:
        c += ["/* rand (OLDCALL, E): the new high byte. */", "static UBYTE ngc_rand(void)", "{",
              "    ngc_rand_step();", "    return (UBYTE)(ngc_rand_seed >> 8);", "}"]
    if "randw" in used:
        c += ["/* randw (OLDCALL, DE): D = new low byte, E = new high byte. */", "static UWORD ngc_randw(void)", "{",
              "    ngc_rand_step();", "    return (UWORD)((UWORD)(ngc_rand_seed << 8) | (ngc_rand_seed >> 8));", "}"]
    if "initrand" in used:
        c += ["/* initrand: the seed is the argument (low byte, then high byte). */",
              "static void ngc_initrand(UWORD seed)", "{", "    ngc_rand_seed = seed;", "}"]
    return "\n".join(c) + "\n"


# cc900's sparse switch checks high opcodes before common low opcodes.
# Emit only the movement/wait instructions actually selected by the game.
HOT_DECODERS = ("VM_ACTOR_MOVE_TO_X", "VM_ACTOR_MOVE_TO_Y", "VM_INVOKE")
# This closed RPN shape occurs in constant Move To loops. Keep the established
# INVOKE priority for other projects (waits, text and calculated expressions).
CONSTANT_DECODERS = ("VM_ACTOR_MOVE_TO_X", "VM_JUMP", "VM_SET_CONST", "VM_RPN", "VM_ACTOR_MOVE_TO_INIT",
                     "VM_ACTOR_MOVE_TO_Y", "VM_ACTOR_MOVE_TO_SET_DIR_X", "VM_ACTOR_MOVE_TO_SET_DIR_Y", "VM_IDLE", "VM_INVOKE", "VM_RESERVE")


# Handlers of one or two statements are written in the decoder itself: the
# statements of vm.c's vm_idle / vm_set_const / vm_jump (with ngc_addr), on
# operands already read by the decoder, and no call, argument push or frame.
# The manifest still lists the vendored function (provenance); its separate
# body is not emitted. Same PC, waitable and memory writes as the call.
INLINE_DECODERS = {
    "VM_IDLE": ("vm_idle", "ctx->waitable = TRUE;"),  # PC published by the zero-operand rule below
    "VM_SET_CONST": ("vm_set_const",
                     "ctx->PC = ngc_pc + 4; a = (((UWORD)ngc_pc[0] << 8) | (UWORD)ngc_pc[1]); "
                     "b = (((UWORD)ngc_pc[2] << 8) | (UWORD)ngc_pc[3]); "
                     "if ((INT16)b < 0) *(ctx->stack_ptr + (INT16)b) = a; else *(script_memory + (INT16)b) = a;"),
    "VM_JUMP": ("vm_jump", "a = (((UWORD)ngc_pc[0] << 8) | (UWORD)ngc_pc[1]); "
                           "ctx->PC = (UBYTE *)(ngc_banks[ctx->bank] + a);"),
}
# Only these three. Writing the vm_actor_move_to_* wrappers here as well was
# measured slower: cc900 then saves more registers in every VM_STEP (R-PF
# scripts peak 49 270 -> 59 238 cycles, docs/M3_RPF.md).
INLINE_FUNCTIONS = {fn: macro for macro, (fn, _) in INLINE_DECODERS.items()}
INLINE_NOTE = "Written in VM_STEP's decoder (same statements, no call); no separate function emitted"


def decoder_code(macro):
    """C actually emitted for a macro: inline statements or the handler call."""
    return INLINE_DECODERS[macro][1] if macro in INLINE_DECODERS else DECODERS[macro][2]


def operand_decoder(code):
    """Read fixed operands locally; publish their end before calling a handler.

    Handlers may rewind/jump PC, invoke a modal, or suspend the quantum.
    Their PC is therefore never overwritten after their call. Indexed UI
    decoders retain their canonical before/after-handler ordering.
    """
    if re.search(r"ctx->PC(?!\+\+)", code):
        return 0, code
    offset = 0
    def fetch(match):
        nonlocal offset
        start = offset
        if match[0] == "ngc_read_word(ctx)":
            offset += 2
            return f"(((UWORD)ngc_pc[{start}] << 8) | (UWORD)ngc_pc[{start + 1}])"
        offset += 1
        return f"ngc_pc[{start}]"
    decoded = re.sub(r"ngc_read_word\(ctx\)|\*ctx->PC\+\+", fetch, code)
    return offset, (f"ctx->PC = ngc_pc + {offset}; " if offset else "") + decoded


def text_sound_use(texts):
    """(effects, remove): EVENT_TEXT_SET_SOUND_EFFECT selects a native effect /
    removes the text sound somewhere in the reachable scripts."""
    from ..project_sfx import TEXT_SOUND_BANK
    joined = "\n".join(texts)
    return (bool(re.search(rf"^\s*VM_SET_TEXT_SOUND\s+{TEXT_SOUND_BANK},", joined, re.M)),
            bool(re.search(r"^\s*VM_SET_TEXT_SOUND\s+0,\s*0,\s*0\s*$", joined, re.M)))


def gbvm_source(runtime, macros, rpn, natives, memory, model, fade_style=0, scene_palettes=False, update_contexts=None, interaction_contexts=None, trigger_contexts=None, hit_contexts=None, scenes=False,
                input_contexts=None, ui=None, heap_words=None, stack_words=None, tick_frames=1, platform_callbacks=(), callback_attach=False, callback_detach=False, callback_contexts=None,
                adventure_callbacks=(), adventure_attach=False, adventure_detach=False, constant_pairs=0, timer_contexts=None, projectile_actors=None, save=None, thread_contexts=None,
                projectile_slots=False,
                palette_maps=None, text_sound=(False, False)):
    """save: None, or (saves, loads) when the scripts save or load data
    (docs/M5_SAVE.md): EXCEPTION_SAVE / EXCEPTION_LOAD on the flash journal.
    scene_palettes: the scene engine is linked: it has loaded the palettes before
    ngc_vm_start, the fade starts from them (GBVM fades the palettes load_scene
    loaded), and its actors_update asks ngc_vm_locked (VM_ISLOCKED).
    scenes: several scenes (scene changes): the game drives the VM through
    ngc_vm_start / ngc_vm_run / ngc_vm_kill / ngc_vm_exception, and the scenes
    write their colours into ngc_palette_shadow, shown by ngc_palette_show.
    ui: (support, api) C of emit_ui.ui_support when the scripts print text."""
    functions = dict(runtime["functions"])
    shake = "camera_shake_frames" in natives
    rpn = rpn_cases(runtime, rpn)
    # Real instructions only (composite macros expand into them); VM_STOP
    # (opcode 0) is the terminator VM_STEP tests before dispatching.
    instructions = sorted(m for m in macros if macro_opcode(model, m) is not None and m != "VM_STOP")
    handlers = []
    for macro in instructions:
        if macro not in DECODERS:
            raise CompileError(f"{macro}: no NGPC decoder delivered")
        for fn in DECODERS[macro][1]:
            if fn not in handlers:
                handlers.append(fn)
    # GBDK's one random seed, shared by every user (rand_support).
    rand_fns = [fn for fn, used in (("rand", shake), ("randw", "vm_rand" in handlers or "VM_OP_RND" in rpn),
                                    ("initrand", "vm_init_rng" in handlers)) if used]
    if rand_fns:
        # Zero at engine start only: a scene change (ngc_vm_kill, reset 0) keeps
        # the sequence, as GBDK's seed survives load_scene (adapted start, §17).
        functions["script_runner_init"] = functions["script_runner_init"].replace(
            "    if (reset) {", "    if (reset) {\n        ngc_rand_seed = 0u;")
    refused = sorted(rpn & RPN_NOT_DELIVERED) + sorted(o for o in rpn if not o.startswith("VM_OP_"))
    if refused:
        raise CompileError(f"RPN operations not delivered: {', '.join(refused)}")
    if "ngc_scene_raise" in handlers and (not scenes or "vm_raise" not in handlers):
        raise CompileError("Pop Scene State without any scene change: the popped scene can only be one "
                           "a Switch Scene led away from (several scenes needed)")
    fade = "vm_fade" in handlers
    c = ["/* GBVM (MIT, Copyright (c) 2020 Toxa, see GBVM_LICENSE.txt), commit "
         f"{runtime['gbvm_commit']}.", " * Functions vendored by tools/extract_gbvm_runtime.py; adaptations"
         " listed in export.json.", " * Decoder, tables and NGPC parts: generated by ngpcraft/gbvm/emit.py. */",
         '#include "gbvm.h"',
         "typedef unsigned char UBYTE;", "typedef unsigned char FASTUBYTE;", "typedef unsigned char UINT8;",
         "typedef unsigned short UWORD;", "typedef signed short INT16;", "typedef signed char INT8;",
         "#define TRUE 1", "#define FALSE 0", "#define NULL 0", "#define CURRENT_BANK 0",
         "#define SWITCH_ROM(bank) ((void)(bank))",
         "#define NGC_LE16(p) ((INT16)((UWORD)(p)[0] | ((UWORD)(p)[1] << 8)))",
         "/* Pictures per scene tick: 1 (60 per second) or 2 (ngpcraft.json ngpc.frame_rate 30,",
         " * docs/M2_PERF_PISTES.md section 3; scripts still run once per picture). */",
         f"#define NGC_TICK_FRAMES {tick_frames}"]
    defines = dict(runtime["defines"])
    if stack_words is not None:
        if type(stack_words) is not int or not 1 <= stack_words <= defines["VM_CONTEXT_STACK_SIZE"]:
            raise CompileError(f"{stack_words!r} VM stack words: expected an integer in "
                               f"1..{defines['VM_CONTEXT_STACK_SIZE']}")
        defines["VM_CONTEXT_STACK_SIZE"] = stack_words
    # Variable aliases are assigned dense indices by the compiler. Context
    # stacks remain separate, including references to their packed locals.
    if heap_words is not None:
        if type(heap_words) is not int or heap_words < 0:
            raise CompileError(f"{heap_words!r} VM heap words: expected a nonnegative integer")
        defines["VM_HEAP_SIZE"] = heap_words
    capacity = update_contexts if update_contexts is not None else interaction_contexts
    if capacity is None:
        capacity = trigger_contexts
    if capacity is None:
        capacity = hit_contexts
    if capacity is None:
        capacity = input_contexts
    if (platform_callbacks or adventure_callbacks) and capacity is None:
        capacity = callback_contexts
    if capacity is None:
        capacity = timer_contexts
    if capacity is None:
        capacity = thread_contexts
    if capacity is not None:
        if not 1 <= capacity <= 255:
            raise CompileError(f"{capacity} VM contexts: context IDs must fit in a byte")
        defines["VM_MAX_CONTEXTS"] = capacity
    words = defines["VM_HEAP_SIZE"] + defines["VM_MAX_CONTEXTS"] * defines["VM_CONTEXT_STACK_SIZE"]
    if words > 32768:
        raise CompileError(f"{words} VM memory words: references must fit in a signed 16-bit index")
    c += [f"#define {k} {v}" for k, v in defines.items()]
    c += [f"#define {k} {v}" for k, v in runtime["c_defines"].items()]
    c.append(runtime["context"])
    if fade:
        c.append(runtime["fade_direction"])
    # Globals of vm.c:20-31, static except the script memory the probes read.
    c += ["static SCRIPT_CTX CTXS[VM_MAX_CONTEXTS];", "static SCRIPT_CTX * first_ctx, * free_ctxs;",
          "static SCRIPT_CTX * old_executing_ctx, * executing_ctx;", "static UBYTE vm_lock_state;",
          "static UBYTE vm_loaded_state;", "static UBYTE vm_exception_code;",
          *(["static UBYTE vm_exception_params_length;", "static UBYTE vm_exception_params_bank;",
             "static const void * vm_exception_params_offset;"] if "vm_raise" in handlers else []),
          # vm.c sizes it VM_HEAP_SIZE + (VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE): cc900 folds
          # that product on 8 bits inside the sum (164 + (25 * 64) -> 228 words, measured
          # 2026-10-10), the context stacks then overwrote the RAM after the array. With
          # unsigned short operands cc900 folds it right (measured: 3528, 488, 360 bytes);
          # the defines stay the source (tools/probe_vm_stacks.py rebuilds with another stride).
          "UWORD script_memory[(unsigned short)VM_HEAP_SIZE + (unsigned short)VM_MAX_CONTEXTS"
          " * (unsigned short)VM_CONTEXT_STACK_SIZE];",
          "static void ngc_clear(UBYTE * p, UWORD n)", "{", "    while (n != 0u) { *p++ = 0u; --n; }", "}",
          "/* NGPC banks: a bank is a C array of at most 64 KB in game.c; a script",
          " * address is a 16-bit offset in its bank, as on the Game Boy (assembler.py). */",
          "static const UBYTE * const * ngc_banks;"]
    inputs = any(fn in handlers for fn in INPUT_FUNCTIONS)
    if inputs:
        # events.h / events.c: input scripts; the buttons are the scene
        # engine's (scene_input.c, refreshed once per tick as input_update).
        c += [runtime["script_event"], "static script_event_t input_events[8];",
              "static UBYTE input_slots[8];", "extern unsigned char ngc_joy;",
              "extern unsigned char ngc_last_joy;", "extern unsigned char ngc_recent_joy;",
              "extern unsigned char ngc_joy_pressed;"]
    timers = any(fn in handlers for fn in TIMER_FUNCTIONS)
    if timers:
        # events.h / events.c timers; script_event_t is shared with input scripts.
        c += ([] if inputs else [runtime["script_event"]]) + [
            runtime["timer_time"], "static script_event_t timer_events[MAX_CONCURRENT_TIMERS];",
            "static timer_time_t timer_values[MAX_CONCURRENT_TIMERS];"]
    # Music routines run only with a reachable Play Music (a song to call them).
    routines = "vm_music_routine" in handlers and "vm_music_play" in handlers
    if routines:
        c += ([] if inputs or timers else [runtime["script_event"]]) + [
            "/* music_manager.c music_events (docs/M5_MUSIC_ROUTINE.md). */",
            "static script_event_t music_events[4];",
            "unsigned char ngc_music_routine_poll(unsigned char * data);  /* audio.c */",
            "void ngc_music_routine_clear(void);  /* audio.c */"]
    if any("ngc_addr" in decoder_code(m) for m in instructions):
        c += ["static UBYTE * ngc_addr(SCRIPT_CTX * ctx, UWORD addr)", "{",
              "    return (UBYTE *)(ngc_banks[ctx->bank] + addr);", "}"]
    text = any(fn in UI_FUNCTIONS for fn in handlers)
    # EVENT_TEXT_SET_SOUND_EFFECT (docs/M5_TEXT_SOUND.md): heard only when text
    # is drawn and a native effect is selected (Remove alone cuts nothing).
    text_sound_live = ("vm_set_text_sound" in handlers and text and
                       (text_sound[0] or "vm_sfx_play" in handlers))
    if text_sound_live:
        original = "    (void)flag;\n"
        if functions["ui_update"].count(original) != 1:
            raise CompileError("text sound: ui_update anchor changed")
        functions["ui_update"] = functions["ui_update"].replace(
            original, "    if ((flag) && (text_sound_bank != SFX_STOP_BANK)) ngc_text_sound_play();\n")
        if "ui_init();" in ui[1].split("void ngc_ui_scene_load(", 1)[1]:
            # Case B re-runs ui_init at each scene; core.c runs it once: the
            # text sound survives scene changes on the Game Boy.
            head, tail = ui[1].split("void ngc_ui_scene_load(", 1)
            tail = tail.replace("    ui_init();", "    {\n        UBYTE kept;\n        kept = text_sound_bank;\n"
                                "        ui_init();\n        text_sound_bank = kept;\n    }", 1)
            ui = (ui[0], head + "void ngc_ui_scene_load(" + tail)
    if fade or text or scene_palettes or scenes:
        c.append(FRAME_WAIT_SUPPORT)
    if fade:
        support = fade_support()
        if scenes:
            # The scene files fill it (scene_link.py externs): public.
            old = "static UWORD ngc_palette_shadow[193];"
            if support.count(old) != 1:
                raise CompileError("fade support: palette shadow declaration changed")
            support = support.replace(old, "UWORD ngc_palette_shadow[193];")
        c.append(support)
    elif scenes:
        # load_scene's palette buffers are needed by scene changes even when
        # no reachable script fades. No fade state, staging buffer or LUT.
        c.append("UWORD ngc_palette_shadow[193];")
    if text:
        if ui is None:
            raise CompileError("text instructions without the scene's UI data (internal)")
        c.append(ui[0])
    if text_sound_live:
        c += ["/* ui.c text_sound_data: a native effect index (project_sfx.TEXT_SOUND_BANK). */",
              "static UWORD text_sound_data;",
              "/* ui.c ui_update: music_play_sfx(text_sound_bank, text_sound_data, mask,",
              " * MUSIC_SFX_PRIORITY_NORMAL); bank 0 is Remove Sound (a NULL sample). */",
              "static void ngc_text_sound_play(void)", "{",
              *(["    if (text_sound_bank == 0u) { ngc_sfx_cut(4u); return; }"] if text_sound[1] else []),
              "    ngc_sfx_play(text_sound_data, 4u);", "}"]
    camera_control = any(fn in handlers for fn in ("vm_camera_move_to", "vm_camera_set_pos"))
    if camera_control:
        c.append("""typedef struct camera_position_t { INT16 X, Y; } camera_position_t;
extern UWORD ngc_camera_x, ngc_camera_y, ngc_camera_clamp_x, ngc_camera_clamp_y;
extern UBYTE ngc_camera_settings;
void ngc_actors_scroll(short cx, short cy);
#define camera_x ngc_camera_x
#define camera_y ngc_camera_y
#define camera_clamp_x ngc_camera_clamp_x
#define camera_clamp_y ngc_camera_clamp_y
#define camera_settings ngc_camera_settings
#define CAMERA_LOCK_FLAG 3u
#define scroll_update() ngc_actors_scroll((short)(camera_x >> 5), (short)(camera_y >> 5))
""")
    if memory:
        if not fade and "fade_frames_per_step" in memory:
            raise CompileError(f"engine memory {memory} used without the fade manager that owns it")
        if "camera_settings" in memory and not scenes:
            raise CompileError("engine memory camera_settings is delivered with scene changes only")
        if "camera_settings" in memory:
            c.append(f"extern UBYTE {MEMORY_C['camera_settings']};")
        platform = [name for name in memory if name not in ("fade_frames_per_step", "camera_settings")
                    and name not in MEMORY_POINTER and name not in TEXT_MEMORY and name not in UNREAD_MEMORY
                    and name not in SCROLL_BOUNDS]
        # scroll.c bounds: scene_stream.c variables (background_stream.SCROLL_BOUNDS_VARIABLES).
        c += [f"extern unsigned short {MEMORY_C[name]};" for name in memory if name in SCROLL_BOUNDS]
        # ui.c overlay_cut_scanline: only a box at the top reads it (refused), a byte stands for it.
        c += [f"static UBYTE {name};" for name in memory if name in UNREAD_MEMORY]
        if not text:
            # No reachable script prints: ui.c's settings are written and never read
            # (GBVM keeps them all the same); plain bytes stand for them.
            c += [f"static UBYTE {name};" for name in memory if name in TEXT_MEMORY]
        if platform:
            # scene_actors.c (platform.c) variables; only their address is taken here.
            c += [f"extern UBYTE {MEMORY_C[name]};" for name in platform]
        if "game_time" in memory:
            c.append("extern unsigned short ngc_game_time;  /* scene_actors.c */")
        if "_is_CGB" in memory:
            c.append("static const UBYTE ngc_is_cgb = 1u;  /* GBVM is_CGB: colour console */")
        if "pause_state_update" in memory:
            c.append("UBYTE ngc_pause_state_update;  /* core.c pause_state_update (game.c resets it) */")
        if "_is_SGB" in memory:
            c.append("static const UBYTE ngc_is_sgb = 0u;  /* GBVM is_SGB: no Super Game Boy (ECARTS 12) */")
        if "_is_GBA" in memory:
            c.append("static const UBYTE ngc_is_gba = 0u;  /* GBVM is_GBA: not a Game Boy Advance (ECARTS 12) */")
        if "joypads" in memory and not inputs:
            c.append("extern unsigned char ngc_joy;  /* scene_input.c */")
        if "current_scene" in memory:
            c += [*(["extern unsigned char ngc_scene;  /* scene_changes: the loaded scene */"] if scenes else []),
                  "/* GBVM current_scene {bank, address}, rebuilt at each read (bank 0). */",
                  "static UBYTE ngc_current_scene_mem[3];",
                  "static UBYTE * ngc_current_scene_ref(void)", "{",
                  "    ngc_current_scene_mem[0] = 0u;",
                  f"    ngc_current_scene_mem[1] = {'ngc_scene' if scenes else '0u'};",
                  "    ngc_current_scene_mem[2] = 0u;",
                  "    return ngc_current_scene_mem;", "}"]
        c += ["/* NGPC engine memory table: an RPN memory operand is an index (assembler.ENGINE_MEMORY). */",
              "static UBYTE * ngc_memory(INT16 index)", "{", "    switch (index) {"]
        for name in memory:
            for offset, address in MEMORY_POINTER.get(name, ((0, f"(UBYTE *)&{MEMORY_C[name]}"),)):
                c.append(f"    case {ENGINE_MEMORY[name] + offset}: return {address};")
        c += ["    default: return 0;", "    }", "}"]
    if "vm_rate_limit_const" in handlers:
        c.append("extern volatile UWORD ngc_sys_time;  /* startup.c, GBDK sys_time */")
    if "vm_input_wait" in handlers and not inputs:
        c.append("extern unsigned char ngc_joy, ngc_last_joy;  /* scene_input.c */")
    if "VM_OP_ATAN2" in rpn:
        c.append(atan2_source())
    if rand_fns:
        c.append(rand_support(rand_fns))
    if shake:
        c.append('/* Original camera_shake_frames reads the 16-bit VBlank clock. */\nextern volatile UWORD ngc_sys_time;\n#define sys_time ngc_sys_time\nsigned char ngc_scroll_offset_x, ngc_scroll_offset_y;\n#define scroll_offset_x ngc_scroll_offset_x\n#define scroll_offset_y ngc_scroll_offset_y\n')
    if "vm_invoke" in handlers:
        for name in natives:
            c.append(functions[name])
        c += ["/* NGPC native table: the bytecode names a native by index (assembler.NATIVES). */",
              "static UBYTE * ngc_native(UWORD index)", "{", "    switch (index) {"]
        c += [f"    case {NATIVES[name]}u: return (UBYTE *){name};" for name in natives]
        c += ["    default: return 0;", "    }", "}",
              "static UBYTE ngc_native_call(UBYTE * fn, void * THIS, UBYTE start, UWORD * stack_frame)", "{"]
        c += [f"    if (fn == (UBYTE *){name}) return {name}(THIS, start, stack_frame);" for name in natives]
        c += ["    return TRUE;", "}",
              "#define FAR_CALL_EX(addr, seg, typ, ctx, start, frame) ngc_native_call((addr), (ctx), (start), (frame))"]
    if platform_callbacks:
        c.append(callback_support(platform_callbacks, callback_attach, callback_detach))
    if adventure_callbacks:
        c.append(callback_support(adventure_callbacks, adventure_attach, adventure_detach, "adventure"))
    link = any(fn in handlers for fn in link_cable.LINK_FUNCTIONS)
    link_emitted = False
    for fn in handlers:
        if fn in INLINE_FUNCTIONS:
            continue
        if fn == "vm_replace_tile":
            c.append(NGPC_FUNCTIONS[fn].replace(
                "NGC_FRAME_DECLARATION", "static void ngc_ui_frame_load(UWORD tileset);" if ui else "").replace(
                "NGC_FRAME_CALL", "ngc_ui_frame_load(tileset);" if ui else "(void)tileset;"))
        elif fn == "vm_music_play" and "vm_music_mute" in handlers:
            # vm_music.c vm_music_play: music_global_mute_mask = MUTE_MASK_NONE first.
            c.append(NGPC_FUNCTIONS[fn].replace("    ngc_music_play_track(track);",
                                                "    ngc_music_mute(0u);\n    ngc_music_play_track(track);"))
        elif fn in NGPC_FUNCTIONS:
            c.append(NGPC_FUNCTIONS[fn].replace("EXCEPTION_CHANGE_SCENE_VALUE",
                                                f"{model['constants']['EXCEPTION_CHANGE_SCENE']}u"))
        elif fn == "vm_load_palette":
            # docs/M4_PALETTES.md: palette_maps[k] lists the K2GE words of scene k.
            if palette_maps is None:
                raise CompileError("VM_LOAD_PALETTE without the scenes' palette maps (internal)")
            from ..palette_load import handler_source
            c.append(handler_source(palette_maps, scenes=scenes, shadow=fade or scenes, fade=fade))
        elif fn == "vm_projectile_load_type" and not projectile_slots:
            # No scene launches what it loads: no slot map was compiled.
            c.append("static void vm_projectile_load_type(SCRIPT_CTX * THIS, UBYTE dest, UBYTE src, UWORD table) {\n"
                     "    (void)THIS; (void)dest; (void)src; (void)table;  /* nothing launched after a load */\n}\n")
        elif fn in link_cable.LINK_FUNCTIONS:
            if not link_emitted:
                c.append(link_cable.source("vm_sio_exchange" in handlers))
                link_emitted = True
        elif fn == "vm_music_routine":
            # vm_music.c vm_music_routine; no reachable Play Music: no song calls it.
            c.append("static void vm_music_routine(SCRIPT_CTX * THIS, UBYTE routine, UBYTE bank, UBYTE * pc)\n{\n" +
                     ("    script_event_t * event;\n    (void)THIS;\n    event = &music_events[routine & 0x03u];\n"
                      "    event->script_bank = bank;\n    event->script_addr = pc;\n}\n" if routines else
                      "    (void)THIS; (void)routine; (void)bank; (void)pc;  /* no reachable Play Music */\n}\n"))
        elif fn == "vm_music_mute":
            # vm_music.c vm_music_mute; no reachable Play Music: no music to mute.
            c.append("static void vm_music_mute(SCRIPT_CTX * THIS, UBYTE channels)\n{\n    (void)THIS;\n" +
                     ("    ngc_music_mute(channels);\n" if "vm_music_play" in handlers else
                      "    (void)channels;  /* no reachable Play Music */\n") + "}\n")
        elif fn == "vm_set_text_sound":
            # vm_ui.c vm_set_text_sound; no drawn text or no effect: nothing to hear.
            c.append("static void vm_set_text_sound(SCRIPT_CTX * THIS, UBYTE bank, UWORD id, UBYTE mask)\n{\n"
                     "    (void)THIS; (void)mask;\n" +
                     ("    text_sound_bank = bank;\n    text_sound_data = id;\n}\n" if text_sound_live else
                      "    (void)bank; (void)id;  /* no text drawn, or no native effect selected */\n}\n"))
        elif fn == "vm_beginthread":
            # script_execute is emitted after VM_STEP, with the runner.
            c += ["static SCRIPT_CTX * script_execute(UBYTE bank, UBYTE * pc, UWORD * handle, UBYTE nargs);",
                  functions[fn]]
        elif fn == "vm_call_native":
            c += ["static void vm_call_native(SCRIPT_CTX *THIS, UBYTE bank, UWORD ptr)", "{", "    (void)bank;"]
            if callback_attach: c.append("    if (ptr == 3u) plat_callback_attach(THIS);")
            if callback_detach: c.append("    if (ptr == 4u) plat_callback_detach(THIS);")
            if adventure_attach: c.append("    if (ptr == 5u) adv_callback_attach(THIS);")
            if adventure_detach: c.append("    if (ptr == 6u) adv_callback_detach(THIS);")
            c.append("}")
        elif fn == "vm_rpn":
            rpn_c = rpn_source(functions, rpn, constant_pairs)
            if "VM_OP_ATAN2" in rpn:
                original = "atan2((WORD)*A, (WORD)*B)"
                if rpn_c.count(original) != 1:
                    raise CompileError("selected original ATAN2 call changed")
                rpn_c = rpn_c.replace(original, "ngc_atan2((INT16)*A, (INT16)*B)")
            c.append(rpn_c)
        elif "VM_REF_TO_PTR" in functions[fn]:
            # vm.h's macro, whatever else the export emits (a scene may move
            # the camera without any actor or text support defining it).
            c += ["#ifndef VM_REF_TO_PTR",
                  "#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))",
                  "#endif", functions[fn]]
        else:
            c.append(functions[fn])
    decoded = {m: (INLINE_DECODERS[m][1].count("ngc_pc["), INLINE_DECODERS[m][1]) if m in INLINE_DECODERS
               else operand_decoder(DECODERS[m][2]) for m in instructions}
    # Fixed operand decoders already publish PC before the handler. Publish
    # only here for zero-sized or custom indexed decoders; never twice.
    decoded = {m: (size, code if size else "ctx->PC = ngc_pc; " + code)
               for m, (size, code) in decoded.items()}
    c += ["static UBYTE VM_STEP(SCRIPT_CTX * ctx)", "{", "    UBYTE op;"]
    c.append("    const UBYTE * ngc_pc;")
    code = [DECODERS[m][2] for m in instructions]
    words = [v for v in ("a", "b", "c") if any(re.search(rf"\b{v} = ", x) for x in code)]
    small = [v for v in ("n", "bank", "cond") if any(re.search(rf"\b{v} = ", x) for x in code)]
    if words:
        c.append(f"    UWORD {', '.join(words)};")
    if small:
        c.append(f"    UBYTE {', '.join(small)};")
    c += ["    ngc_pc = ctx->PC;", "    op = *ngc_pc++;", "    if (!op) return 0u;"]
    hot_decoders = CONSTANT_DECODERS if constant_pairs else HOT_DECODERS
    for macro in hot_decoders:
        if macro in instructions:
            opcode = macro_opcode(model, macro)
            c.append(f"    if (op == 0x{opcode:02X}u) {{ {decoded[macro][1]} return 1u; }}  /* {macro} */")
    cases, dense = {}, []
    for macro in sorted(instructions, key=lambda m: macro_opcode(model, m)):
        opcode = macro_opcode(model, macro)
        if opcode in cases:
            # Two macros of one opcode (VM_RET_FAR, VM_RET_FAR_N): one decoder.
            if DECODERS[cases[opcode]][2] != DECODERS[macro][2]:
                raise CompileError(f"{macro} and {cases[opcode]} share opcode {opcode:#04x} with different decoders")
            continue
        cases[opcode] = macro
        if macro not in hot_decoders:
            dense.append((opcode, macro))
    # cc900 compiles a sparse switch into a chain of compares (the common low
    # opcodes came last, ~18 compares) but a dense one into a jump table: the
    # opcode goes through a ROM table to 1..N first (docs/M2_PERF_PISTES.md).
    index = [0] * 256
    for k, (opcode, _) in enumerate(dense, 1):
        index[opcode] = k
    c.append("    switch (ngc_op_index[op]) {")
    for k, (opcode, macro) in enumerate(dense, 1):
        c.append(f"    case {k}u: {decoded[macro][1]} break;  /* {macro} */")
    c += ["    default: ctx->PC = ngc_pc; return 0u;  /* unreachable: only assembled opcodes are listed */", "    }",
          "    return 1u;", "}"]
    # Inside VM_STEP (a local static const, in ROM): the function stays self-contained.
    step = c.index("static UBYTE VM_STEP(SCRIPT_CTX * ctx)") + 2
    c[step:step] = ["    /* Opcode -> dense case number of the switch below (0: not assembled). */",
                    "    static const UBYTE ngc_op_index[256] = {",
                    *("        " + ", ".join(f"{v}u" for v in index[i:i + 16]) + "," for i in range(0, 256, 16)),
                    "    };"]
    starts = [functions["script_runner_init"], functions["script_execute"]]
    if fade:
        starts.append(functions["fade_init"])
    c += starts + [runner_source(functions["script_runner_update"])]
    if platform_callbacks:
        c.append(callback_bodies(platform_callbacks, callback_attach, callback_detach))
    if adventure_callbacks:
        c.append(callback_bodies(adventure_callbacks, adventure_attach, adventure_detach, "adventure"))
    if text:
        c.append(ui[1])
    if inputs:
        c += [functions["events_init"], functions["events_update"]]
    if timers:
        c += [functions["timers_init"], functions["timers_update"]]
    if routines:
        c.append(MUSIC_EVENTS_SOURCE)
    if update_contexts is not None:
        c += [*([] if "script_terminate" in handlers else [functions["script_terminate"]]),
              "void ngc_vm_execute(unsigned short addr, unsigned short *handle)", "{",
              "    (void)script_execute(0u, (UBYTE *)(ngc_banks[0] + addr), handle, 0u);", "}",
              "void ngc_vm_terminate(unsigned short handle)", "{",
              "    (void)script_terminate((UBYTE)handle);", "}",
              # Public symbol of the actor updates unit; with several scenes the
              # game runs each init thread with ngc_vm_run instead (unused there).
              "/* load_scene queues init before scroll_repaint activates actors. */",
              "void ngc_vm_prepare(const unsigned char * const *banks, unsigned char bank, unsigned short addr)",
              "{", "    ngc_banks = banks;", "    script_runner_init(1u);",
              "    (void)script_execute(bank, (UBYTE *)(banks[bank] + addr), 0, 0u);", "}"]
    if interaction_contexts is not None:
        c += ["/* topdown.c interaction: one thread-local argument, value 0. */",
              "void ngc_vm_interact(unsigned short addr)", "{", "    SCRIPT_CTX *ctx;",
              "    ctx = script_execute(0u, (UBYTE *)(ngc_banks[0] + addr), 0, 0u);",
              "    if (ctx) *(ctx->stack_ptr++) = 0;", "}"]
    if trigger_contexts is not None:
        c += ["void ngc_vm_trigger(unsigned short addr, unsigned short argument)", "{", "    SCRIPT_CTX *ctx;",
              "    ctx = script_execute(0u, (UBYTE *)(ngc_banks[0] + addr), 0, 0u);",
              "    if (ctx) *(ctx->stack_ptr++) = argument;", "}"]
    if projectile_actors is not None:
        c.append(functions["script_detach_hthread"])
        c.append(hit_support(projectile_actors))
    if hit_contexts is not None:
        c += ["void ngc_vm_hit(unsigned short addr, unsigned short argument)", "{", "    SCRIPT_CTX *ctx;",
              "    ctx = script_execute(0u, (UBYTE *)(ngc_banks[0] + addr), 0, 0u);",
              "    if (ctx) *(ctx->stack_ptr++) = argument;", "}"]
    if scene_palettes:
        c += ["/* vm.h VM_ISLOCKED, for the scene engine's actors_update. */",
              "unsigned char ngc_vm_locked(void)", "{", "    return (unsigned char)(vm_lock_state != 0u);", "}"]
    if scenes:
        if "vm_raise" not in handlers:
            raise CompileError("several scenes without any VM_RAISE (internal: no scene change compiled)")
        return c_scenes(c, fade_style, model["constants"]["EXCEPTION_CHANGE_SCENE"], inputs, fade, timers,
                        music_routines=routines, link=link,
                        save=save and (model["constants"]["EXCEPTION_SAVE"], model["constants"]["EXCEPTION_LOAD"],
                                       save[1])), handlers
    if save:
        raise CompileError("save events need the scene engine (several scenes, internal)")
    c += ["/* Engine start (core.c): VM reset, fade manager, then the start scene's init thread. */",
                   "void ngc_vm_start(const unsigned char * const * banks, unsigned char bank, unsigned short addr)",
                   "{", *(["    (void)banks; (void)bank; (void)addr;"] if update_contexts is not None
                            else ["    ngc_banks = banks;", "    script_runner_init(1u);"]),
         *(["    events_init(0u);              /* core_reset: random RAM on a console */"]
           if inputs else []),
         *(["    timers_init(0u);              /* core_run: timers_init(FALSE) */"] if timers else []),
         *(["    music_init_events(0u);        /* core_run: music_init_events(FALSE) */"] if routines else []),
         *(["    ngc_link_init();              /* core.c: SIO_init */"] if link else [])]
    if fade:
        # GBVM's statics start at zero: the Game Boy C start-up clears .bss.
        # startup.c does not (initialized_ram_supported: false), and a console
        # powers on with random RAM (seen on hardware 2026-09-29: red screen).
        c += ["    ngc_clear((UBYTE *)ngc_palette_shadow, sizeof(ngc_palette_shadow));",
              *(["    /* The scene engine loaded the palettes: they are what the fades show. */",
                 "    { UBYTE n; for (n = 0u; n < 192u; ++n) ngc_palette_shadow[n] = ((volatile UWORD *)0x8200)[n]; }",
                 "    ngc_palette_shadow[192] = *(volatile UWORD *)0x83E0;"] if scene_palettes else []),
              "    fade_running = 0u; fade_frames_per_step = 0u; fade_timer = 0u;",
              "    fade_style = 0u; fade_frame = 0u; fade_direction = FADE_IN;",
              "    ngc_staged_step = 0xFFu; ngc_staged_style = 0u;"]
        # core.c runs fade_init, then the bootstrap script (compileBootstrap.ts
        # script_engine_init) sets the engine fields: fade_style is one.
        c += ["    fade_init();", f"    fade_style = {fade_style}u;  /* engine field fade_style */"]
    if update_contexts is None:
        c += ["    (void)script_execute(bank, (UBYTE *)(banks[bank] + addr), 0, 0u);"]
    c += ["}"]
    return "\n".join(c) + "\n", handlers


def c_scenes(c, fade_style, change_scene, inputs=False, fade=True, timers=False, save=None, music_routines=False,
             link=False):
    """GBVM core.c pieces the scene manager (game.c) calls, several scenes.
    save: None, or (EXCEPTION_SAVE, EXCEPTION_LOAD, loads used)."""
    if save and save[2] and not fade:
        raise CompileError("Load Data without the fade manager: core.c EXCEPTION_LOAD runs fade_out_modal "
                           "(no fade linked; internal, save data always fades in)")
    c += [("/* core_run: VM reset (memory cleared), fade manager, engine field fade_style;"
           if fade else "/* core_run: VM reset (memory cleared), palette buffers;") ,
          " * no thread: the game loads the start scene, which runs its init. */",
          "void ngc_vm_start(const unsigned char * const * banks, unsigned char bank, unsigned short addr)", "{",
          "    (void)bank; (void)addr;",
          "    ngc_banks = banks;", "    script_runner_init(1u);",
          # GBVM's statics start at zero: startup.c does not clear the RAM (see ngc_vm_start).
          "    ngc_clear((UBYTE *)ngc_palette_shadow, sizeof(ngc_palette_shadow));",
          *(["    fade_running = 0u; fade_frames_per_step = 0u; fade_timer = 0u;",
          "    fade_style = 0u; fade_frame = 0u; fade_direction = FADE_IN;",
          "    ngc_staged_step = 0xFFu; ngc_staged_style = 0u;"] if fade else []),
          "    vm_exception_code = 0u;",
          *(["    events_init(0u);"] if inputs else []),
          *(["    timers_init(0u);"] if timers else []),
          *(["    music_init_events(0u);"] if music_routines else []),
          *(["    ngc_link_init();                 /* core.c: SIO_init */"] if link else []),
          *(["    fade_init();", f"    fade_style = {fade_style}u;  /* engine field fade_style */"] if fade else []), "}",
          "/* load_scene: the scene's init thread (bank 0). */",
          "void ngc_vm_run(unsigned short addr)", "{",
          "    (void)script_execute(0u, (UBYTE *)(ngc_banks[0] + addr), 0, 0u);", "}",
          "/* EXCEPTION_CHANGE_SCENE: kill all threads, but don't clear variables. */",
          "void ngc_vm_kill(void)", "{", "    script_runner_init(0u);",
          *(["    events_init(0u);                  /* reset input events on scene change */"]
            if inputs else []),
          *(["    timers_init(0u);                  /* reset timers on scene change */"] if timers else []),
          *(["    music_init_events(0u);            /* reset music events */"] if music_routines else []),
          "}",
          "/* After RUNNER_EXCEPTION: the scene of a change (its far pointer: bank, then",
          " * the scene index, little-endian), or 0xFFFF for an exception not delivered,",
          " * which core.c ignores. */",
          *(["/* load_save.c on the flash journal (docs/M5_SAVE.md, save_journal.c);",
             " * the producer and reader are game.c's (ngpcraft/save_emit.py). */",
             "unsigned char ngc_journal_save(unsigned char slot, unsigned char (*produce)(void));",
             "const volatile unsigned char * ngc_journal_find(unsigned char slot, unsigned short * length);",
             "unsigned char ngc_save_produce(void);", "unsigned char ngc_save_restore(unsigned short length);"]
            if save else []),
          *(["/* EXCEPTION_SAVE is handled here (data_save, then the scripts go on: core.c",
             " * `continue`); EXCEPTION_LOAD returns 0xFFF0 + its slot. */"] if save else []),
          "unsigned short ngc_vm_exception(void)", "{", "    const UBYTE * p;",
          *(["    p = (const UBYTE *)vm_exception_params_offset;",
             f"    if (vm_exception_code == {save[0]}u) {{          /* EXCEPTION_SAVE */",
             "        if (p[0] < 3u) (void)ngc_journal_save(p[0], ngc_save_produce);", "        return 0xFFFFu;", "    }",
             f"    if (vm_exception_code == {save[1]}u) return (UWORD)(0xFFF0u | (UWORD)(p[0] & 0x0Fu));  /* EXCEPTION_LOAD */"]
            if save else []),
          f"    if (vm_exception_code != {change_scene}u) return 0xFFFFu;  /* EXCEPTION_CHANGE_SCENE */",
          "    p = (const UBYTE *)vm_exception_params_offset;",
          "    return (UWORD)((UWORD)p[1] | ((UWORD)p[2] << 8));", "}",
          *(["/* core.c EXCEPTION_LOAD up to load_scene: fade_out_modal, then data_load",
             " * (vm_loaded_state: the state of the slot was restored). No record of this",
             " * build in the slot: nothing restored, as data_load returns FALSE. */",
             "void ngc_vm_load(unsigned char slot)", "{", "    const volatile unsigned char * p;",
             "    unsigned short length;", "    fade_out_modal();", "    vm_loaded_state = 0u;",
             "    if (slot > 2u) return;", "    p = ngc_journal_find(slot, &length);",
             "    if (p) vm_loaded_state = ngc_save_restore(length);", "}"]
            if save and save[2] else []),
          *(["/* The scene loader wrote its colours into the shadow: show them at the",
          " * current fade step (GBVM loads palettes into buffers the fade applies).",
          " * The staged step was computed from the previous scene: recompute. */"] if fade else [
          "/* load_scene fills palette buffers; publish them during VBlank. */"]),
          "void ngc_palette_show(void)", "{",
          *(["    ngc_staged_step = 0xFFu;", "    ApplyPaletteChangeColor(fade_timer);"] if fade else [
              "    /* No fade requested: publish load_scene's palette buffers directly.",
              "     * K2GE palette words: sprites 0x8200, SCR1 0x8280, SCR2 0x8300. */",
              "    const UWORD * src = ngc_palette_shadow;",
              "    volatile UWORD * dst = (volatile UWORD *)0x8200;",
              "    UBYTE n;",
              "    for (n = 192u; n != 0u; --n) *dst++ = *src++;",
              "    *(volatile UWORD *)0x83E0 = *src;"]), "}",
          "/* vm.h VM_ISLOCKED, for the scene engine's actors_update. */",
          "unsigned char ngc_vm_locked(void)", "{", "    return (unsigned char)(vm_lock_state != 0u);", "}"]
    return "\n".join(c) + "\n"


# Text Draw on the background: ui.c prints into the SCR2 map (emit_ui._bkg_text_support).
BKG_TEXT = re.compile(r"^\s*VM_SWITCH_TEXT_LAYER\s+\.TEXT_LAYER_BKG\b", re.M)


def scene_frames(project, tilesets, text, warnings):
    """Set Dialogue Frame: [(tileset index, nine GB tiles)] of the 24 x 24
    tilesets the scripts select (the default frame needs none)."""
    from ..ui_text import _ui_image
    from pathlib import PurePosixPath
    out = []
    for symbol in frame_tilesets(text, "Set Dialogue Frame"):
        if symbol == "tileset_default_frame":
            continue
        index = next(i for i, t in enumerate(tilesets) if t["symbol"] == symbol)
        rel = str(PurePosixPath(tilesets[index]["_rel"]).with_suffix(""))
        out.append((index, _ui_image(project, rel, warnings)[:9]))
    return out


def with_called_scripts(texts, shared):
    """Append the additional scripts the texts reference, recursively: custom
    scripts (VM_CALL_FAR), input, timer, thread and music routine scripts (VM_CONTEXT_PREPARE,
    VM_TIMER_PREPARE, VM_BEGINTHREAD, VM_MUSIC_ROUTINE), their
    recursion placeholders replaced (compileData.ts replaceScriptSymbols)."""
    additional, mapping = shared["additionalScripts"], shared["recursiveSymbolMap"]

    def resolved(text):
        for key, symbol in mapping.items():
            text = text.replace(key, symbol)
        return text
    out, seen, pending = list(texts), set(), list(texts)
    # Scripts already in the bank (an actor's interact script, called by
    # Invoke Actor Script, ECARTS 25) need no copy.
    seen.update(m for t in texts for m in re.findall(r"^_(\w+)::", t, re.M))
    while pending:
        for macro, symbol in re.findall(r"(VM_CALL_FAR|VM_CONTEXT_PREPARE|VM_TIMER_PREPARE|VM_BEGINTHREAD|VM_MUSIC_ROUTINE|VM_PUSH_CONST)\s+(?:\d+,\s*)?___bank_(\w+)",
                                        pending.pop(0)):
            if symbol in seen:
                continue
            if symbol.endswith("_interact"):
                raise CompileError(f"{macro} {symbol}: Invoke Actor Script calls an interact script this scene "
                                   "does not run (actor with a collision group, or a scene type without "
                                   "interactions): not delivered")
            if symbol not in additional:
                raise CompileError(f"{macro} {symbol}: no compiled script of that name")
            seen.add(symbol)
            text = resolved(additional[symbol]["compiledScript"])
            out.append(text)
            pending.append(text)
    return out


def translated_features(project, features):
    """plugin:<id> for each translated engine plugin replacing a unit the export uses
    (runtime_units.select_units swaps that unit for the translation's)."""
    from ..translated import UNIT_FEATURES
    out = []
    for tid, entry in project.translated.items():
        for target in entry["manifest"]["replaces"]:
            if UNIT_FEATURES[target] in features:
                out.append(f"plugin:{tid}")
    return out


def _translated_widened(project):
    from ..scene_platform import project_runtime, widened_fields
    return widened_fields(project_runtime(project))


def _translated_fields(project):
    from ..translated import engine_fields
    return engine_fields(project, load_gbstudio_model()["engineFields"])


def engine_field(project, key):
    """compileBootstrap.ts: the project's value, else the engine default;
    numbers are floored (roundValue)."""
    from ..translated import engine_fields
    fields = engine_fields(project, load_gbstudio_model()["engineFields"])
    if key not in fields:
        raise CompileError(f"engine field {key} unknown to the GB Studio 4.3.2 engine")
    values = [v for _, res in project.by_type["engineFieldValues"] for v in res["engineFieldValues"]
              if v["id"] == key]
    value = values[0]["value"] if values and "value" in values[0] else fields[key]["defaultValue"]
    if isinstance(value, float):
        value = math.floor(value)
    return value


def scene_avatar_glyphs(project, shared, texts, scene):
    """The avatar glyphs a scene's assembled texts print (ngpcraft/avatars.py)."""
    from ..avatars import avatar_fonts, printed_glyphs, scene_glyphs
    where = f"scene {scene['name']}"
    printed = printed_glyphs(texts, len(shared["fonts"]), where)
    if not printed:
        return ()
    if "_avatar_fonts" not in shared:
        shared["_avatar_fonts"] = avatar_fonts(project, shared["warnings"])
    return scene_glyphs(shared["_avatar_fonts"], printed, where)


def text_font_options(project):
    """The scripts' font options: ui_fonts in GB Studio's order (ui_text.used_fonts,
    the default first), each with its character mapping."""
    from ..fonts import read_metadata
    from ..avatars import avatar_options
    from ..ui_text import used_fonts
    if not project.by_type["font"]:
        return {"fonts": [], "defaultFontId": project.settings.get("defaultFontId")}
    fonts = [{"id": res["id"], "mapping": read_metadata(project.root / PurePosixPath(rel).with_suffix(""))[0],
              "symbol": res.get("symbol", ""), "_font": (rel, res)} for rel, res in used_fonts(project)]
    return {"fonts": fonts, "defaultFontId": project.settings.get("defaultFontId"),
            # compileData.ts precompileAvatars: usedAvatars (ngpcraft/avatars.py).
            "avatars": avatar_options(project)}


def font_constants(shared):
    """compileGameGlobalsInclude: FONT symbol (upper case) = its ui_fonts index."""
    return {f["symbol"].upper(): i for i, f in enumerate(shared.get("fonts", ())) if f.get("symbol")}


def project_constants(project):
    """variables.gbsres constants: {id: constant} (scriptBuilder constantsLookup)."""
    return {c["id"]: c for _, res in project.by_type["variables"] for c in res.get("constants", ())}


def global_constants(shared):
    """compileGameGlobalsInclude: SYMBOL = value of each project constant with a
    symbol (upper case), then the font symbols. A value the Game Boy assembler
    could not take as a 16-bit integer, or a symbol defined twice, is refused."""
    out = {}
    for constant in shared.get("constantsLookup", {}).values():
        if not constant.get("symbol"):
            continue
        name, value = constant["symbol"].upper(), constant.get("value")
        where = f"constant {constant.get('name') or name}"
        if type(value) is not int:
            raise CompileError(f"{where}: value {value!r} is not an integer")
        if not -32768 <= value <= 65535:
            raise CompileError(f"{where}: value {value} does not fit 16 bits")
        if name in out:
            raise CompileError(f"{where}: symbol {name} is defined twice")
        out[name] = value
    for name, index in font_constants(shared).items():
        if name in out:
            raise CompileError(f"font symbol {name} is also a project constant")
        out[name] = index
    return out


def text_fonts(project, shared, texts):
    """ui_data's fonts: (ui_fonts text font count, [(index, FontTable)]) of the
    fonts the reachable scripts can select (all of them with a `%f` variable)."""
    from ..avatars import printed_fonts
    from ..ui_text import font_table
    fonts = shared["fonts"]
    chosen, variable = printed_fonts(texts, len(fonts), "text fonts", font_constants(shared))
    indices = range(len(fonts)) if variable else sorted(chosen)
    return len(fonts), [(i, font_table(project, fonts[i]["_font"])) for i in indices]


def loaded_projectiles(registry, texts, tables):
    """Load Projectile Slot in these scripts: one selection per global entry
    loaded (VM_PROJECTILE_LOAD_TYPE dest, src, ___bank_<table>), definition id
    SLOT_BASE + 5 * table + src, its fields checked as a scene projectile's."""
    from ..projectile_runtime import SLOT_BASE
    from ..projectiles import ProjectileSelection
    index = {t["symbol"]: k for k, t in enumerate(tables)}
    out = {}
    for src, symbol in re.findall(r"VM_PROJECTILE_LOAD_TYPE\s+\d+,\s*(\d+),\s*___bank_(\w+),", "\n".join(texts)):
        if symbol not in index:
            raise CompileError(f"VM_PROJECTILE_LOAD_TYPE {symbol}: no global projectile table of that name")
        k, src = index[symbol], int(src)
        gid = SLOT_BASE + 5 * k + src
        if gid not in out:
            where = f"global projectile {symbol}[{src}]"
            out[gid] = ProjectileSelection(gid, registry._key(tables[k]["projectiles"][src], where), where)
    return tuple(out[g] for g in sorted(out))


def projectile_scene(project, scene, registry, texts, hits, addresses, compiled_hits, global_tables=()):
    """The projectiles a scene's scripts launch (VM_PROJECTILE_LAUNCH operands),
    its actor groups and hit bindings, and the extra VM contexts; None if none.

    GBVM projectiles.c runs the hit actor's `script` (compileData.ts: the
    combined collision script; PLAYER: the scene's player hit script) with the
    projectile's group, through one hscript_hit handle per actor. Per bound
    actor: the running hit plus one detached generation (deactivated while its
    hit runs, then hit again), each possibly terminated but not yet reaped;
    activations inside a hit script reset one more handle each, counted for
    the script that performs them (its 4 contexts x 2), not for every binding."""
    used = {int(n) for n in re.findall(r"VM_PROJECTILE_LAUNCH\s+(\d+),", "\n".join(texts))}
    if not used:
        return None
    if scene["type"] not in ("TOPDOWN", "PLATFORM", "SHMUP", "ADVENTURE"):
        # build_collisions compiles no hit script here: refuse instead of losing them.
        from ..scene_init import _Builder
        b = _Builder(project, load_gbstudio_model())
        keys = [("scene", scene, ("playerHit1Script", "playerHit2Script", "playerHit3Script"))]
        keys += [(b.path_of[a["id"]], b.actor(a)[0], ("hit1Script", "hit2Script", "hit3Script"))
                 for a in scene["actors"]]
        for where, entity, names in keys:
            if (entity is scene or entity["collisionGroup"]) and any(entity.get(k) for k in names):
                raise CompileError(f"scene {scene['name']}: {where}: hit scripts in a {scene['type']} scene "
                                   "are not delivered; projectiles could not run them")
    selections = tuple(s for s in registry.selections if s.index in used) + loaded_projectiles(registry, texts, global_tables)
    bindings = {h.index: (0, addresses[compiled_hits[h.entity["id"]][0]]) for h in hits}

    def activations(events):
        count = 0
        for event in events or ():
            if (event.get("args") or {}).get("__comment"):
                continue
            count += event["command"] == "EVENT_ACTOR_ACTIVATE"
            count += sum(activations(child) for child in (event.get("children") or {}).values())
        return count
    return selections, collision_groups(project, scene), bindings, projectile_hit_contexts(
        len(bindings), [activations(h.events) for h in hits])


def projectile_hit_contexts(bindings, activations):
    """VM contexts of projectile hits: 4 per bound entity, plus 8 per actor
    activation in a hit script (each of its 4 contexts may reset one more
    handle, itself running or awaiting its reaping)."""
    return 4 * bindings + 8 * sum(activations)


# One cc900 SCRIPT_CTX, alignment included (docs/QUALIFICATION_M0_S1.md,
# re-measured in the linked map by tests/test_context_budget.py).
CONTEXT_HEADER_BYTES = 34
# RAM given to VM contexts (headers + stacks) when the conservative estimate
# is larger: about 40 % of the 10 240 static bytes left by the stack reserve.
CONTEXT_RAM_BUDGET = 4096
GBVM_CONTEXTS = 16      # the pinned GBVM pool: never fewer than GB Studio
# A saving game snapshots the whole pool (save_vm.c NV1: SV_HEADER 12, then
# SV_CONTEXT 17 bytes per context and the heap + stacks words). Share of the
# 2537-byte journal payload (save_state.check_capacity) given to that VM part;
# the linked sizes stay the final gate (save_emit.check_rom).
SAVE_VM_BUDGET = 1792
SV_HEADER, SV_CONTEXT = 12, 17


def fit_contexts(capacity, stride, *, heap_words=0, saves=False):
    """Automatic NGPC context count (docs/M4_CONTEXT_BUDGET.md).

    The conservative estimate is kept when it fits the context RAM budget
    (and, for a saving game, its share of the save payload); otherwise the
    pool is the largest that fits, never below GBVM's 16. Beyond the pool
    script_execute returns NULL, as GBVM when full.
    Returns (contexts, report or None when the estimate is kept)."""
    if capacity is None:
        return None, None
    fit = CONTEXT_RAM_BUDGET // (CONTEXT_HEADER_BYTES + 2 * stride)
    limits = {"ram": fit}
    if saves:
        limits["save"] = (SAVE_VM_BUDGET - SV_HEADER - 2 * heap_words) // (SV_CONTEXT + 2 * stride)
    chosen = max(GBVM_CONTEXTS, min(limits.values()))
    if capacity <= chosen:
        return capacity, None
    report = {"conservative_estimate": capacity, "contexts": chosen,
              "words_per_context": stride, "limits": limits,
              "ram_budget_bytes": CONTEXT_RAM_BUDGET, "header_bytes": CONTEXT_HEADER_BYTES,
              "ram_bytes": chosen * (CONTEXT_HEADER_BYTES + 2 * stride),
              "on_exhaustion": "script_execute returns NULL, as GBVM when its 16 contexts are full"}
    if saves:
        report["save_vm_budget_bytes"] = SAVE_VM_BUDGET
        report["save_vm_bytes"] = SV_HEADER + SV_CONTEXT * chosen + 2 * (heap_words + chosen * stride)
    return chosen, report


def pause_state_game(game):
    """EVENT_SCENE_UPDATE_PAUSE / RESUME: core.c runs state_update only while
    pause_state_update is false; load_scene sets it false (each scene load,
    Load Data's resume included: before ngc_actors_load / ngc_actors_resume)."""
    out, calls, loads = [], 0, 0
    for line in game:
        if line.strip() in ("ngc_actors_load();", "ngc_actors_resume();"):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + "ngc_pause_state_update = 0u;  /* load_scene: pause_state_update = false */")
            loads += 1
        m = re.fullmatch(r"(\s*)(if \(!ngc_vm_locked\(\)\) )?ngc_(topdown|platform|shmup|pointnclick|adventure)_update\(\);", line)
        if m:
            call = f"if (!ngc_pause_state_update) ngc_{m[3]}_update();"
            line = m[1] + (f"{m[2]}{{ {call} }}" if m[2] else call)
            calls += 1
        out.append(line)
    if not loads or not calls:
        raise CompileError("scene update pause: game.c anchors changed (no state update or no scene load)")
    return ["extern unsigned char ngc_pause_state_update;"] + out


def camera_properties_game(game):
    """Camera Property Set: camera.c camera_init (boot) zeroes the offsets;
    scene loads keep them (scene_engine.py moved their reset here)."""
    text = "\n".join(game)
    init = "void ngc_game_init(void)\n{\n"
    if text.count(init) != 1:
        raise CompileError("camera properties: game.c anchors changed")
    text = text.replace(init, init + "    camera_offset_x = 0;              /* camera.c camera_init */\n"
                                     "    camera_offset_y = 0;\n")
    return ("extern signed char camera_offset_x, camera_offset_y;\n" + text).split("\n")


def sprites_visible_game(game):
    """EVENT_SHOW_SPRITES / EVENT_HIDE_SPRITES: GBVM hide_sprites (FALSE at boot)
    gates each VBlank sprite copy. ngc_game_init is the boot, not a scene load."""
    out = []
    for line in game:
        if line.strip() == "ngc_sprites_commit();":
            line = line.replace("ngc_sprites_commit();", "if (ngc_sprites_hidden) ngc_sprites_hide_all(); else ngc_sprites_commit();")
        out.append(line)
    text = "\n".join(out)
    init = "void ngc_game_init(void)\n{\n"
    if text.count(init) != 1 or "ngc_sprites_commit();" not in "\n".join(game):
        raise CompileError("sprite visibility: game.c anchors changed")
    text = text.replace(init, init + "    ngc_sprites_hidden = 0u;           /* interrupts.c: hide_sprites FALSE */\n")
    head = "extern unsigned char ngc_sprites_hidden;\nvoid ngc_sprites_hide_all(void);\n"
    return (head + text).split("\n")


def gbvm_referenced_variables(project, variables_lookup):
    """compileData.ts: the variables an EVENT_GBVM_SCRIPT lists in its references
    are declared before any event alias (determineUsedAssets addVariableById):
    the variable's symbol, else VAR_<id>; events then reuse that alias
    (getVariableAlias existingAlias). Scenes and the custom scripts they call."""
    scripts = {res["id"]: res for _, res in project.by_type["script"]}
    found, seen = {}, set()

    def walk(value, depth):
        if isinstance(value, list):
            for item in value:
                walk(item, depth)
        elif isinstance(value, dict):
            args = value.get("args") if isinstance(value.get("args"), dict) else {}
            if args.get("__comment"):
                return
            command = value.get("command")
            if command == "EVENT_GBVM_SCRIPT" and isinstance(args.get("references"), list):
                for ref in args["references"]:
                    if isinstance(ref, dict) and ref.get("type") == "variable" and ref.get("id") not in found:
                        named = variables_lookup.get(ref["id"])
                        symbol = (named["symbol"] if named and named.get("symbol") else f"VAR_{ref['id']}").upper()
                        found[ref["id"]] = {"symbol": symbol, "id": ref["id"], "name": named["name"] if named else symbol,
                                            "isLocal": False, "entityType": "scene", "entityId": "", "sceneId": ""}
            if command == "EVENT_CALL_CUSTOM_EVENT" and args.get("customEventId") in scripts                     and args["customEventId"] not in seen and depth < MAX_NESTED_SCRIPT_DEPTH:
                seen.add(args["customEventId"])
                walk(scripts[args["customEventId"]].get("script"), depth + 1)
            for key, child in value.items():
                if key != "args":
                    walk(child, depth)
    walk(project.scenes, 0)
    return found


def generate_project(project, inits, variables_lookup):
    from ..project_sfx import sound_options
    tick_frames = 2 if frame_rate(project) == 30 else 1
    """Loaded project + C10 threads -> export files (target ngpc-rom)."""
    start_id = project.settings["startSceneId"]
    scenes = {s.scene_id: s for s in inits.scenes}
    if start_id not in scenes:
        raise CompileError(f"settings.startSceneId {start_id!r} is not a compiled scene")
    resources = {s["id"]: s for s in project.scenes}
    entities = {r["id"]: r for s in project.scenes for r in [s, *s["actors"]]}
    aliases, compiled, compiled_updates = {}, {}, {}
    aliases.update(gbvm_referenced_variables(project, variables_lookup))
    updates = build_updates(project)
    interactions = build_interactions(project)
    compiled_interactions = {}
    triggers = build_triggers(project)
    compiled_triggers = {}
    hits = build_collisions(project)
    compiled_hits = {}
    scene_list = [resources[s.scene_id] for s in inits.scenes]
    # compileSprites.ts statesOrder, shared with every compiled script.
    from ..scene_platform import states_order
    animation_states = states_order(project)
    # compileData.ts: options shared by every script of the export.
    sprite_options = [{"id": s["id"], "symbol": s["symbol"]} for _, s in project.by_type["sprite"]]
    emote_options = [{"id": e["id"], "symbol": e["symbol"]} for _, e in project.by_type["emote"]]
    tileset_list = tileset_options(project)
    sprite_symbols = {s["symbol"]: i for entries in (sprite_options, emote_options, tileset_list)
                      for i, s in enumerate(entries)}
    sprite_symbols["tileset_default_frame"] = DEFAULT_FRAME
    shared = {"music": music_options(project),
              # scriptBuilder options.debugEnabled: GBVM$ / GBVM_END$ symbols (docs/M7_DEBUG.md).
              "debugEnabled": project.debug,
              # A copy: default built-in effects are added at compile time (project_sfx).
              # loadProjectile's global tables (getGlobalProjectile), one list for the export.
              "globalProjectiles": [],
              "nativeSfx": dict(project.ngpc["ngpc"].get("native_sfx", {})),
              # compileData options.sounds: the effect index soundPlay plays.
              "sounds": sound_options(project), "scenes": scene_list, "sprites": sprite_options, "emotes": emote_options, "tilesets": tileset_list,
              "statesOrder": animation_states, "stateReferences": [str(i) for i in range(len(animation_states))],
              "customEvents": [{**res, "_path": path} for path, res in project.by_type["script"]],
              "compiledCustomEventScriptCache": {}, "additionalScripts": {}, "additionalScriptsCache": {},
              "symbols": {}, "recursiveSymbolMap": {}, "maxDepth": MAX_NESTED_SCRIPT_DEPTH,
              # Event plugins (docs/M7_PLUGINS.md): compiled through the project's bridge.
              "pluginEvents": {command: project.plugin_bridge for command in project.plugin_events},
              # compileData engineFields with the translated engine plugins' fields (none: the frozen model).
              **({"engineFields": _translated_fields(project),
                  "translatedWidenedFields": _translated_widened(project)} if project.translated else {}),
              # Located warnings of every compiled script (ScriptBuilder.warn), export.json "warnings".
              "warnings": [],
              # compileData.ts precompileScenes: scene.projectiles, read by getProjectileIndex.
              "projectiles": {s.scene_id: scene_registry(project, resources[s.scene_id]) for s in inits.scenes},
              # compileData.ts: fonts (usedFonts, the default first) and defaultFontId, for text.
              **text_font_options(project),
              # scriptBuilder options.palettes / settings, for the palette events.
              # scriptBuilder options.constantsLookup (getConstantSymbol); values: global_constants.
              "constantsLookup": project_constants(project),
              "palettes": {res["id"]: tuple(res["colors"]) for _, res in project.by_type.get("palette", ())},
              "paletteSettings": {"colorMode": project.settings["colorMode"],
                                  "defaultBackgroundPaletteIds": project.settings["defaultBackgroundPaletteIds"],
                                  "defaultSpritePaletteIds": project.settings["defaultSpritePaletteIds"]}}
    for index, init in enumerate(inits.scenes):
        compiled[init.scene_id] = compile_scene_init(init, resources[init.scene_id], index,
                                                     variables_lookup, aliases, entities, shared)
        for update in updates:
            if update.scene_id == init.scene_id:
                compiled_updates[update.actor["id"]] = compile_actor_update(
                    update, resources[init.scene_id], index, variables_lookup, aliases, entities, shared)
        for interaction in interactions:
            if interaction.scene_id == init.scene_id:
                compiled_interactions[interaction.actor["id"]] = compile_actor_interaction(
                    interaction, resources[init.scene_id], index, variables_lookup, aliases, entities, shared)
        for trigger in triggers:
            if trigger.scene_id == init.scene_id:
                compiled_triggers[trigger.trigger['id']] = compile_trigger(
                    trigger, resources[init.scene_id], index, variables_lookup, aliases, entities, shared)
        for hit in hits:
            if hit.scene_id == init.scene_id:
                compiled_hits[hit.entity['id']] = compile_collision(
                    hit, resources[init.scene_id], index, variables_lookup, aliases, entities, shared)
    order = js_object_keys(aliases)
    variables = {aliases[k]["symbol"]: i for i, k in enumerate(order)}
    model = load_model()
    graph = scene_graph(project, inits, updates + interactions + triggers + hits)
    if any(e["from"] in graph["reachable"] for e in graph["transitions"]):
        return generate_scenes(project, inits, graph, resources, variables, model, shared,
                               (updates, compiled_updates, lambda u: u.actor["id"]),
                               (interactions, compiled_interactions, lambda u: u.actor["id"]),
                               (triggers, compiled_triggers, lambda t: t.trigger["id"]),
                               (hits, compiled_hits, lambda h: h.entity["id"]), compiled)
    symbol, text = compiled[start_id]
    active_updates = [u for u in updates if u.scene_id == start_id]
    active_interactions = [u for u in interactions if u.scene_id == start_id]
    active_triggers = [t for t in triggers if t.scene_id == start_id]
    active_hits = [h for h in hits if h.scene_id == start_id]
    texts = with_called_scripts([text] + [compiled_updates[u.actor["id"]][1] for u in active_updates] +
                                [compiled_interactions[u.actor["id"]][1] for u in active_interactions] +
                                [compiled_triggers[t.trigger["id"]][1] for t in active_triggers] +
                                [compiled_hits[h.entity["id"]][1] for h in active_hits], shared)
    sprite_symbols.update(select_music(shared, texts))
    sprite_symbols.update(global_projectile_symbols(shared))
    frame_tilesets("\n".join(texts), "export")
    shared["warnings"] += link_slot_warnings(texts, variables)
    data, bank_scripts, macros, rpn = assemble_bank(texts, variables, model, sprites=sprite_symbols,
                                                    constants=global_constants(shared),
                                                    scenes=scene_symbols(project, {resources[start_id]["symbol"]: 0}))
    stack_used = sorted(m for m in SCENE_INSTRUCTIONS if m in macros)
    if stack_used:
        raise CompileError(f"{', '.join(stack_used)}: the scene stack is delivered with scene changes "
                           "(Switch Scene) only; this export has a single reachable scene")
    if ("VM_POLL_LOADED" in macros or "VM_SAVE_PEEK" in macros or "VM_SAVE_CLEAR" in macros
            or re.search(r"^\s*VM_RAISE\s+EXCEPTION_(?:SAVE|LOAD)\b", "\n".join(texts), re.M)):
        raise CompileError("Save Data / Load Data / If Data Saved are delivered with scene changes (Switch Scene) "
                           "only (docs/M5_SAVE.md); this export has a single reachable scene")
    entry = next(s for s in bank_scripts if s.symbol == symbol)
    text = "\n".join(texts)  # dependencies of every reachable script
    addresses = {s.symbol: s.address for s in bank_scripts}
    actor_updates = {u.actor_index + 1: addresses[compiled_updates[u.actor["id"]][0]] for u in active_updates}
    actor_interactions = {u.actor_index + 1: addresses[compiled_interactions[u.actor["id"]][0]] for u in active_interactions}
    trigger_data = [(t.trigger, addresses[compiled_triggers[t.trigger['id']][0]]) for t in active_triggers]
    capacity = context_capacity(scenes[start_id], active_updates + active_interactions, active_triggers) if active_updates or active_interactions or active_triggers or active_hits else None
    hit_data = None
    if active_hits:
        capacity += collision_capacity(active_hits, {res["id"]: res for _, res in project.by_type["script"]},
                                       shared["warnings"])
        hit_data = {'groups': collision_groups(project,resources[start_id]),
                    'actors': {h.index: addresses[compiled_hits[h.entity['id']][0]] for h in active_hits if h.index},
                    'player': next((addresses[compiled_hits[h.entity['id']][0]] for h in active_hits if not h.index),None)}
    elif needs_player_collisions(project, resources[start_id]):
        # shmup.c reads player_iframes: the bookkeeping runs without any script.
        hit_data = {'groups': collision_groups(project, resources[start_id]), 'actors': {}, 'player': None}
    projectiles = projectile_scene(project, resources[start_id], shared["projectiles"][start_id], texts,
                                   active_hits, addresses, compiled_hits, shared["globalProjectiles"])
    if projectiles and projectiles[2]:
        capacity += projectiles[3]
    inputs = any(m in macros for m in INPUT_MACROS)
    if inputs:
        # One running + one terminated-not-reaped thread per input slot.
        capacity = ((capacity if capacity is not None else context_capacity(scenes[start_id], []))
                    + 2 * len(input_slots(text)))
    callbacks = callback_slots(text)
    if callbacks:
        capacity = (capacity if capacity is not None else context_capacity(scenes[start_id], [])) + 2 * len(callbacks)
    adv_callbacks = callback_slots(text, "adventure")
    if adv_callbacks:
        capacity = (capacity if capacity is not None else context_capacity(scenes[start_id], [])) + 2 * len(adv_callbacks)
    timers = timer_slots(text)
    if timers:
        capacity = (capacity if capacity is not None else context_capacity(scenes[start_id], [])) + 2 * len(timers)
    # Music routines need a song: without a reachable Play Music none runs.
    routines = routine_slots(text) if "VM_MUSIC_PLAY" in macros else []
    if routines:
        capacity = (capacity if capacity is not None else context_capacity(scenes[start_id], [])) + 2 * len(routines)
    threads = thread_sites(text)
    if threads:
        capacity = (capacity if capacity is not None else context_capacity(scenes[start_id], [])) + 2 * threads
    contexts = capacity if active_updates else None
    topdown = scene_state(project, resources[start_id], bool(callbacks), bool(adv_callbacks))
    # scriptBuilder._invoke declares b_<fn> as a dependency of the script.
    natives = sorted(n for n in NATIVES if f"b_{n}" in text)
    # _setConstMemInt8 and its siblings declare _<variable> as a dependency.
    memory = engine_memory(text, macros, model)
    runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
    rpn = rpn_cases(runtime, rpn)
    fade_style = engine_field(project, "fade_style")
    if type(fade_style) is not int or fade_style not in (0, 1):
        raise CompileError(f"engine field fade_style = {fade_style!r}: expected 0 (white) or 1 (black)")
    # Scene engine (M2) when the start scene allows it; a scene blocked only by an
    # open decision keeps the M1 scripts-only export, and the manifest says why.
    blocker = engine_blocker(project, resources[start_id])
    if blocker and tick_frames == 2:
        raise CompileError(f"ngpcraft.json ngpc.frame_rate 30: the start scene has no scene engine ({blocker}); "
                           "the fixed cadence paces scene ticks")
    used_actor = sorted(ACTOR_INSTRUCTIONS & set(macros))
    validate_logo_scripts(resources[start_id], macros, text)
    if blocker and inputs:
        raise CompileError(f"input scripts need the scene engine, which this start scene cannot use: {blocker}")
    if blocker and (used_actor or active_updates):
        raise CompileError(f"{', '.join(used_actor)}: actor instructions need the scene engine, "
                           f"which this start scene cannot use: {blocker}")
    prints = any(m in macros for m in UI_DECODERS)            # EVENT_TEXT (docs/M2_TEXT.md)
    if blocker and prints:
        raise CompileError(f"text needs the scene engine, which this start scene cannot use: {blocker}")
    if blocker and set(macros) & {"VM_CAMERA_MOVE_TO", "VM_CAMERA_SET_POS"}:
        raise CompileError(f"camera instructions need the scene engine, which this start scene cannot use: {blocker}")
    moves = move_features(text, model)
    cancel = "VM_ACTOR_MOVE_CANCEL" in macros
    palette_slots = changed_slots(texts, f"scene {resources[start_id]['name']}")
    if blocker and palette_slots:
        raise CompileError(f"palette changes need the scene engine, which this start scene cannot use: {blocker}")
    engine_c, engine_report = ({}, {"built": False, "reason": blocker}) if blocker else \
        engine_files(build_engine_scene(project, resources[start_id], sprite_switches=sprite_assets(texts, shared["sprites"]), emotes=emote_assets(texts, shared["emotes"]),
                                        replace_ops=replace_ops(texts), palette_slots=palette_slots,
                                        projectiles=projectiles[0] if projectiles else ()), moves, actor_updates, cancel, topdown,
                     actor_interactions, trigger_data, hit_data, text=prints,
                     projectile_groups=projectiles[1] if projectiles else None,
                     projectile_scripts=projectiles[2] if projectiles and projectiles[2] else None,
                     projectile_modal=bool(projectiles) and prints,
                     stop_update="VM_ACTOR_TERMINATE_UPDATE" in macros,
                     start_update="VM_ACTOR_BEGIN_UPDATE" in macros,
                     set_bounds="VM_ACTOR_SET_BOUNDS" in macros,
                     actor_states=shared["statesOrder"] if "VM_ACTOR_SET_ANIM_SET" in macros else None,
                     sprite_states=shared["statesOrder"],
                     read_positions="VM_ACTOR_GET_POS" in macros, read_directions=("VM_ACTOR_GET_DIR" in macros or "VM_ACTOR_GET_ANGLE" in macros),
                     push="VM_ACTOR_MOVE_TO" in macros, split_moves=bool(set(macros) & set(MOVE_INSTRUCTIONS)), camera_shake="camera_shake_frames" in natives,
                     camera_control=bool(set(macros) & {"VM_CAMERA_MOVE_TO", "VM_CAMERA_SET_POS"}),
                     camera_properties=any(n in memory for n in CAMERA_PROPERTIES),
                     scroll_bounds=any(n in memory for n in SCROLL_BOUNDS))
    second = not blocker and "second_background" in engine_report
    ui = None
    if prints:
        check_tiles(engine_report, f"scene {resources[start_id]['name']}")
        tall = check_window_commands(text, f"scene {resources[start_id]['name']}",
                                     shared=engine_report["planes"]["case"] == "B")
        start_ui = scene_ui(project, resources[start_id], engine_report["view"]["top_band"],
                            shared["warnings"],
                            palette_base=engine_report["ui_palette_base"],
                            avatar_glyphs=scene_avatar_glyphs(project, shared, [text],
                                                              resources[start_id]),
                            logical_map=tall,
                            shared_plane=engine_report["planes"]["case"] == "B",
                            palette_slot=7 in palette_slots,
                            bkg_slot=engine_report["scr2_palettes"] if BKG_TEXT.search(text) else None,
                            frames=scene_frames(project, tileset_list, "\n".join(texts), shared["warnings"]))
        if start_ui.palette_entries:
            engine_report["ui_palette_entries"] = [list(e) for e in start_ui.palette_entries]
        # Case A window over 13 rows or moved horizontally: the logical map is
        # copied to SCR1 at the commit (emit_ui.TALL_COMMIT), as case B's is.
        engine_report["planes"]["text_logical_map"] = bool(tall)
        ui = ui_support(text_fonts(project, shared, texts), [start_ui], "(UWORD *)0x8280", front_scroll=second,
                        choice="VM_CHOICE" in macros, narrow=narrow_windows("\n".join(texts)),
                        frame_support="VM_REPLACE_TILE" in macros, cut=overlay_cut_live("\n".join(texts)))
    entry_arguments = {done[key(item)][0]: 1 for items, done, key in (
        (active_interactions, compiled_interactions, lambda x: x.actor["id"]),
        (active_triggers, compiled_triggers, lambda x: x.trigger["id"]),
        (active_hits, compiled_hits, lambda x: x.entity["id"])) for item in items}
    if routines:
        entry_arguments.update(routine_scripts(text))
    stacks = stack_budget(bank_scripts, model, runtime, capacity, entry_arguments)
    capacity, context_budget = fit_contexts(capacity, stacks["words_per_context"])
    if context_budget:
        contexts = capacity if active_updates else None
        stacks = stack_budget(bank_scripts, model, runtime, capacity, entry_arguments)
        engine_report["context_budget"] = context_budget
    gbvm_c, handlers = gbvm_source(runtime, macros, rpn, natives, memory, model, fade_style,
                                   scene_palettes=not blocker, update_contexts=contexts,
                                   interaction_contexts=capacity if active_interactions else None,
                                   trigger_contexts=capacity if active_triggers else None,
                                   hit_contexts=capacity if active_hits else None,
                                   input_contexts=capacity if inputs else None, ui=ui, heap_words=len(variables), platform_callbacks=callbacks, callback_contexts=capacity, callback_attach="b_plat_callback_attach" in text, callback_detach="b_plat_callback_detach" in text,
                                   adventure_callbacks=adv_callbacks, adventure_attach=callback_natives(text, "adventure")[0],
                                   adventure_detach=callback_natives(text, "adventure")[1],
                                   stack_words=stacks["words_per_context"], tick_frames=tick_frames, constant_pairs=constant_rpn_pairs(texts, model, rpn), text_sound=text_sound_use(texts),
                                   timer_contexts=capacity if timers else None,
                                   thread_contexts=capacity if threads else None,
                                   projectile_actors=len(projectiles[1]) if projectiles and projectiles[2] else None,
                                   projectile_slots=bool(projectiles) and any(s.index >= SLOT_BASE for s in projectiles[0]),
                                   palette_maps=[[tuple(e) for e in engine_report.get("palette_entries", ())] +
                                                 [tuple(e) for e in (start_ui.palette_entries if prints else ())]])
    if blocker and timers:
        raise CompileError(f"timer scripts need the scene engine, which this start scene cannot use: {blocker}")
    if not blocker:
        engine_report["movement"] = None if moves is None else list(moves)
    if not blocker:
        engine_report = {"built": True, **engine_report}
    features = ["gbvm"] + (["frame_rate_30"] if tick_frames == 2 else []) + ([] if blocker else FEATURES + ["gbvm_scene"] +
                           (["actor_moves"] if set(macros) & set(MOVE_INSTRUCTIONS) else []))
    if second:
        features.append("second_background")
    fades = "vm_fade" in handlers
    if fades:
        features.append("fades")
    if (inputs or prints) and topdown is None:
        engine_c["src/scene_input.c"] = input_source(option_long_press(project))
    if (inputs or prints or timers) and topdown is None and "src/scene_input.c" not in engine_c:
        engine_c["src/scene_input.c"] = input_source(option_long_press(project))
    if topdown is not None or inputs or prints or timers:
        features.append("input")
    if prints:
        features.append("text")
        if engine_report["planes"]["case"] == "B":
            features.append("text_shared")
        if overlay_cut_live("\n".join(texts)):
            features.append("text_cut")
    if timers:
        features.append("timer_events")
        engine_report["timer_slots"] = timers
    if routines:
        features.append("music_routines")
        engine_report["music_routines"] = routines
    features += link_features(macros)
    if inputs:
        features.append("input_events")
        engine_report["input_contexts"] = capacity
        engine_report["input_slots"] = input_slots(text)
    if state_of(topdown) == "topdown":
        features += ["topdown"]
        engine_report["topdown"] = {"grid_px": topdown[0], "interact_mask": topdown[1], "option_long_press": topdown[2]}
    elif state_of(topdown) == "platform":
        features += ["platform"] + (["anim_states"] if len(topdown) > 4 else [])
        engine_report["platform"] = {"option_long_press": topdown[2], "animation_states": len(topdown) > 4}
    elif state_of(topdown) is not None:
        # An Adventure scene loading animation sets (genre_state) links them as the platformer.
        features += [state_of(topdown)] + (["anim_states"] if len(topdown) > 4 else [])
        engine_report[state_of(topdown)] = {"option_long_press": topdown[2], "animation_states": len(topdown) > 4}
    if engine_report.get("parallax"):
        features.append("parallax")
    if hit_data is not None:
        # collision_update (iframes, registration); player_collisions: its scripts.
        features += ["collision_update"] + (["player_collisions"] if active_hits else [])
        engine_report["collision_contexts"] = capacity
        engine_report["collision_scripts"] = len(active_hits)
    if active_triggers:
        features.append("triggers")
        engine_report["trigger_contexts"] = capacity
        engine_report["triggers"] = len(active_triggers)
    if active_interactions:
        features.append("actor_interactions")
        engine_report["interaction_contexts"] = capacity
        engine_report["actor_interactions"] = actor_interactions
    if callback_slots(text):
        features.append("platform_callbacks")
    if callback_slots(text, "adventure"):
        features.append("adventure_callbacks")
    if "camera_shake_frames" in natives:
        features.append("camera_shake")
    if set(macros) & {"VM_CAMERA_MOVE_TO", "VM_CAMERA_SET_POS"}:
        features.append("camera_control")
    if "VM_RATE_LIMIT_CONST" in macros:
        features.append("rate_limit")
    if ("VM_ACTOR_GET_DIR" in macros or "VM_ACTOR_GET_ANGLE" in macros):
        features.append("actor_directions")
    if "VM_ACTOR_MOVE_TO" in macros:
        features.append("actor_push")
    if "VM_ACTOR_GET_POS" in macros:
        features.append("actor_positions")
    if "VM_REPLACE_TILE_XY" in macros:
        features.append("replace_tiles")
    if "VM_ACTOR_EMOTE" in macros:
        features.append("actor_emotes")
    if "VM_ACTOR_SET_SPRITESHEET" in macros:
        features.append("actor_spritesheets")
    if "VM_ACTOR_SET_ANIM_SET" in macros:
        features.append("actor_anim_states")
    if "VM_ACTOR_TERMINATE_UPDATE" in macros:
        features.append("actor_stop_update")
    if "VM_ACTOR_BEGIN_UPDATE" in macros:
        features.append("actor_start_update")
    if "VM_ACTOR_SET_BOUNDS" in macros:
        features.append("actor_set_bounds")
    if "VM_SET_SPRITE_VISIBLE" in macros:
        features.append("sprites_visible")
    if "pause_state_update" in memory:
        features.append("scene_update_pause")
    if any(n in memory for n in CAMERA_PROPERTIES):
        features.append("camera_properties")
    if any(n in memory for n in SCROLL_BOUNDS):
        features.append("scroll_bounds")
    if cancel:
        features.append("actor_move_cancel")
    if projectiles:
        features.append("projectiles")
        if any(s.index >= SLOT_BASE for s in projectiles[0]):
            features.append("projectile_slots")
        if prints:
            features.append("projectile_modal")
        if projectiles[2]:
            features.append("projectile_hits")
            engine_report["projectile_hit_contexts"] = projectiles[3]
    if active_updates:
        features.append("actor_updates")
        engine_report["update_contexts"] = contexts
        engine_report["actor_updates"] = actor_updates
    features += translated_features(project, features)
    units = select_units(features, "rom")
    game = ['#include "game.h"', '#include "gbvm.h"', *([] if blocker else DECLARATIONS),
            *(["void ngc_vm_frame_end(void);"] if fades or prints or not blocker else []),
            *input_game_lines(state_of(topdown), inputs, prints, bool(timers), bool(routines))[0],
            *(UI_DECLARATIONS if prints else []),
            *(["void ngc_collision_update(void);"] if hit_data is not None else []),
            f"/* {symbol}: {scenes[start_id].path}, init thread (contract C10) */",
            "/* Bank 0 (assembler.py: a script address is an offset in its bank). */",
            f"static const unsigned char ngc_bank_0[] = {{{_c_bytes(data)}}};",
            "static const unsigned char * const ngc_banks[] = {ngc_bank_0};",
            *(["void ngc_vm_prepare(const unsigned char * const *banks, unsigned char bank, unsigned short addr);"]
              if active_updates else []),
            "void ngc_game_init(void)", "{",
            *([f"    ngc_vm_prepare(ngc_banks, 0u, {entry.address}u);"] if active_updates else []),
            *(["    ngc_ui_start(0u);                 /* initialize UI before actors render */"] if prints else []),
            *([f"    ngc_{state_of(topdown)}_boot();              /* script_engine_init: engine fields */"]
              if state_of(topdown) in BOOT_STATES else []),
            *([] if blocker else start_lines(engine_report["view"]["height"], state_of(topdown))),
            *input_game_lines(state_of(topdown), inputs, prints, bool(timers), bool(routines))[1],
            *(["    ngc_ui_scene_load(0u);            /* load_scene: UI tiles and palettes */"] if prints else []),
            f"    ngc_vm_start(ngc_banks, 0u, {entry.address}u);  /* {symbol} */", "}",
            *(modal_frame(COMMIT, after_scripts(tick_frames), bool(projectiles)) if prints else []),
            "/* One frame of GBVM's core.c process_VM: run the scheduler until every",
            " * thread waits (RUNNER_BUSY loops without waiting for the frame). */",
            "void ngc_game_tick(void)", "{", "    unsigned char status;",
            *([] if blocker else COMMIT), *(["    ngc_ui_commit();"] if prints else []),
            "    /* Native audio service: after video, before picture logic. */",
            *picture_frames([
                "    do {", "        status = script_runner_update();",
                f"    }} while (status == {runtime['defines']['RUNNER_BUSY']}u);",
                *input_game_lines(state_of(topdown), inputs, prints, bool(timers), bool(routines))[2],
                *([] if blocker else scene_frame_lines(prints)),
                *(["    ngc_collision_update();"] if hit_data is not None else []),
                *([] if blocker else AFTER_SCRIPTS[-1:])], tick_frames),
            *(["    ngc_vm_frame_end();"] if fades or prints or not blocker else []), "}"]
    if "VM_SET_SPRITE_VISIBLE" in macros:
        game = sprites_visible_game(game)
    if "pause_state_update" in memory:
        game = pause_state_game(game)
    if any(n in memory for n in CAMERA_PROPERTIES):
        game = camera_properties_game(game)
    files = {"src/game.h": GAME_H, "src/game.c": "\n".join(game) + "\n", "src/gbvm.h": GBVM_H,
             "src/gbvm.c": share_timer0(gbvm_c) if "parallax" in features and {"text_shared", "text_cut"} & set(features)
             else gbvm_c, "GBVM_LICENSE.txt": runtime["license"], **engine_c}
    files.update(emit_unit_files(units))
    manifest = {
        "format": "ngpcraft-export/0.1", "target": "ngpc-rom", "complete_rom": False,
        "sources": sorted(x for x in files if x.endswith(".c")),
        "runtime_units": [describe_unit(u) for u in units], "features": features,
        "scene_engine": engine_report,
        "warnings": script_warnings(shared, [engine_report] if "warnings" in engine_report else []),
        "platform": {"profile": "retail-ngpc", "viewport": [160, 152], "initialized_ram_supported": False},
        "project": {"root": str(project.root), "start_scene": start_id},
        "scripts": {s.symbol: {"source": s.source, "bank": 0, "address": s.address,
                               "bytes": len(s.data), "sha256": hashlib.sha256(s.data).hexdigest(),
                               **({"debug_labels": [list(d) for d in s.debug]} if s.debug else {})}
                    for s in bank_scripts},
        "variables": variables,
        "gbvm": {"commit": runtime["gbvm_commit"], "heap_words": len(variables), "stack_budget": stacks,
                 "opcodes": sorted(m for m in macros if m in model["macros"]),
                 "rpn": sorted(rpn), "natives": natives, "functions": handlers,
                 "adaptations": runtime_adaptations(runtime, handlers, natives, rpn, constant_rpn_pairs(texts, model, rpn))},
        "removed": [{"scene": s, "reason": "unreachable: scene changes are not delivered"}
                    for s in scenes if s != start_id] + inits.excluded,
        "sha256": {p: hashlib.sha256(d.encode()).hexdigest() for p, d in sorted(files.items())},
    }
    files["export.json"] = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return attach_music(files, project, shared)


def script_warnings(shared, reports):
    """export.json "warnings": the scenes' (black bands...) then the scripts'
    (unset custom event arguments, REPRISE A9), each once, in order. Every
    scene's scripts are compiled, so a warning may name a scene this export
    leaves out (as its errors would)."""
    return list(dict.fromkeys([w for r in reports for w in r["warnings"]] + shared["warnings"]))


def keep_source(hits):
    """C added to the linked scene_actors.c: what GBVM keeps across load_scene
    (core.c EXCEPTION_CHANGE_SCENE, data_manager.c load_scene): the player
    actor (position and direction set by the scene switch script, speeds,
    flags, animation frame; its sheet and animations are the new scene's),
    player_iframes (actor.c, reset by actors_init only). scroll_reset resets
    game_time on every load_scene, including a change to the same scene."""
    return "\n".join([
        "/* The current scene (index in the export's scene table). */",
        "unsigned char ngc_scene;",
        "/* load_scene keeps PLAYER: saved before the scene's actors are loaded. */",
        "static unsigned short keep_x;", "static unsigned short keep_y;",
        "static unsigned char keep_dir;", "static unsigned char keep_flags;", "static unsigned char keep_tick;",
        "static unsigned char keep_speed;", "static unsigned char keep_frame;", "static unsigned char keep_fstart;",
        "static unsigned char keep_fend;", *(["static unsigned char keep_iframes;"] if hits else []),
        "void ngc_actor_set_dir(unsigned char i, unsigned char dir, unsigned char moving);",
        "/* step 0: save; 1: restore the player after the scene's",
        " * actors are loaded (before state_init snaps the player); 2: restore",
        " * player_iframes after state_init. */",
        "void ngc_scene_keep(unsigned char step)", "{",
        "    if (step == 0u) {",
        "        keep_x = act_x[0]; keep_y = act_y[0]; keep_dir = act_dir[0]; keep_flags = act_flags[0];",
        "        keep_tick = act_tick[0]; keep_speed = act_speed[0]; keep_frame = act_frame[0];",
        "        keep_fstart = act_fstart[0]; keep_fend = act_fend[0];",
        *(["        keep_iframes = collision_iframes;"] if hits else []),
        "        return;", "    }",
        "    if (step == 1u) {",
        "        act_x[0] = keep_x; act_y[0] = keep_y; act_tick[0] = keep_tick; act_speed[0] = keep_speed;",
        "        act_frame[0] = keep_frame; act_fstart[0] = keep_fstart; act_fend[0] = keep_fend;",
        "        /* activated again (ACTIVE), then player_init: clear HIDDEN, DISABLED,",
        "         * ANIM_NOLOOP, PINNED; set COLLISION */",
        "        act_flags[0] = (unsigned char)(((unsigned char)(keep_flags | 0x20u) & (unsigned char)~0x47u) | 0x08u);",
        "        ngc_actor_set_dir(0u, keep_dir, 0u);",
        "        return;", "    }",
        *(["    collision_iframes = keep_iframes;"] if hits else []),
        "}", ""])


def stack_source():
    """data_manager.c scene_stack (SCENE_STACK_SIZE 8) and vm_scene.c push /
    pop on the scene engine: a far pointer (bank 0, scene index little-endian,
    as ngc_vm_exception reads it), PLAYER.pos and PLAYER.dir. GBVM does not
    check the bounds (corrupts memory): here a push on a full stack and a pop
    on an empty one do nothing (ECARTS)."""
    return "\n".join([
        "static unsigned char stack_scene[8][3];", "static unsigned short stack_x[8];",
        "static unsigned short stack_y[8];", "static unsigned char stack_dir[8];",
        "static unsigned char stack_n;",
        "void ngc_scene_stack_reset(void)", "{", "    stack_n = 0u;", "}",
        "void ngc_scene_push(void)", "{", "    if (stack_n == 8u) return;",
        "    stack_scene[stack_n][0] = 0u; stack_scene[stack_n][1] = ngc_scene; stack_scene[stack_n][2] = 0u;",
        "    stack_x[stack_n] = act_x[0]; stack_y[stack_n] = act_y[0]; stack_dir[stack_n] = act_dir[0];",
        "    ++stack_n;", "}",
        "const unsigned char * ngc_scene_pop(unsigned char all)", "{", "    if (stack_n == 0u) return 0;",
        "    if (all) stack_n = 0u; else --stack_n;",
        "    act_x[0] = stack_x[stack_n]; act_y[0] = stack_y[stack_n]; act_dir[0] = stack_dir[stack_n];",
        "    return stack_scene[stack_n];", "}", ""])


def generate_scenes(project, inits, graph, resources, variables, model, shared, upd, inter, trig, hit, compiled):
    tick_frames = 2 if frame_rate(project) == 30 else 1
    """Every scene reachable from the start scene through EVENT_SWITCH_SCENE
    (scene_graph.py): one bank 0 with all their scripts, each scene's engine
    specialized then linked (scene_link.py), and game.c running GBVM core.c's
    scene change: kill the threads, load_scene, state init, repaint."""
    table = list(graph["reachable"])
    index = {sid: k for k, sid in enumerate(table)}
    symbols = scene_symbols(project, {resources[sid]["symbol"]: k for sid, k in index.items()})
    inits_by_id = {s.scene_id: s for s in inits.scenes}
    runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
    fade_style = engine_field(project, "fade_style")
    if type(fade_style) is not int or fade_style not in (0, 1):
        raise CompileError(f"engine field fade_style = {fade_style!r}: expected 0 (white) or 1 (black)")
    groups = []
    for sid in table:
        blocker = engine_blocker(project, resources[sid])
        if blocker:
            raise CompileError(f"{blocker} (reached by a scene change)")
        group = [compiled[sid]]
        for items, done, key in (upd, inter, trig, hit):
            group += [done[key(x)] for x in items if x.scene_id == sid]
        groups.append(group)
    # Custom scripts called by a scene come after its own scripts (once per bank).
    texts = with_called_scripts([t for g in groups for _, t in g], shared)
    select_music(shared, texts)
    frame_tilesets("\n".join(texts), "export")
    shared["warnings"] += link_slot_warnings(texts, variables)
    data, bank_scripts, macros, rpn = assemble_bank(texts, variables, model, scenes=symbols, sprites=bank_assets(shared),
                                                    constants=global_constants(shared))
    addresses = {s.symbol: s.address for s in bank_scripts}
    text = "\n".join(texts)
    natives = sorted(n for n in NATIVES if f"b_{n}" in text)
    memory = engine_memory(text, macros, model)
    rpn = rpn_cases(runtime, rpn)
    # Save / Load / If Data Saved (docs/M5_SAVE.md): the flash journal, the
    # state writer and reader, and the scene resumed without its init.
    loads = bool(re.search(r"^\s*VM_RAISE\s+EXCEPTION_LOAD\b", text, re.M))
    save = (bool(re.search(r"^\s*VM_RAISE\s+EXCEPTION_SAVE\b", text, re.M)) or loads
            or "VM_SAVE_PEEK" in macros or "VM_POLL_LOADED" in macros or "VM_SAVE_CLEAR" in macros)
    if save and any(resources[sid]["type"] == "LOGO" for sid in table):
        raise CompileError("LOGO save/load is not delivered yet: the reachable scene graph contains a LOGO scene")
    engines, reports, capacities = [], [], []
    used = {"updates": False, "interactions": False, "triggers": False, "hits": False,
            **{s: False for s in SCENE_STATES}, "hit_scripts": False, "moves": False, "cancel": False, "inputs": False,
            "timers": False, "threads": False, "routines": False}
    boot_scenes = {}
    projectile_actors, projectile_used, projectile_slots = None, False, False
    input_c = None
    all_callbacks = callback_slots(text)
    all_adv_callbacks = callback_slots(text, "adventure")
    adventure_attach, adventure_detach = callback_natives(text, "adventure")
    callback_attach = "b_plat_callback_attach" in text
    callback_detach = "b_plat_callback_detach" in text
    text = any(m in macros for m in UI_DECODERS)
    scene_uis = []
    palette_maps = []      # docs/M4_PALETTES.md, per scene index
    second = any("second_background" in project.ngpc["resources"].get(sid, {}) for sid in table)
    from ..parallax import compile_parallax
    parallax_export = any(compile_parallax(resources[sid]) for sid in table)
    for k, sid in enumerate(table):
        scene = resources[sid]
        a_upd = [u for u in upd[0] if u.scene_id == sid]
        a_int = [u for u in inter[0] if u.scene_id == sid]
        a_trig = [t for t in trig[0] if t.scene_id == sid]
        a_hit = [h for h in hit[0] if h.scene_id == sid]
        scene_texts = with_called_scripts([t for _, t in groups[k]], shared)
        _, _, scene_macros, _ = assemble_bank(scene_texts, variables, model, scenes=symbols, sprites=bank_assets(shared),
                                              constants=global_constants(shared))
        scene_text = "\n".join(scene_texts)
        validate_logo_scripts(scene, scene_macros, scene_text)
        actor_updates = {u.actor_index + 1: addresses[upd[1][u.actor["id"]][0]] for u in a_upd}
        actor_interactions = {u.actor_index + 1: addresses[inter[1][u.actor["id"]][0]] for u in a_int}
        trigger_data = [(t.trigger, addresses[trig[1][t.trigger["id"]][0]]) for t in a_trig]
        capacity = (context_capacity(inits_by_id[sid], a_upd + a_int, a_trig)
                    if a_upd or a_int or a_trig or a_hit else None)
        slots = input_slots(scene_text)
        if slots:
            capacity = ((capacity if capacity is not None else context_capacity(inits_by_id[sid], []))
                        + 2 * len(slots))
        scene_timers = timer_slots(scene_text)
        if scene_timers:
            capacity = ((capacity if capacity is not None else context_capacity(inits_by_id[sid], []))
                        + 2 * len(scene_timers))
        # Music routines need a song: a Play Music reachable from any scene.
        scene_routines = routine_slots(scene_text) if "VM_MUSIC_PLAY" in macros else []
        if scene_routines:
            capacity = ((capacity if capacity is not None else context_capacity(inits_by_id[sid], []))
                        + 2 * len(scene_routines))
        scene_threads = thread_sites(scene_text)
        if scene_threads:
            capacity = ((capacity if capacity is not None else context_capacity(inits_by_id[sid], []))
                        + 2 * scene_threads)
        scene_callbacks = callback_slots(scene_text)
        if scene_callbacks:
            if scene["type"] != "PLATFORM": raise CompileError("platform state callbacks require a PLATFORM scene")
            capacity = (capacity if capacity is not None else context_capacity(inits_by_id[sid], [])) + 2 * len(scene_callbacks)
        scene_adv_callbacks = callback_slots(scene_text, "adventure")
        if scene_adv_callbacks:
            if scene["type"] != "ADVENTURE": raise CompileError("adventure state callbacks require an ADVENTURE scene")
            capacity = (capacity if capacity is not None else context_capacity(inits_by_id[sid], [])) + 2 * len(scene_adv_callbacks)
        hit_data = None
        if a_hit:
            capacity += collision_capacity(a_hit, {res["id"]: res for _, res in project.by_type["script"]},
                                           shared["warnings"])
            hit_data = {"groups": collision_groups(project, scene),
                        "actors": {h.index: addresses[hit[1][h.entity["id"]][0]] for h in a_hit if h.index},
                        "player": next((addresses[hit[1][h.entity["id"]][0]] for h in a_hit if not h.index), None)}
        elif needs_player_collisions(project, scene):
            # shmup.c reads player_iframes: the bookkeeping runs without any script.
            hit_data = {"groups": collision_groups(project, scene), "actors": {}, "player": None}
        projectiles = projectile_scene(project, scene, shared["projectiles"][sid], scene_texts, a_hit, addresses, hit[1],
                                       shared["globalProjectiles"])
        if projectiles and projectiles[2]:
            capacity += projectiles[3]
            projectile_actors = max(projectile_actors or 0, len(projectiles[1]))
        topdown = scene_state(project, scene, bool(all_callbacks) and scene["type"] == "PLATFORM",
                              bool(all_adv_callbacks) and scene["type"] == "ADVENTURE")
        if state_of(topdown) in BOOT_STATES:
            boot_scenes.setdefault(state_of(topdown), k)
        moves = move_features(scene_text, model)
        cancel = "VM_ACTOR_MOVE_CANCEL" in scene_macros
        palette_slots = changed_slots(scene_texts, f"scene {scene['name']}")
        files, report = engine_files(build_engine_scene(project, scene, sprite_switches=sprite_assets(scene_texts, shared["sprites"]), emotes=emote_assets(scene_texts, shared["emotes"]),
                                                        replace_ops=replace_ops(scene_texts), palette_slots=palette_slots,
                                                        projectiles=projectiles[0] if projectiles else ()), moves, actor_updates, cancel, topdown,
                                     actor_interactions, trigger_data, hit_data, shadow=True, text=text, track_front=second,
                                     parallax_export=parallax_export,
                                     projectile_groups=projectiles[1] if projectiles else None,
                                     projectile_scripts=projectiles[2] if projectiles and projectiles[2] else None,
                                     projectile_modal=text and "VM_PROJECTILE_LAUNCH" in macros,
                                     stop_update="VM_ACTOR_TERMINATE_UPDATE" in scene_macros,
                                     start_update="VM_ACTOR_BEGIN_UPDATE" in scene_macros,
                                     set_bounds="VM_ACTOR_SET_BOUNDS" in scene_macros,
                                     actor_states=shared["statesOrder"] if "VM_ACTOR_SET_ANIM_SET" in scene_macros else None,
                                     sprite_states=shared["statesOrder"],
                                     read_positions="VM_ACTOR_GET_POS" in scene_macros, read_directions=("VM_ACTOR_GET_DIR" in scene_macros or "VM_ACTOR_GET_ANGLE" in scene_macros),
                                     push="VM_ACTOR_MOVE_TO" in scene_macros, split_moves=bool(set(scene_macros) & set(MOVE_INSTRUCTIONS)), camera_shake="camera_shake_frames" in natives,
                                     resume=loads, camera_control=bool(set(macros) & {"VM_CAMERA_MOVE_TO", "VM_CAMERA_SET_POS"}),
                                     camera_properties=any(n in memory for n in CAMERA_PROPERTIES),
                                     scroll_bounds=any(n in memory for n in SCROLL_BOUNDS))
        if text:
            check_tiles(report, f"scene {scene['name']}")
            tall = check_window_commands(scene_text, f"scene {scene['name']}", shared=report["planes"]["case"] == "B")
            scene_uis.append(scene_ui(project, scene, report["view"]["top_band"], shared["warnings"],
                                       palette_base=report["ui_palette_base"], shared_plane=report["planes"]["case"] == "B",
                                       avatar_glyphs=scene_avatar_glyphs(project, shared, [scene_text], scene),
                                       logical_map=tall, palette_slot=7 in palette_slots,
                                       bkg_slot=report["scr2_palettes"] if BKG_TEXT.search(scene_text) else None,
                                       frames=scene_frames(project, shared["tilesets"], scene_text, shared["warnings"])))
        if text and scene_uis[-1].palette_entries:
            report["ui_palette_entries"] = [list(e) for e in scene_uis[-1].palette_entries]
        palette_maps.append([tuple(e) for e in report.get("palette_entries", ())] +
                            [tuple(e) for e in (scene_uis[-1].palette_entries if text else ())])
        if "src/scene_input.c" in files:
            input_c = files.pop("src/scene_input.c")
        report = {"index": k, "id": sid, "init_address": addresses[compiled[sid][0]],
                  "movement": None if moves is None else list(moves), **report}
        if state_of(topdown) == "topdown":
            report["topdown"] = {"grid_px": topdown[0], "interact_mask": topdown[1], "option_long_press": topdown[2]}
        elif state_of(topdown) == "platform":
            report["platform"] = {"option_long_press": topdown[2], "animation_states": len(topdown) > 4}
        elif state_of(topdown) is not None:
            # As the one-scene export: an Adventure scene can load animation sets (genre_state).
            report[state_of(topdown)] = {"option_long_press": topdown[2], "animation_states": len(topdown) > 4}
        if projectiles and projectiles[2]:
            report["projectile_hit_contexts"] = projectiles[3]
        projectile_used = projectile_used or bool(projectiles)
        projectile_slots = projectile_slots or (bool(projectiles) and any(s.index >= SLOT_BASE for s in projectiles[0]))
        engines.append(files)
        reports.append(report)
        capacities.append(capacity)
        if slots:
            report["input_slots"] = slots
        for flag, value in (("inputs", slots), ("timers", scene_timers), ("routines", scene_routines), ("threads", scene_threads), ("updates", a_upd), ("interactions", a_int), ("triggers", a_trig), ("hits", hit_data is not None), ("hit_scripts", a_hit),
                            *((s, state_of(topdown) == s) for s in SCENE_STATES),
                            ("moves", bool(set(scene_macros) & set(MOVE_INSTRUCTIONS))), ("cancel", cancel)):
            used[flag] = used[flag] or bool(value)
    capacity = max((c for c in capacities if c is not None), default=None)
    conservative_capacity = capacity
    if project.ngpc["ngpc"].get("vm_context_policy") == "gbvm-16":
        # Explicit compatibility contract, not the automatic NGPC allocation.
        # The pinned GBVM has sixteen contexts and returns NULL on exhaustion.
        capacity = runtime["defines"]["VM_MAX_CONTEXTS"]
    stack = any(m in macros for m in SCENE_INSTRUCTIONS)
    entry_arguments = {done[key(item)][0]: 1 for items, done, key in (inter, trig, hit)
                       for item in items if item.scene_id in index}
    if used["routines"]:
        entry_arguments.update(routine_scripts("\n".join(texts)))
    stacks = stack_budget(bank_scripts, model, runtime, capacity, entry_arguments)
    context_budget = None
    if project.ngpc["ngpc"].get("vm_context_policy") != "gbvm-16":
        capacity, context_budget = fit_contexts(capacity, stacks["words_per_context"],
                                                heap_words=len(variables), saves=bool(save))
        if context_budget:
            stacks = stack_budget(bank_scripts, model, runtime, capacity, entry_arguments)
    gbvm_c, handlers = gbvm_source(runtime, macros, rpn, natives, memory, model, fade_style,
                                   update_contexts=capacity if used["updates"] else None,
                                   interaction_contexts=capacity if used["interactions"] else None,
                                   trigger_contexts=capacity if used["triggers"] else None,
                                   hit_contexts=capacity if used["hit_scripts"] else None, scenes=True,
                                   input_contexts=capacity if used["inputs"] else None,
                                   timer_contexts=capacity if used["timers"] else None,
                                   thread_contexts=capacity if used["threads"] else None,
                                   ui=ui_support(text_fonts(project, shared, texts), scene_uis, "ngc_palette_shadow + 64", front_scroll=second,
                                                        choice="VM_CHOICE" in macros,
                                                        frame_support="VM_REPLACE_TILE" in macros,
                                                        cut=overlay_cut_live("\n".join(texts)))
                                   if text else None, heap_words=len(variables), platform_callbacks=all_callbacks, callback_contexts=capacity, callback_attach=callback_attach, callback_detach=callback_detach,
                                   adventure_callbacks=all_adv_callbacks, adventure_attach=adventure_attach,
                                   adventure_detach=adventure_detach,
                                   stack_words=stacks["words_per_context"], tick_frames=tick_frames, constant_pairs=constant_rpn_pairs(texts, model, rpn), text_sound=text_sound_use(texts),
                                   projectile_actors=projectile_actors, save=(save, loads) if save else None,
                                   projectile_slots=projectile_slots,
                                   palette_maps=palette_maps)
    engine_c, link = link_scenes(engines, publish={"src/scene_actors.c": {} if set(macros) & {"VM_CAMERA_MOVE_TO", "VM_CAMERA_SET_POS"}
                                                else {"cam_settings": "ngc_camera_settings"}},
                                 extra={"src/scene_actors.c": keep_source(used["hits"])
                                        + (stack_source() if stack else "")})
    fades = "vm_fade" in handlers
    if input_c is None and (used["inputs"] or text or used["timers"]):
        input_c = input_source(option_long_press(project))
    if input_c is not None:
        engine_c["src/scene_input.c"] = input_c
    features = (["gbvm"] + (["frame_rate_30"] if tick_frames == 2 else []) + FEATURES +
                ["gbvm_scene", "scene_changes"] + (["fades"] if fades else []) + (["scene_stack"] if stack else []))
    if save:
        features += ["save_state", "save_journal"] + (["save_load"] if loads else [])
    if second:
        features.append("second_background")
    if all_callbacks:
        features.append("platform_callbacks")
    if all_adv_callbacks:
        features.append("adventure_callbacks")
    if "camera_shake_frames" in natives:
        features.append("camera_shake")
    if set(macros) & {"VM_CAMERA_MOVE_TO", "VM_CAMERA_SET_POS"}:
        features.append("camera_control")
    if "VM_RATE_LIMIT_CONST" in macros:
        features.append("rate_limit")
    if ("VM_ACTOR_GET_DIR" in macros or "VM_ACTOR_GET_ANGLE" in macros):
        features.append("actor_directions")
    if "VM_ACTOR_MOVE_TO" in macros:
        features.append("actor_push")
    if "VM_ACTOR_GET_POS" in macros:
        features.append("actor_positions")
    if "VM_REPLACE_TILE_XY" in macros:
        features.append("replace_tiles")
    if "VM_ACTOR_EMOTE" in macros:
        features.append("actor_emotes")
    if "VM_ACTOR_SET_SPRITESHEET" in macros:
        features.append("actor_spritesheets")
    if "VM_ACTOR_SET_ANIM_SET" in macros:
        features.append("actor_anim_states")
    if "VM_ACTOR_TERMINATE_UPDATE" in macros:
        features.append("actor_stop_update")
    if "VM_ACTOR_BEGIN_UPDATE" in macros:
        features.append("actor_start_update")
    if "VM_ACTOR_SET_BOUNDS" in macros:
        features.append("actor_set_bounds")
    if "VM_SET_SPRITE_VISIBLE" in macros:
        features.append("sprites_visible")
    if "pause_state_update" in memory:
        features.append("scene_update_pause")
    if any(n in memory for n in CAMERA_PROPERTIES):
        features.append("camera_properties")
    if any(n in memory for n in SCROLL_BOUNDS):
        features.append("scroll_bounds")
    if projectile_used:
        features.append("projectiles")
        if projectile_slots:
            features.append("projectile_slots")
        if text:
            features.append("projectile_modal")
    if projectile_actors is not None:
        features.append("projectile_hits")
    if any(used[s] for s in SCENE_STATES) or used["inputs"] or text or used["timers"]:
        features.append("input")
    anim_states = any(r[s]["animation_states"] for r in reports for s in ("platform", "adventure") if s in r)
    if text:
        features.append("text")
        if any(s.shared_plane for s in scene_uis):
            features.append("text_shared")
        if overlay_cut_live("\n".join(texts)):
            features.append("text_cut")
    if anim_states:
        features.append("anim_states")
    if parallax_export:
        features.append("parallax")
    for flag, feature in (("moves", "actor_moves"), ("inputs", "input_events"), ("timers", "timer_events"),
                          ("routines", "music_routines"),
                          *((s, s) for s in SCENE_STATES),
                          ("hits", "collision_update"), ("hit_scripts", "player_collisions"), ("triggers", "triggers"),
                          ("interactions", "actor_interactions"), ("cancel", "actor_move_cancel"),
                          ("updates", "actor_updates")):
        if used[flag]:
            features.append(feature)
    features += link_features(macros)
    features += translated_features(project, features)
    units = select_units(features, "rom")
    start = index[project.settings["startSceneId"]]
    game = ['#include "game.h"', '#include "gbvm.h"', *DECLARATIONS,
            *input_game_lines(scene_states(used), used["inputs"], text, used["timers"], used["routines"])[0],
            *(["void ngc_collision_update(void);"] if used["hits"] else []),
            *(UI_DECLARATIONS if text else []),
            "void ngc_vm_run(unsigned short addr);", "void ngc_vm_kill(void);",
            "unsigned short ngc_vm_exception(void);", "void ngc_palette_show(void);",
            "void ngc_scene_keep(unsigned char step);", "extern unsigned char ngc_scene;",
            "void ngc_vm_frame_end(void);",
            *(["void ngc_scene_stack_reset(void);"] if stack else []),
            *(["void ngc_vm_load(unsigned char slot);", "void ngc_actors_resume(void);"] if loads else []),
            "/* Bank 0: the scripts of every reachable scene (assembler.py: a script",
            " * address is an offset in its bank; a scene is its index below). */",
            f"static const unsigned char ngc_bank_0[] = {{{_c_bytes(data)}}};",
            "static const unsigned char * const ngc_banks[] = {ngc_bank_0};",
            "/* Per scene: its init thread, and half its view height (camera centre). */"]
    c_array_lines(game, "ngc_scene_init", [r["init_address"] for r in reports], "unsigned short")
    c_array_lines(game, "ngc_scene_half_h", [r["view"]["height"] >> 1 for r in reports], "unsigned char")
    game += ["/* GBVM data_manager.c load_scene (init_data), then what core.c does after it:",
             " * player_init and state_init, camera_update, scroll_repaint,",
             " * activate_persistent_actors, actors_update, actors_render. keep: a scene",
             " * change (the player and what GBVM keeps stay; core_run starts with none). */",
             "static void ngc_scene_load(unsigned char k, unsigned char keep)", "{",
             "    if (keep) ngc_scene_keep(0u);", "    ngc_scene = k;",
             "    ngc_sprites_load();", "    ngc_actors_load();",
             *(["    ngc_ui_scene_load(k);             /* load_scene: ui_load_tiles, UI palettes */"]
               if text else []),
             "    if (keep) ngc_scene_keep(1u);",
             "    ngc_vm_run(ngc_scene_init[k]);          /* end of load_scene */",
             *[f"    ngc_{s}_init();" for s in SCENE_STATES if used[s]],
             *(["    if (keep) ngc_scene_keep(2u);"] if used["hits"] else []),
             "    ngc_camera_update();",
             "    ngc_stream_load((short)(ngc_camera_x >> 5) - 80, (short)(ngc_camera_y >> 5) - (short)ngc_scene_half_h[k]);",
             "    ngc_actors_repaint((short)(ngc_camera_x >> 5), (short)(ngc_camera_y >> 5));",
             *(["    if (keep) ngc_vm_frame_end();  /* second decor: publish the new palette in VBlank */"]
               if second else ["    if (keep) ngc_vm_frame_end();  /* cut: publish the new palette in VBlank */"]
               if not fades else []),
             "    ngc_scroll_commit();",
             *(["    ngc_ui_commit();  /* cut: close the previous scene's window before showing colours */"]
               if text and not fades else []),
             "    ngc_sprites_commit();", "    ngc_palette_show();", "}",
             *(["/* core.c EXCEPTION_LOAD after data_load: data_manager.c load_scene(current",
                " * scene, FALSE): graphics, the restored actors set idle, no actor data and",
                " * no init thread; then player_init, state_init, camera_update, repaint. The",
                " * screen stays faded out (fade_in = FALSE): the script that saved fades in",
                " * on its On Load path. */",
                "static void ngc_scene_resume(void)", "{", "    unsigned char k = ngc_scene;",
                "    ngc_sprites_load();", "    ngc_actors_resume();",
                *(["    ngc_ui_scene_load(k);"] if text else []),
                *[f"    ngc_{s}_init();" for s in SCENE_STATES if used[s]],
                "    ngc_camera_update();",
                "    ngc_stream_load((short)(ngc_camera_x >> 5) - 80, (short)(ngc_camera_y >> 5) - (short)ngc_scene_half_h[k]);",
                "    ngc_actors_repaint((short)(ngc_camera_x >> 5), (short)(ngc_camera_y >> 5));",
                "    ngc_scroll_commit();", "    ngc_sprites_commit();", "    ngc_palette_show();", "}"]
               if loads else []),
             "void ngc_game_init(void)", "{",
             "    ngc_vm_start(ngc_banks, 0u, 0u);",
             *(["    ngc_scene_stack_reset();          /* load_init: random RAM on a console */"]
               if stack else []),
             *(["    ngc_input_init();"] if any(used[s] for s in SCENE_STATES) or used["inputs"] or text
               or used["timers"] else []),
             # script_engine_init once at boot: ngc_platform_boot dispatches on
             # ngc_scene, so a platformer scene is selected for the call (the
             # start scene, loaded next, sets ngc_scene again).
             *[line for s, k in boot_scenes.items()
               for line in (f"    ngc_scene = {k}u;", f"    ngc_{s}_boot();             /* engine fields, BSS */")],
             *([f"    ngc_ui_start({start}u);              /* core_reset: ui_init */"] if text else []),
             f"    ngc_scene_load({start}u, 0u);  /* {resources[table[start]]['symbol']} */", "}",
             *(modal_frame(COMMIT, after_scripts(tick_frames), "VM_PROJECTILE_LAUNCH" in macros) if text else []),
             "/* One frame of GBVM's core.c process_VM. RUNNER_EXCEPTION: a scene change",
             " * kills the threads and loads the scene, then the scripts run again in the",
             " * same frame (core.c loops); any other exception is ignored, as there. */",
             "void ngc_game_tick(void)", "{", "    unsigned char status;", "    unsigned short next;",
             *COMMIT, *(["    ngc_ui_commit();"] if text else []),
             "    /* Native audio service: after video, before picture logic. */",
             *picture_frames([
                 "    for (;;) {",
                 "        do {", "            status = script_runner_update();",
                 f"        }} while (status == {runtime['defines']['RUNNER_BUSY']}u);",
                 f"        if (status != {runtime['defines']['RUNNER_EXCEPTION']}u) break;",
                 "        next = ngc_vm_exception();", "        if (next == 0xFFFFu) continue;",
                 *(["        if (next >= 0xFFF0u) {             /* EXCEPTION_LOAD */",
                    "            ngc_vm_load((unsigned char)(next & 0x0Fu));", "            ngc_scene_resume();",
                    "            continue;", "        }"] if loads else []),
                 "        ngc_vm_kill();", "        ngc_scene_load((unsigned char)next, 1u);", "    }",
                 *input_game_lines(scene_states(used), used["inputs"], text, used["timers"], used["routines"])[2],
                 *scene_frame_lines(text),
                 *(["    ngc_collision_update();"] if used["hits"] else []),
                 *AFTER_SCRIPTS[-1:]], tick_frames), "    ngc_vm_frame_end();", "}"]
    if "VM_SET_SPRITE_VISIBLE" in macros:
        game = sprites_visible_game(game)
    if "pause_state_update" in memory:
        game = pause_state_game(game)
    if any(n in memory for n in CAMERA_PROPERTIES):
        game = camera_properties_game(game)
    files = {"src/game.h": GAME_H, "src/game.c": "\n".join(game) + "\n", "src/gbvm.h": GBVM_H,
             "src/gbvm.c": share_timer0(gbvm_c) if "parallax" in features and {"text_shared", "text_cut"} & set(features)
             else gbvm_c, "GBVM_LICENSE.txt": runtime["license"], **engine_c}
    files.update(emit_unit_files(units))
    sources = sorted(x for x in files if x.endswith(".c"))
    manifest = {
        "format": "ngpcraft-export/0.1", "target": "ngpc-rom", "complete_rom": False,
        "sources": sources,
        "runtime_units": [describe_unit(u) for u in units], "features": features,
        "scene_engine": {"built": True, "scenes": reports, "start": start, "link": link,
                         "contexts": capacity,
                         "context_policy": project.ngpc["ngpc"].get("vm_context_policy", "automatic-ngpc"),
                         "conservative_context_estimate": conservative_capacity,
                         **({"context_budget": context_budget} if context_budget else {}),
                         "warnings": [w for r in reports for w in r["warnings"]]},
        "warnings": script_warnings(shared, reports),
        "platform": {"profile": "retail-ngpc", "viewport": [160, 152], "initialized_ram_supported": False},
        "project": {"root": str(project.root), "start_scene": table[start]},
        "scripts": {s.symbol: {"source": s.source, "bank": 0, "address": s.address,
                               "bytes": len(s.data), "sha256": hashlib.sha256(s.data).hexdigest(),
                               **({"debug_labels": [list(d) for d in s.debug]} if s.debug else {})}
                    for s in bank_scripts},
        "variables": variables,
        "gbvm": {"commit": runtime["gbvm_commit"], "heap_words": len(variables), "stack_budget": stacks,
                 "opcodes": sorted(m for m in macros if m in model["macros"]),
                 "rpn": sorted(rpn), "natives": natives, "functions": handlers,
                 "adaptations": runtime_adaptations(runtime, handlers, natives, rpn, constant_rpn_pairs(texts, model, rpn))},
        "removed": [{"scene": s.scene_id, "reason": "unreachable from the start scene"}
                    for s in inits.scenes if s.scene_id not in index] + inits.excluded,
        "sha256": {p: hashlib.sha256(d.encode()).hexdigest() for p, d in sorted(files.items())},
    }
    files["export.json"] = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return attach_music(files, project, shared)


def c_array_lines(lines, name, values, ctype):
    lines.append(f"static const {ctype} {name}[{len(values)}] = {{{', '.join(f'{v}u' for v in values)}}};")
