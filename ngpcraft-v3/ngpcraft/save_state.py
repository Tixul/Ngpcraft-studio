"""What a GB Studio save keeps, variable by variable (docs/M5_SAVE.md step 3).

GBVM load_save.c saves the save_points list (script_memory, VM contexts,
input and timer events, music, scene and scene stack, actors and their
lists, player_moving, player_collision_actor, the random seed). Loading
restores them, then data_manager.c load_scene(scene, FALSE) reloads the
scene's graphics without the actors' data, and core.c runs player_init,
state_init, camera_update, scroll_repaint, activate_persistent_actors,
actors_update, actors_render: everything else is rebuilt or transient.

Every variable with static storage of the generated C is classified here
(tools/state_inventory.py lists them; tests/test_save_state.py fails on an
unclassified one). The audit refuses an unclassified variable. Save events
select this classification for their export gate; the actual codecs and
flash operations live in save_emit.py and the selected runtime units.
"""
from .compiler import CompileError

SAVE, SKIP = "save", "skip"

# name -> (decision, GBVM save point or the reason it is not saved)
_VM = {
    "script_memory": (SAVE, "script_memory (variables first, then the live part of each context stack)"),
    "CTXS": (SAVE, "CTXS"), "first_ctx": (SAVE, "first_ctx"), "free_ctxs": (SAVE, "free_ctxs"),
    "old_executing_ctx": (SAVE, "old_executing_ctx"), "executing_ctx": (SAVE, "executing_ctx"),
    "vm_lock_state": (SAVE, "vm_lock_state"),
    "input_events": (SAVE, "input_events"), "input_slots": (SAVE, "input_slots"),
    "timer_events": (SAVE, "timer_events"), "timer_values": (SAVE, "timer_values"),
    "music_events": (SAVE, "music_events"),
    "ngc_rand_seed": (SAVE, "__rand_seed"),
    "vm_loaded_state": (SKIP, "set by EXCEPTION_LOAD itself (data_load result)"),
    "vm_exception_code": (SKIP, "the exception being handled"),
    "vm_exception_params_bank": (SKIP, "the exception being handled"),
    "vm_exception_params_length": (SKIP, "the exception being handled"),
    "vm_exception_params_offset": (SKIP, "the exception being handled"),
    "ngc_banks": (SKIP, "ROM table pointer set at start-up"),
    "ngc_plat_events": (SKIP, "platformer callbacks: state data, not a GBVM save point"),
    "ngc_projectile_handles": (SAVE, "actors.hscript_hit (per actor: its hit thread, saved with CTXS)"),
    "ngc_tick_frame": (SKIP, "frame pacing"), "ngc_vbl_passed": (SKIP, "frame pacing"),
    "ngc_shake_seed": (SKIP, "camera shake: camera state, not saved"),
    "ngc_scroll_offset_x": (SKIP, "scroll_offset: reset by load_scene"),
    "ngc_scroll_offset_y": (SKIP, "scroll_offset: reset by load_scene"),
}
_FADE = {n: (SKIP, "fade manager: EXCEPTION_LOAD runs its own fades") for n in (
    "fade_direction", "fade_frame", "fade_frames_per_step", "fade_running", "fade_style", "fade_timer",
    "ngc_palette_shadow", "ngc_palette_staged", "ngc_staged_step", "ngc_staged_style")}
_UI = {n: (SKIP, "ui.c state is not a GBVM save point (text window and printer)") for n in (
    "current_text_speed", "overlay_priority", "text_bkg_fill", "text_draw_speed", "text_drawn", "text_ff",
    "text_ff_joypad", "text_in_speed", "text_options", "text_out_speed", "text_palette", "text_render_base_addr",
    "text_scroll_addr", "text_scroll_fill", "text_scroll_height", "text_scroll_width", "text_sound_bank",
    "ui_current_tile", "ui_current_tile_bank", "ui_dest_base", "ui_dest_ptr", "ui_prev_tile", "ui_prev_tile_bank",
    "ui_text_ptr", "vwf_current_font_bank", "vwf_current_font_desc", "vwf_current_font_idx", "vwf_current_offset",
    "vwf_direction", "vwf_inverse_map", "vwf_tile_data", "win_dest_pos_x", "win_dest_pos_y", "win_pos_x",
    "win_pos_y", "win_speed", "text_sound_data", "ngc_ui_text", "ngc_ui_avatar_attr", "ngc_ui_avatar_glyphs", "ngc_ui_cell_avatar",
    "ngc_ui_copy_source", "ngc_ui_copy_target", "ngc_ui_frame_source", "ngc_ui_frame_target", "ngc_ui_hw_rows", "ngc_ui_hw_cols", "ngc_ui_copy_count", "ngc_sprites_hidden", "ngc_current_scene_mem",
    "ngc_ui_map", "ngc_ui_offset", "ngc_ui_pending", "ngc_ui_pixels", "ngc_ui_scene", "ngc_ui_shared",
    "ngc_ui_tall", "ngc_ui_text_attr", "split_active", "split_line", "split_x", "split_y")}
