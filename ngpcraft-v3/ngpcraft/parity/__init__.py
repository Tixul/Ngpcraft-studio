"""GB Studio parity: which reference events the V3 compiler actually delivers.

An event may be marked `identique` or `adapte` in gbstudio.json only when it is
listed here AND has its contract, generator, laboratory and probe (cahier §3.4).
"""
IMPLEMENTED_EVENTS = frozenset({"EVENT_ACTOR_GET_DIRECTION", "EVENT_SOUND_PLAY_EFFECT", "EVENT_MUSIC_PLAY", "EVENT_MUSIC_STOP", "EVENT_DEC_VALUE", "EVENT_FADE_IN", "EVENT_FADE_OUT", "EVENT_GROUP", "EVENT_IF", "EVENT_SWITCH", "EVENT_VARIABLE_MATH", "EVENT_RNG_SEED", "EVENT_IF_INPUT", "EVENT_AWAIT_INPUT", "EVENT_IF_COLOR_SUPPORTED", "EVENT_RATE_LIMIT", "EVENT_ACTOR_SET_ANIMATE",
                                "EVENT_PLAYER_SET_SPRITE", "EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR", "EVENT_LOOP_WHILE", "EVENT_LOOP_FOR",
                                "EVENT_SCRIPT_LOCK", "EVENT_SCRIPT_UNLOCK", "EVENT_IDLE", "EVENT_RESET_VARIABLES",
                                # Second corpus batch (docs/M4_BATCH2_EVENTS.md).
                                "EVENT_ACTOR_START_UPDATE", "EVENT_OVERLAY_HIDE", "EVENT_SHOW_SPRITES", "EVENT_HIDE_SPRITES",
                                "EVENT_IF_CURRENT_SCENE_IS", "EVENT_CAMERA_SET_POSITION", "EVENT_GOTO_LABEL", "EVENT_DEFINE_LABEL",
                                "EVENT_ACTOR_SET_POSITION_TO_VALUE", "EVENT_ACTOR_MOVE_TO_VALUE", "EVENT_ACTOR_SET_FRAME_TO_VALUE",
                                # Threads (docs/M4_THREADS.md).
                                "EVENT_THREAD_START", "EVENT_THREAD_STOP",
                                # Save slot clear and peek (docs/M5_SAVE.md).
                                "EVENT_CLEAR_DATA", "EVENT_PEEK_DATA",
                                # Static tile replacement (docs/M4_REPLACE_TILES.md).
                                "EVENT_REPLACE_TILE_XY",
                                # Device conditions, always false (ECARTS §12).
                                "EVENT_IF_DEVICE_SGB", "EVENT_IF_DEVICE_GBA",
                                # Raw GBVM, delivered instructions only (ECARTS §12).
                                "EVENT_GBVM_SCRIPT",
                                # Invoke Actor Script, on the named actor (ECARTS §25).
                                "EVENT_ACTOR_INVOKE",
                                # Engine field read into a variable (scene_platform engine memory).
                                "EVENT_ENGINE_FIELD_STORE",
                                # Actor collision box (VM_ACTOR_SET_BOUNDS, mutable act_bounds).
                                "EVENT_ACTOR_SET_COLLISION_BOX",
                                # Projectile slots (ECARTS §26).
                                "EVENT_LOAD_PROJECTILE_SLOT", "EVENT_LAUNCH_PROJECTILE_SLOT",
                                # Scene type update pause (core.c pause_state_update).
                                "EVENT_SCENE_UPDATE_PAUSE", "EVENT_SCENE_UPDATE_RESUME",
                                # Camera lock without speed, camera.c properties.
                                "EVENT_CAMERA_SET_LOCK", "EVENT_CAMERA_PROPERTY_SET",
                                # Close the non-modal box (eventTextCloseNonModal.js).
                                "EVENT_DIALOGUE_CLOSE_NONMODAL", "EVENT_SET_FONT",
                                # Actor split / flicker effects (VM_LOOP).
                                "EVENT_ACTOR_EFFECTS",
                                # Variable Data Table, rows after the script (docs/M4_BATCH6_EVENTS.md).
                                "EVENT_DATA_TABLE",
                                # Text drawn on the background or overlay map (eventTextDraw.js).
                                "EVENT_TEXT_DRAW",
                                # Scroll bounds at runtime, NGPC view height (ECARTS §1).
                                "EVENT_CAMERA_SET_BOUNDS",
                                # Dialogue frame variants per scene (ECARTS §4 colours).
                                "EVENT_SET_DIALOGUE_FRAME",
                                # Native effect replayed per character (docs/M5_TEXT_SOUND.md).
                                "EVENT_TEXT_SET_SOUND_EFFECT",
                                # Muted voices on the Over Rev driver (docs/M5_MUTE_CHANNEL.md).
                                "EVENT_MUTE_CHANNEL",
                                # Routine calls carried by the native stream (docs/M5_MUSIC_ROUTINE.md).
                                "EVENT_SET_MUSIC_ROUTINE",
                                # GBVM vm_sio.c on the NGPC cable, BIOS COM (docs/M6_LINK.md).
                                "EVENT_LINK_HOST", "EVENT_LINK_JOIN", "EVENT_LINK_CLOSE", "EVENT_LINK_TRANSFER",
                                # overlay_cut_scanline on SCR1, Timer0 split (docs/M6_SCANLINE_CUTOFF.md).
                                "EVENT_OVERLAY_SET_SCANLINE_CUTOFF",
                                # Adventure scene type state (adventure.c adv_next_state).
                                "EVENT_ADVENTURE_STATE_SET",
                                "EVENT_SET_ADVENTURE_CALLBACK_SCRIPT", "EVENT_REMOVE_ADVENTURE_CALLBACK_SCRIPT",
                                "EVENT_SET_FLAGS", "EVENT_ADD_FLAGS", "EVENT_CLEAR_FLAGS", "EVENT_IF_FLAGS_COMPARE",
                                "EVENT_SET_TIMER_SCRIPT", "EVENT_TIMER_RESTART", "EVENT_TIMER_DISABLE",
                                "EVENT_INC_VALUE", "EVENT_LOOP", "EVENT_SET_VALUE", "EVENT_WAIT", "EVENT_VARIABLE_MATH_EVALUATE",
                                "EVENT_ACTOR_GET_POSITION", "EVENT_ACTOR_SET_POSITION_RELATIVE", "EVENT_IF_ACTOR_DIRECTION",
                                "EVENT_ACTOR_DEACTIVATE",
                                "EVENT_ACTOR_ACTIVATE",
                                "EVENT_ACTOR_SET_POSITION",
                                "EVENT_ACTOR_SET_DIRECTION",
                                "EVENT_ACTOR_SET_FRAME",
                                "EVENT_ACTOR_SET_MOVEMENT_SPEED",
                                "EVENT_ACTOR_SET_ANIMATION_SPEED",
                                "EVENT_ACTOR_HIDE",
                                "EVENT_ACTOR_SHOW",
                                "EVENT_ACTOR_COLLISIONS_DISABLE",
                                "EVENT_ACTOR_COLLISIONS_ENABLE",
                                "EVENT_CAMERA_SHAKE", "EVENT_CAMERA_MOVE_TO", "EVENT_CAMERA_LOCK",
                                "EVENT_IF_ACTOR_RELATIVE_TO_ACTOR",
                                "EVENT_IF_ACTOR_AT_POSITION",
                                "EVENT_LAUNCH_PROJECTILE", "EVENT_ACTOR_EMOTE", "EVENT_ACTOR_SET_SPRITE", "EVENT_ACTOR_SET_STATE",
                                "EVENT_ACTOR_STOP_UPDATE",
                                "EVENT_ACTOR_PUSH",
                                "EVENT_ACTOR_MOVE_CANCEL",
                                "EVENT_ACTOR_MOVE_TO",
                                "EVENT_ACTOR_MOVE_RELATIVE",
                                "EVENT_SWITCH_SCENE", "EVENT_TEXT", "EVENT_CHOICE", "EVENT_MENU", "EVENT_REPLACE_TILE_XY_SEQUENCE",
                                "EVENT_PALETTE_SET_BACKGROUND", "EVENT_PALETTE_SET_UI", "EVENT_PALETTE_SET_SPRITE", "EVENT_PALETTE_SET_EMOTE", "EVENT_IF_VALUE", "EVENT_IF_VALUE_COMPARE", "EVENT_IF_TRUE", "EVENT_IF_FALSE", "EVENT_SET_TRUE", "EVENT_SET_FALSE", "EVENT_IF_EXPRESSION", "EVENT_TEXT_SET_ANIMATION_SPEED", "EVENT_COMMENT", "EVENT_STOP",
                                "EVENT_OVERLAY_SHOW", "EVENT_OVERLAY_MOVE_TO",
                                "EVENT_CALL_CUSTOM_EVENT",
                                "EVENT_SET_INPUT_SCRIPT",
                                "EVENT_REMOVE_INPUT_SCRIPT",
                                "EVENT_SCENE_PUSH_STATE", "EVENT_SCENE_POP_STATE",
                                "EVENT_SCENE_POP_ALL_STATE", "EVENT_SCENE_RESET_STATE",
                                "EVENT_SET_PLATFORMER_CALLBACK_SCRIPT", "EVENT_REMOVE_PLATFORMER_CALLBACK_SCRIPT",
                                "EVENT_PLATFORMER_STATE_SET", "EVENT_PLAYER_BOUNCE",
                                "EVENT_ENGINE_FIELD_SET",
                                "EVENT_SAVE_DATA", "EVENT_LOAD_DATA", "EVENT_IF_SAVED_DATA"})