_ACTORS = {
    # actor_t fields (gbs_types.h), saved with `actors`
    "act_x": (SAVE, "actors.pos.x"), "act_y": (SAVE, "actors.pos.y"), "act_dir": (SAVE, "actors.dir"),
    "act_flags": (SAVE, "actors.flags"), "act_bounds": (SAVE, "actors.bounds"),
    "act_frame": (SAVE, "actors.frame"), "act_fstart": (SAVE, "actors.frame_start"),
    "act_fend": (SAVE, "actors.frame_end"), "act_tick": (SAVE, "actors.anim_tick"),
    "act_speed": (SAVE, "actors.move_speed"), "act_anim": (SAVE, "actors.animation"),
    "act_sheet": (SAVE, "actors.sprite"), "act_state_anims": (SAVE, "actors.animations"),
    "act_player_anims": (SAVE, "actors.animations (player)"), "ngc_player_anim_set": (SAVE, "actors.animations (player)"),
    "act_update_handle": (SAVE, "actors.hscript_update"),
    "act_next": (SAVE, "actors.next"), "act_prev": (SAVE, "actors.prev"),
    "act_head": (SAVE, "actors_active_head"), "inact_head": (SAVE, "actors_inactive_head"),
    "act_tail": (SAVE, "GBVM does not save actors_active_tail (stale after a load); kept consistent with the lists"),
    "player_moving": (SAVE, "player_moving"),
    "ngc_scene": (SAVE, "current_scene"),
    "stack_scene": (SAVE, "scene_stack"), "stack_x": (SAVE, "scene_stack"), "stack_y": (SAVE, "scene_stack"),
    "stack_dir": (SAVE, "scene_stack"), "stack_n": (SAVE, "scene_stack_ptr"),
    # rebuilt or transient
    "act_order_base": (SAVE, "actors.sprite: mutable frame-table base after a spritesheet switch"),
    "act_emote_frame": (SKIP, "emote: emote_actor = NULL in load_scene"),
    "ngc_emote_actor": (SKIP, "emote_actor = NULL in load_scene"), "ngc_emote_timer": (SKIP, "emote_actor = NULL in load_scene"),
    "cam_clamp_x": (SKIP, "camera: not saved, camera_update"), "cam_clamp_y": (SKIP, "camera: not saved, camera_update"),
    "ngc_camera_clamp_x": (SKIP, "camera: not saved, camera_update"),
    "ngc_camera_clamp_y": (SKIP, "camera: not saved, camera_update"),
    "cam_deadzone_x": (SKIP, "camera: not saved"), "cam_deadzone_y": (SKIP, "camera: not saved"),
    "cam_offset_x": (SKIP, "camera: not saved"), "cam_offset_y": (SKIP, "camera: not saved"),
    "cam_settings": (SKIP, "camera: not saved"), "ngc_camera_settings": (SKIP, "camera: not saved"),
    # Camera Property Set publishes camera.c's names (scene_engine.py); not saved either.
    "camera_deadzone_x": (SKIP, "camera: not saved"), "camera_deadzone_y": (SKIP, "camera: not saved"),
    "camera_offset_x": (SKIP, "camera: not saved"), "camera_offset_y": (SKIP, "camera: not saved"),
    "ngc_pause_state_update": (SKIP, "load_scene resets pause_state_update"),
    "ngc_camera_x": (SKIP, "camera: not saved, camera_update"), "ngc_camera_y": (SKIP, "camera: not saved, camera_update"),
    "collision_iframes": (SKIP, "player_iframes: not a save point"),
    "collision_pending": (SAVE, "player_collision_actor (a collision pending at the save fires after the load)"), "did_interact_actor": (SKIP, "interaction of the frame"),
    "draw_x": (SKIP, "render of the frame"), "draw_y": (SKIP, "render of the frame"),
    "gb_scroll_x": (SKIP, "scroll: scroll_reset in load_scene"), "gb_scroll_y": (SKIP, "scroll: scroll_reset in load_scene"),
    "ngc_game_time": (SKIP, "game_time: not a save point"),
    "player_max_pos": (SKIP, "state data: state_init"),
    "tile_hit_x": (SKIP, "collision of the frame"), "tile_hit_y": (SKIP, "collision of the frame"),
    "trigger_last": (SKIP, "trigger_reset in load_scene"),
}
_KEEP = {f"keep_{n}": (SKIP, "scene change scratch (ngc_scene_keep)") for n in (
    "dir", "fend", "flags", "frame", "fstart", "iframes", "speed", "tick", "x", "y")}
_PROJECTILES = {f"proj_{n}": (SKIP, "projectiles_init in load_scene") for n in (
    "bounds", "dx", "dy", "end", "flags", "frame", "free", "group", "head", "life", "mask", "next", "start",
    "tick", "x", "y", "slot")}
_DISPLAY = {n: (SKIP, "display, streaming or input of the frame: rebuilt after load") for n in (
    "ngc_frame_color_target", "ngc_frame_colors", "ngc_frame_count", "ngc_frame_target", "ngc_frame_words",
    "ngc_mask_cache", "ngc_mask_frame", "ngc_mask_key", "ngc_mask_pending", "ngc_mask_row_plane",
    "ngc_mask_row_source", "ngc_mask_row_target", "ngc_mask_shadow", "ngc_mask_valid", "ngc_mask_x",
    "ngc_mask_y", "ngc_oam_prev", "ngc_oam_used", "ngc_scol", "ngc_slot_frame", "ngc_soam", "ngc_soam_count",
    "ngc_front_scroll", "ngc_front_scroll_next", "ngc_front_stream_tx", "ngc_front_stream_ty", "ngc_scroll",
    "ngc_scroll_base_next", "ngc_scroll_next", "ngc_stream_tx", "ngc_stream_ty", "ngc_frame", "ngc_sys_time",
    "ngc_joy", "ngc_joy_pressed", "ngc_last_joy", "ngc_option_ticks", "ngc_recent_joy")}
# Function statics: the state of one call (script_runner_update's quantum,
# ui_draw_text_buffer_char's saved settings, scratch pointers).
_FUNCTION = {
    ("script_runner_update", "_save"), ("script_runner_update", "counter"), ("script_runner_update", "waitable"),
    ("script_detach_hthread", "ctx"), ("script_terminate", "ctx"), ("vm_rpn", "op"),
    ("ui_draw_text_buffer_char", "current_font_idx"), ("ui_draw_text_buffer_char", "current_text_bkg_fill"),
    ("ui_draw_text_buffer_char", "current_text_draw_speed"), ("ui_draw_text_buffer_char", "current_text_ff_joypad"),
    ("ui_draw_text_buffer_char", "current_vwf_direction"),
}

CLASSIFICATION = {**_VM, **_FADE, **_UI, **_ACTORS, **_KEEP, **_PROJECTILES, **_DISPLAY}

# Reviewed mutable declarations of the frozen Over Rev driver (all 72
# variants). GBVM saves only music identity, not sequencer/PSG/SFX state.
_AUDIO_TRANSIENT = """ngc_sound_staging s_attn_base s_bgm_ch_used_by_sfx
s_bgm_dbg s_bgm_fade_attn s_bgm_fade_counter s_bgm_fade_speed
s_bgm_gate_percent s_bgm_last_vbl s_bgm_loop s_bgm_master_attn
s_bgm_note_table s_bgm_restore_ch s_bgm_song_frame s_bgm_speed
s_bgm_v0 s_bgm_v1 s_bgm_v2 s_bgm_vn s_buf_count s_freq_base
s_psg_commit_mask s_psg_pending s_psg_pending_mask s_psg_shadow
s_sfxTimer s_sfx_active_mask s_sfx_cmd s_sfx_end_pending
s_sfx_noise_attn_base s_sfx_noise_attn_cur s_sfx_noise_burst
s_sfx_noise_burst_counter s_sfx_noise_burst_dur s_sfx_noise_burst_off
s_sfx_noise_env_counter s_sfx_noise_env_on s_sfx_noise_env_spd
s_sfx_noise_env_step s_sfx_noise_val s_sfx_tone_attn_base
s_sfx_tone_attn_cur s_sfx_tone_div_base s_sfx_tone_div_cur
s_sfx_tone_env_counter s_sfx_tone_env_on s_sfx_tone_env_spd
s_sfx_tone_env_step s_sfx_tone_sw_counter s_sfx_tone_sw_dir
s_sfx_tone_sw_end s_sfx_tone_sw_on s_sfx_tone_sw_ping
s_sfx_tone_sw_speed s_sfx_tone_sw_step s_sound_drops s_sound_fault
s_sound_last_sfx ngc_music_pending ngc_audio_last_frame
ngc_audio_loading_flag g_vb_counter ngc_sfx_id ngc_sfx_row ngc_sfx_remaining ngc_sfx_priority
ngc_music_mute_mask ngc_routine_queue ngc_routine_head ngc_routine_tail""".split()
CLASSIFICATION.update({name: (SKIP, 'audio driver/transport/clock: not a GBVM save point; track restarts')
                       for name in _AUDIO_TRANSIENT})
CLASSIFICATION['ngc_music_current'] = (SAVE, 'music_current_track_bank + music_current_track: native ID, 65535 means stopped')
# Flash/journal transaction scratch is owned by the operation reading or
# writing this snapshot, never by the saved gameplay state.
_SAVE_TRANSIENT = """ngc_flash_dst ngc_flash_src sj_bad sj_best sj_block_best
sj_block_w sj_buffer sj_check sj_copy sj_end sj_free sj_head sj_length
sj_mode sj_page_w sj_pos sj_produce sj_read sj_sum sj_top""".split()
CLASSIFICATION.update({name: (SKIP, 'flash/journal transaction scratch: not game state')
                       for name in _SAVE_TRANSIENT})

# Reviewed platform.c variables at gbvm bd6f41c. Do not accept a new variable
# merely because its name starts with plat_: it needs its own review.
_PLATFORM_NAMES = """plat_air_control plat_air_dec plat_anim_dirty plat_attached_actor
plat_attached_prev_x plat_attached_prev_y plat_blank_grav plat_camera_block
plat_camera_deadzone_x plat_climb_vel plat_coyote_frames plat_coyote_timer
plat_dash_active plat_dash_cooldown_timer plat_dash_currentframe plat_dash_deadzone
plat_dash_dist plat_dash_frames plat_dash_from plat_dash_jump_from plat_dash_mask
plat_dash_per_frame plat_dash_pressed plat_dash_ready_frames plat_dec plat_delta_x
plat_delta_y plat_drop_through_active plat_drop_timer plat_extra_jumps
plat_extra_jumps_counter plat_float_active plat_float_vel plat_grav plat_grounded
plat_hold_grav plat_hold_jump_timer plat_is_actor_attached plat_jump_buffer_frames
plat_jump_buffer_timer plat_jump_hold_frames plat_jump_hold_vel plat_jump_reduction
plat_jump_reduction_vel plat_jump_type plat_jump_vel plat_jump_vel_per_frame
plat_knockback_frames plat_knockback_timer plat_knockback_vel_x plat_knockback_vel_y
plat_ladder_block_h plat_ladder_block_v plat_last_wall_col plat_max_fall_vel
plat_min_vel plat_next_state plat_nocontrol_h_timer plat_on_slope plat_run_acc
plat_run_boost plat_run_stage plat_run_vel plat_slope_y plat_state plat_tap_timer
plat_temp_y plat_turn_acc plat_turn_control plat_vel_x plat_vel_y plat_walk_acc
plat_walk_vel plat_wall_col plat_wall_coyote_timer plat_wall_jump_counter
plat_wall_jump_max plat_wall_kick plat_wall_slide plat_wall_slide_vel""".split()
CLASSIFICATION.update({name: (SKIP, "platformer state or engine field: not a GBVM save point")
                       for name in _PLATFORM_NAMES})

# GBVM states/shmup.c and pointnclick.c: rebuilt by state_init, never written
# by load_save.c. Engine fields follow the same contract as platform fields.
CLASSIFICATION.update({name: (SKIP, "genre state or engine field: not a GBVM save point")
                       for name in ("shooter_dest", "shooter_direction", "shooter_reached_end",
                                    "shooter_scroll_speed", "last_hit_trigger")})

# Reviewed adventure.c variables at gbvm bd6f41c (file-scope, statics included):
# rebuilt by adventure_init or engine fields, never written by load_save.c.
_ADVENTURE_NAMES = """adv_anim_dirty adv_attached_actor adv_attached_prev_x adv_attached_prev_y
adv_camera_deadzone_x adv_camera_deadzone_y adv_camera_transitioning adv_dash_active
adv_dash_cooldown_timer adv_dash_currentframe adv_dash_deadzone adv_dash_dir adv_dash_dist
adv_dash_frames adv_dash_mask adv_dash_per_frame adv_dash_ready_frames adv_dec adv_events
adv_is_actor_attached adv_knockback_frames adv_knockback_timer adv_knockback_vel_x
adv_knockback_vel_y adv_next_state adv_push_delay_frames adv_push_timer adv_run_acc adv_run_vel
adv_state adv_tap_timer adv_temp_y adv_turn_acc adv_vel_x adv_vel_y adv_walk_acc adv_walk_vel
facing_dir delta movement_delta collision_dir temp_x temp_y facing_priority_axis""".split()
CLASSIFICATION.update({name: (SKIP, "adventure state or engine field: not a GBVM save point")
                       for name in _ADVENTURE_NAMES})

# Parallax (docs/M6_PARALLAX.md): slice scrolls recomputed every frame from the
# scroll position (background_stream.py), streamed band origins reset to 0x7FFF
# by ngc_stream_load, the Timer0 slice index restarted every vertical blank
# (runtime/parallax.c). scroll.c's parallax state is not a GBVM save point either.
CLASSIFICATION.update({name: (SKIP, "parallax: recomputed from the scroll, reset by the scene load or the VBlank")
                       for name in ("ngc_parallax_line", "ngc_parallax_n", "ngc_parallax_x", "ngc_parallax_y",
                                    "parallax_i", "ngc_band_tx", "ngc_band_ty", "ngc_band_x")})

# Every actor_t field of the pinned GBVM, including fields represented by ROM
# constants. Tests compare these keys with gbs_types.h, so a new field fails.
ACTOR_FIELDS = {
    "flags": ("act_flags",), "pos": ("act_x", "act_y"), "dir": ("act_dir",),
    "bounds": ("act_bounds",), "frame": ("act_frame",), "frame_start": ("act_fstart",),
    "frame_end": ("act_fend",), "anim_tick": ("act_tick",), "move_speed": ("act_speed",),
    "animation": ("act_anim",), "animations": ("act_state_anims", "act_player_anims", "ngc_player_anim_set"),
    "sprite": ("act_sheet", "act_order_base"), "hscript_update": ("act_update_handle",),
    "hscript_hit": ("ngc_projectile_handles",), "next": ("act_next",), "prev": ("act_prev",),
    "base_tile": "graphics allocation rebuilt by ngc_sprites_load",
    "reserve_tiles": "graphics capacity fixed by the export",
    "script": "ROM act_interact_addr, fixed for the scene",
    "script_update": "ROM act_update_addr, fixed for the scene",
    "collision_group": "ROM act_collision_group, fixed for the scene",
}


def classify(function, name):
    """(decision, reason) of one variable; None when unclassified."""
    if function:
        return (SKIP, "function static: state of one call") if (function, name) in _FUNCTION else None
    if name in CLASSIFICATION:
        return CLASSIFICATION[name]
    return None


def unclassified(variables):
    """Inventory entries (tools/state_inventory.py) with no decision."""
    return [v for v in variables if classify(v["function"], v["name"]) is None]


def check(variables, where):
    missing = unclassified(variables)
    if missing:
        names = ", ".join(sorted({f"{v['source']}:{v['name']}" for v in missing}))
        raise CompileError(f"{where}: save state: unclassified variable(s) {names} (ngpcraft/save_state.py)")


def describe(variables, where="export"):
    """Fail-closed inventory with a decision and reason for every RAM variable.

    sizeof_expression is evaluated in the owning C unit by the target compiler;
    a host estimate must never replace it for the flash capacity gate.
    """
    check(variables, where)
    out = []
    for variable in variables:
        decision, reason = classify(variable["function"], variable["name"])
        out.append({**variable, "decision": decision, "reason": reason,
                    "sizeof_expression": f"sizeof({variable['name']})" if decision == SAVE else None})
    return out


def check_capacity(payload_bytes, where="export"):
    """Journal capacity for a target-measured maximum payload, including header.

    22 header bytes and the final commit byte leave 233 payload bytes in a
    one-page record. Reserve one extra page for recovery of a torn transfer.
    """
    if type(payload_bytes) is not int or not 0 <= payload_bytes <= 65535:
        raise CompileError(f"{where}: save state: invalid payload length {payload_bytes!r}")
    pages = (payload_bytes + 23 + 255) // 256
    if 3 * pages + 1 > 32:
        raise CompileError(f"{where}: save state: {payload_bytes} bytes need {pages} pages per slot; "
                           f"3 slots plus recovery need {3 * pages + 1} pages, only 32 available "
                           "(maximum payload 2537 bytes)")
    return pages
