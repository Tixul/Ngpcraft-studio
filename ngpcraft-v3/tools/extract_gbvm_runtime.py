"""M1: vendor the GBVM runtime functions the V3 ports, from GBVM's sources.

Decision D-2: scripts run on GBVM itself (scheduler, instruction handlers,
fade manager; MIT, (c) 2020 Toxa), adapted to C89 for cc900 and to the
TLCS-900. The functions are not rewritten by hand: this tool cuts them out of
GBVM's sources, applies the listed adaptations (each one an exact text
replacement that must match the stated number of times, with its reason),
and writes ngpcraft/runtime/gbvm_runtime.json with the source hashes and
GBVM's license. The exporter emits only the functions a game uses; vm_rpn is
stored as parts so that only the RPN cases a game uses are emitted. A test
re-extracts and compares.

Sources (GBVM pinned by GB Studio 4.3.2, tools/reference.py):
  appData/engine/gbvm/src/core/{vm.c, vm_gameboy.c, fade_manager.c}
  appData/engine/gbvm/include/{vm.h, vm_gameboy.h, fade_manager.h}
  appData/engine/gbvm/LICENSE

  python -B -m tools.extract_gbvm_runtime write | check
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
from tools.extract_gbvm_model import write_json
from tools.reference import GBVM_COMMIT, LABEL, REFERENCE
OUT = ROOT / "ngpcraft" / "runtime" / "gbvm_runtime.json"
GBVM = "appData/engine/gbvm"
SOURCES = {"vm_c": f"{GBVM}/src/core/vm.c", "vm_h": f"{GBVM}/include/vm.h",
           "vm_gameboy_c": f"{GBVM}/src/core/vm_gameboy.c", "vm_gameboy_h": f"{GBVM}/include/vm_gameboy.h",
           "fade_c": f"{GBVM}/src/core/fade_manager.c", "fade_h": f"{GBVM}/include/fade_manager.h",
           "events_c": f"{GBVM}/src/core/events.c", "events_h": f"{GBVM}/include/events.h",
           "ui_c": f"{GBVM}/src/core/ui.c", "vm_ui_c": f"{GBVM}/src/core/vm_ui.c",
           "vm_camera_c": f"{GBVM}/src/core/vm_camera.c",
           "vm_camera_h": f"{GBVM}/include/vm_camera.h",
           "license": f"{GBVM}/LICENSE"}
ANNOTATION = r"(?:OLDCALL BANKED|OLDCALL NONBANKED|NONBANKED|BANKED)"
DEFINES = ("VM_MAX_CONTEXTS", "VM_CONTEXT_STACK_SIZE", "VM_HEAP_SIZE", "INSTRUCTIONS_PER_QUANT",
           "SCRIPT_TERMINATED", "INSTRUCTION_SIZE", "RUNNER_DONE", "RUNNER_IDLE", "RUNNER_BUSY",
           "RUNNER_EXCEPTION", "EXCEPTION_CODE_NONE")
# Defines copied as C text: (source, name pattern).
C_DEFINES = (("vm_camera_h", r"CAMERA_SHAKE_\w+"), ("vm_h", r"VM_OP_\w+"), ("vm_gameboy_h", r"FADE_(?:MODE_MODAL|DIR_IN)"),
             ("fade_c", r"FADED_(?:OUT|IN)_FRAME"))
# (source, function).
FUNCTIONS = (("vm_camera_c", "vm_camera_move_to"), ("vm_camera_c", "vm_camera_set_pos"), ("vm_c", "script_detach_hthread"), ("vm_camera_c", "camera_shake_frames"), ("vm_c", "wait_frames"), ("vm_c", "vm_invoke"), ("vm_c", "vm_reserve"), ("vm_c", "vm_set"),
             ("vm_c", "vm_get_tlocal"), ("vm_c", "vm_set_const"), ("vm_c", "vm_memset"), ("vm_c", "vm_idle"), ("vm_c", "vm_jump"), ("vm_c", "vm_if_const"), ("vm_c", "vm_if"),
             ("vm_c", "vm_switch"), ("vm_c", "vm_rand"), ("vm_c", "vm_init_rng"), ("vm_c", "vm_rate_limit_const"),
             ("vm_c", "vm_lock"), ("vm_c", "vm_unlock"), ("vm_c", "vm_pop"),
             ("vm_c", "vm_rpn"), ("vm_gameboy_c", "vm_fade"),
             ("fade_c", "fade_init"), ("fade_c", "fade_in"), ("fade_c", "fade_out"), ("fade_c", "fade_update"),
             ("fade_c", "fade_in_modal"), ("fade_c", "fade_out_modal"),
             ("vm_c", "vm_raise"), ("vm_c", "vm_call_far"), ("vm_c", "vm_ret_far"), ("vm_c", "vm_push"),
             ("vm_c", "vm_push_value_ind"), ("vm_c", "vm_set_indirect"), ("vm_c", "vm_get_indirect"),
             ("vm_c", "vm_push_value"), ("vm_c", "vm_push_reference"),
             # EVENT_THREAD_START / EVENT_THREAD_STOP (docs/M4_THREADS.md).
             ("vm_c", "vm_beginthread"), ("vm_c", "vm_terminate"),
             # EVENT_ACTOR_EFFECTS loops.
             ("vm_c", "vm_loop"),
             # EVENT_SAVE_DATA (docs/M5_SAVE.md): emitted only with VM_POLL_LOADED.
             ("vm_c", "vm_poll_loaded"), ("vm_c", "script_terminate"), ("vm_c", "script_runner_init"), ("vm_c", "script_execute"), ("vm_c", "script_runner_update"),
             ("vm_gameboy_c", "vm_context_prepare"), ("vm_gameboy_c", "vm_input_attach"),
             ("vm_gameboy_c", "vm_input_detach"), ("vm_gameboy_c", "vm_input_wait"),
             ("vm_gameboy_c", "vm_timer_prepare"), ("vm_gameboy_c", "vm_timer_set"), ("vm_gameboy_c", "vm_timer_stop"),
             ("vm_gameboy_c", "vm_timer_reset"), ("events_c", "timers_init"), ("events_c", "timers_update"),
             ("events_c", "events_init"), ("events_c", "events_update"),
             # Text (docs/M2_TEXT.md): ui.c logic and vm_ui.c instructions; the
             # K2GE parts (tiles, map, VWF printing) are emit_ui.UI_SUPPORT.
             ("ui_c", "ui_init"), ("ui_c", "ui_print_reset"), ("ui_c", "ui_set_start_tile"),
             ("ui_c", "ui_draw_frame"), ("ui_c", "ui_draw_text_buffer_char"), ("ui_c", "ui_update"),
             ("ui_c", "ui_run_modal"), ("vm_ui_c", "load_text"), ("vm_ui_c", "vm_load_text"),
             ("vm_ui_c", "vm_display_text"), ("vm_ui_c", "vm_switch_text_layer"), ("vm_ui_c", "vm_overlay_wait"),
             ("vm_ui_c", "vm_overlay_move_to"), ("vm_ui_c", "vm_overlay_set_scroll"),
             ("vm_ui_c", "vm_overlay_clear"),
             # EVENT_CHOICE (docs/M4_CHOICE.md): emitted only with VM_CHOICE.
             ("ui_c", "ui_run_menu"), ("vm_ui_c", "vm_choice"),
             # EVENT_OVERLAY_SHOW (docs/M4_OVERLAY.md): emitted only with VM_OVERLAY_SHOW.
             ("vm_ui_c", "vm_overlay_show"), ("vm_ui_c", "vm_overlay_setpos"),
             # EVENT_SET_FONT: emitted only with VM_SET_FONT.
             ("vm_ui_c", "vm_set_font"))
# ngc_vm_start (emit.py) starts the VM; the game calls the scheduler, and
# events_update (core.c process_VM) when input scripts are used.
PUBLIC = ("script_runner_update", "events_update", "timers_update")

C89 = "C89: declarations before statements (cc900 refuses late declarations)"
PTR = ("TLCS-900: pointers are 4 bytes and the bytecode holds a 2-byte operand; a memory "
       "operand is an index in the NGPC engine memory table (assembler.ENGINE_MEMORY)")
LE16 = "read the little-endian .dw operand byte by byte (no unaligned word access assumed)"
BANKPC = ("TLCS-900: a script pointer is 4 bytes and the VM stack holds words: the return address is "
          "kept as the offset in its bank (assembler.py: a script address is an offset in its bank array), "
          "as on the Game Boy where a 16-bit address is the place in the bank")
CGB = ("colour console: the CGB branch is kept (a GB Studio colour project runs it: 116 text tiles over "
       "two VRAM banks, palettes per map cell), the DMG code removed")
FONT = ("fonts are ROM tables of the export (no banks on the NGPC): ngc_ui_font_load copies the font "
        "descriptor as MemcpyBanked did")
MAP = ("K2GE scroll map: one 16-bit cell per tile (tile number and palette), so the map pointers are "
       "word pointers into SCR1 (0x9000) instead of byte pointers into the Game Boy window map")
INPUT = ("NGPC input of the scene engine (scene_input.c): input_update is ngc_input_update, "
         "joy / last_joy / joy_pressed are ngc_joy / ngc_last_joy / ngc_joy_pressed (emit_ui INPUT macros)")
MENU = ("the .MENUITEM operands are 6 bytes of bytecode (vm.i), read byte by byte "
        "(ngc_menu_item_load): no assumption on the C layout of menu_item_t on the TLCS-900 or on "
        "the alignment of the bytecode; no ROM banks on the NGPC")
RAND = ("GBDK 4.3.0 rand / randw / initrand (libc/asm/sm83/rand.s, one 16-bit seed): independent C in "
        "emit.py, seed = 17*seed + 0x5c93; rand returns the new high byte, randw (OLDCALL, DE) the new "
        "low byte above the new high byte")
IND = ("VM_OP_REF_MEM_IND reads the 16-bit address held by a variable; GB Studio emits it only for "
       "Variable Data Table, whose rows follow the script in its bank: the address is an offset in the "
       "executing script's bank (assembler.py local labels), little-endian as sdasgb's .dw")
# (function, old, new, count, reason)
ADAPTATIONS = (
    ("vm_memset", "    for (INT16 i = 0, *v = VM_REF_TO_PTR(idx); i != count; i++) *v++ = value;",
     "    INT16 i;\n    INT16 *v = VM_REF_TO_PTR(idx);\n    for (i = 0; i != count; i++) *v++ = value;", 1, C89),
    ("vm_memset", "VM_REF_TO_PTR(idx)",
     "((idx < 0) ? (INT16 *)THIS->stack_ptr + idx : (INT16 *)script_memory + idx)", 1,
     "vm.h reference resolution explicit: independent of actor/UI support, signed word pointers"),
    ("vm_camera_move_to", "    THIS->waitable = 1;", "    camera_position_t * params;\n    UBYTE x_dest;\n    THIS->waitable = 1;", 1, C89),
    ("vm_camera_move_to", "    camera_position_t * params =", "    params =", 1, C89),
    ("vm_camera_move_to", "    UBYTE x_dest =", "    x_dest =", 1, C89),
    ("vm_camera_move_to", "(INSTRUCTION_SIZE + sizeof(idx) + sizeof(speed) + sizeof(after_lock_camera))", "5u", 1,
     "encoded VM_CAMERA_MOVE_TO length: opcode + signed word + two bytes; explicit on all hosts"),
    ("vm_rpn", ": script_memory + idx", ": (INT16 *)script_memory + idx", 7,
     "C89 conditional references: both branches are signed-word pointers, "
     "as ARGS; preserves the corresponding signed/unsigned word alias and the index"),
    ("vm_rpn", "*((INT8 *)idx)", "*((INT8 *)(ngc_banks[THIS->bank] + (UWORD)idx))", 1, IND),
    ("vm_rpn", "*((UINT8 *)idx)", "*((UINT8 *)(ngc_banks[THIS->bank] + (UWORD)idx))", 1, IND),
    ("vm_rpn", "*((INT16 *)idx)", "NGC_LE16(ngc_banks[THIS->bank] + (UWORD)idx)", 1, IND),

    ("camera_shake_frames", "rand()", "ngc_rand()", 2, RAND),
    ("vm_rate_limit_const", "UINT16", "UWORD", 2, "UINT16 is UWORD (GBDK types.h); one type name in the export"),
    ("vm_rate_limit_const", "sys_time", "ngc_sys_time", 2,
     "GBDK sys_time, the 16-bit VBlank clock, is startup.c's ngc_sys_time (runtime unit vblank_clock)"),
    ("vm_rand", "UINT16", "UWORD", 3, "UINT16 is UWORD (GBDK types.h); one type name in the export"),
    ("vm_rand", "randw()", "ngc_randw()", 1, RAND),
    ("vm_init_rng", "UINT16", "UWORD", 1, "UINT16 is UWORD (GBDK types.h); one type name in the export"),
    ("vm_init_rng", "initrand(*A)", "ngc_initrand(*A)", 1, RAND),
    ("vm_rpn", "randw()", "ngc_randw()", 1, RAND),
    ("camera_shake_frames", "((UWORD)sys_time - *((SCRIPT_CTX *)THIS)->stack_ptr)",
     "((UWORD)((UWORD)sys_time - *((SCRIPT_CTX *)THIS)->stack_ptr))", 1,
     "explicit 16-bit VBlank elapsed time, including wrap, also on the host C oracle"),
    ("vm_invoke", "    UWORD * stack_frame =", "    UBYTE start;\n    UWORD * stack_frame =", 1, C89),
    ("vm_invoke", "    UBYTE start =", "    start =", 1, C89),
    ("vm_invoke", "(INSTRUCTION_SIZE + sizeof(bank) + sizeof(fn) + sizeof(nparams) + sizeof(idx))", "7u", 1,
     "rewind by the encoded instruction length (1+2+1+2+1 bytes, vm.i VM_INVOKE); on TLCS-900 "
     "sizeof(fn) is a 4-byte pointer while the bytecode holds 2"),
    ("vm_beginthread", "DUMMY0_t dummy0, DUMMY1_t dummy1, SCRIPT_CTX * THIS", "SCRIPT_CTX * THIS", 1,
     "the two dummy words belong to the Game Boy banked-call ABI"),
    ("vm_beginthread", "    dummy0; dummy1;\n", "", 1, "see above"),
    ("vm_beginthread", "    UWORD * A;\n", "    UWORD * A;\n    SCRIPT_CTX * ctx;\n    UBYTE i;\n    INT16 v;\n", 1, C89),
    ("vm_beginthread", "    SCRIPT_CTX * ctx = script_execute", "    ctx = script_execute", 1, C89),
    ("vm_beginthread", "        SWITCH_ROM(THIS->bank);             \n", "", 1,
     "no ROM banks on the NGPC: the bytecode is always mapped"),
    ("vm_beginthread", "for (UBYTE i = nargs;", "for (i = nargs;", 1, C89),
    ("vm_beginthread", "            INT16 A = *((INT16 *)THIS->PC);\n"
                       "            A = (A < 0) ? *(THIS->stack_ptr + A) : *(script_memory + A);\n"
                       "            *(ctx->stack_ptr++) = (UWORD)A;\n",
     "            v = NGC_LE16(THIS->PC);\n"
     "            v = (v < 0) ? *(THIS->stack_ptr + v) : *(script_memory + v);\n"
     "            *(ctx->stack_ptr++) = (UWORD)v;\n", 1,
     LE16 + "; the inner INT16 A shadowed the handle pointer A (C89: one declaration, renamed v)"),
    ("vm_if_const", "    INT16 A;", "    INT16 A;\n    UBYTE res;", 1, C89),
    ("vm_if_const", "    UBYTE res = FALSE;", "    res = FALSE;", 1, C89),
    ("vm_if", "    INT16 A, B;", "    INT16 A, B;\n    UBYTE res;", 1, C89),
    ("vm_if", "    UBYTE res = FALSE;", "    res = FALSE;", 1, C89),
    ("vm_switch", "DUMMY0_t dummy0, DUMMY1_t dummy1, SCRIPT_CTX * THIS", "SCRIPT_CTX * THIS", 1,
     "the two dummy words belong to the Game Boy banked-call ABI"),
    ("vm_switch", "    dummy0; dummy1; \n", "", 1, "see above"),
    ("vm_switch", "INT16 value, * table;", "INT16 value;\n    const UBYTE * table;", 1, LE16),
    ("vm_switch", "table = (INT16 *)(THIS->PC);", "table = THIS->PC;", 1, LE16),
    ("vm_switch", "        if (value == *table++) {\n            THIS->PC = (UBYTE *)(*table);",
     "        if (value == NGC_LE16(table)) {\n            THIS->PC = ngc_banks[THIS->bank] + (UWORD)NGC_LE16(table + 2);", 1,
     LE16 + "; the case label is an offset in the script's bank (assembler.py), as VM_JUMP's"),
    ("vm_switch", "        } else table++;", "        } else table += 4;", 1,
     "one case = two .dw words (value, label): the original steps one word in the test and one here"),
    ("vm_switch", "THIS->PC = (UBYTE *)table;", "THIS->PC = table;", 1, LE16),
    ("vm_rpn", "DUMMY0_t dummy0, DUMMY1_t dummy1, SCRIPT_CTX * THIS", "SCRIPT_CTX * THIS", 1,
     "the two dummy words belong to the Game Boy banked-call ABI"),
    ("vm_rpn", "    dummy0; dummy1; ", "", 1, "see above"),
    ("vm_rpn", "*((INT16 *)(THIS->PC))", "NGC_LE16(THIS->PC)", 5, LE16),
    ("vm_rpn", "*((UWORD *)(THIS->PC))", "(UWORD)NGC_LE16(THIS->PC)", 1, LE16),
    ("vm_rpn", "**((INT8 **)(THIS->PC))", "*((INT8 *)ngc_memory(NGC_LE16(THIS->PC)))", 2, PTR),
    ("vm_rpn", "**((UINT8 **)(THIS->PC))", "*((UINT8 *)ngc_memory(NGC_LE16(THIS->PC)))", 2, PTR),
    ("vm_rpn", "**((INT16 **)(THIS->PC))", "*((INT16 *)ngc_memory(NGC_LE16(THIS->PC)))", 2, PTR),
    ("vm_rpn", "(bool)(*A)", "(*A != 0)", 2, "no bool type in C89"),
    ("vm_rpn", "(bool)(*B)", "(*B != 0)", 2, "no bool type in C89"),
    ("vm_rpn", "*(uint16_t *)A", "*(UWORD *)A", 2, "no stdint.h with cc900"),
    ("script_execute", "UBYTE nargs, ...)", "UBYTE nargs)", 1,
     "no stdarg in cc900: script_execute takes nargs = 0; specialized callers push known thread-local arguments explicitly"),
    ("script_execute", "    if (free_ctxs == NULL) return NULL;",
     "    SCRIPT_CTX * tmp;\n    if (free_ctxs == NULL) return NULL;", 1, C89),
    ("script_execute", "    SCRIPT_CTX * tmp = free_ctxs;", "    tmp = free_ctxs;", 1, C89),
    ("script_execute", "    tmp->update_fn_bank = 0;",
     "    tmp->update_fn = 0; tmp->update_fn_bank = 0;", 1,
     "NGPC native functions use bank 0: clearing only the bank no longer invalidates a pending "
     "VM_INVOKE when a terminated context is reused; clear the pointer as well"),
    ("script_runner_init", "void script_runner_init(UBYTE reset) {",
     "void script_runner_init(UBYTE reset) {\n    UWORD * base_addr;\n    SCRIPT_CTX * nxt;\n"
     "    SCRIPT_CTX * tmp;\n    UBYTE i;", 1, C89),
    ("script_runner_init", "memset(script_memory, 0, sizeof(script_memory));",
     "ngc_clear((UBYTE *)script_memory, sizeof(script_memory));", 1, "no libc memset with cc900"),
    ("script_runner_init", "memset(CTXS, 0, sizeof(CTXS));", "ngc_clear((UBYTE *)CTXS, sizeof(CTXS));", 1,
     "no libc memset with cc900"),
    ("script_runner_init", "    UWORD * base_addr = &script_memory", "    base_addr = &script_memory", 1, C89),
    ("script_runner_init", "    SCRIPT_CTX * nxt = 0;", "    nxt = 0;", 1, C89),
    ("script_runner_init", "    SCRIPT_CTX * tmp = CTXS", "    tmp = CTXS", 1, C89),
    ("script_runner_init", "for (UBYTE i = VM_MAX_CONTEXTS;", "for (i = VM_MAX_CONTEXTS;", 1, C89),
    ("vm_call_far", "*(THIS->stack_ptr++) = (UWORD)THIS->PC;",
     "*(THIS->stack_ptr++) = (UWORD)(THIS->PC - ngc_banks[THIS->bank]);", 1, BANKPC),
    ("vm_ret_far", "THIS->PC = (const UBYTE *)*(THIS->stack_ptr);",
     "THIS->PC = ngc_banks[THIS->bank] + *(THIS->stack_ptr);", 1, BANKPC),
    ("vm_context_prepare", "    THIS;\n    script_event_t * event = &input_events[(slot - 1) & 7];",
     "    script_event_t * event = &input_events[(slot - 1) & 7];\n    (void)THIS;", 1, C89),
    ("vm_input_attach", "    THIS;\n    UBYTE * current_slot = input_slots;\n    for (UBYTE tmp = mask;",
     "    UBYTE * current_slot = input_slots;\n    UBYTE tmp;\n    (void)THIS;\n    for (tmp = mask;", 1, C89),
    ("vm_input_wait", "if ((joy != last_joy) && (joy & mask)) return;",
     "if ((ngc_joy != ngc_last_joy) && (ngc_joy & mask)) return;", 1, INPUT),
    ("vm_input_detach", "    THIS;\n    UBYTE * current_slot = input_slots;\n    for (UBYTE tmp = mask;",
     "    UBYTE * current_slot = input_slots;\n    UBYTE tmp;\n    (void)THIS;\n    for (tmp = mask;", 1, C89),
    ("vm_timer_prepare", "    THIS;\n    script_event_t * event = &timer_events[(timer - 1) & 3];",
     "    script_event_t * event = &timer_events[(timer - 1) & 3];\n    (void)THIS;", 1, C89),
    ("vm_timer_set", "    THIS;\n    timer_time_t * timer_value = &timer_values[(timer - 1) & 3];",
     "    timer_time_t * timer_value = &timer_values[(timer - 1) & 3];\n    (void)THIS;", 1, C89),
    ("vm_timer_stop", "    THIS;\n    timer_time_t * timer_value = &timer_values[(timer - 1) & 3];",
     "    timer_time_t * timer_value = &timer_values[(timer - 1) & 3];\n    (void)THIS;", 1, C89),
    ("vm_timer_reset", "    THIS;\n    timer_time_t * timer_value = &timer_values[(timer - 1) & 3];",
     "    timer_time_t * timer_value = &timer_values[(timer - 1) & 3];\n    (void)THIS;", 1, C89),
    ("timers_init", "void timers_init(UBYTE preserve) {", "void timers_init(UBYTE preserve) {\n    UBYTE i;", 1, C89),
    ("timers_init", "for (UBYTE i = 0; i != MAX_CONCURRENT_TIMERS; i++)", "for (i = 0; i != MAX_CONCURRENT_TIMERS; i++)", 1, C89),
    ("timers_init", "memset(timer_values, 0, sizeof(timer_values));",
     "ngc_clear((UBYTE *)timer_values, sizeof(timer_values));", 1, "no libc memset with cc900"),
    ("timers_init", "memset(timer_events, 0, sizeof(timer_events));",
     "ngc_clear((UBYTE *)timer_events, sizeof(timer_events));", 1, "no libc memset with cc900"),
    ("timers_update", "    timer_time_t * ctimer = timer_values;\n    for (UBYTE i = 0;",
     "    timer_time_t * ctimer = timer_values;\n    UBYTE i;\n    script_event_t * event;\n    for (i = 0;", 1, C89),
    ("timers_update", "                script_event_t * event = &timer_events[i];", "                event = &timer_events[i];", 1, C89),
    ("timers_update", "&event->handle, 0, 0);", "&event->handle, 0);", 1,
     "no stdarg in cc900: script_execute takes nargs = 0 and no argument (as GBVM passes 0, 0)"),
    ("events_init", "void events_init(UBYTE preserve) {", "void events_init(UBYTE preserve) {\n    UBYTE i;", 1, C89),
    ("events_init", "for (UBYTE i = 0; i < 8; i++)", "for (i = 0; i < 8; i++)", 1, C89),
    ("events_init", "memset(input_slots, 0, sizeof(input_slots));",
     "ngc_clear((UBYTE *)input_slots, sizeof(input_slots));", 1, "no libc memset with cc900"),
    ("events_init", "memset(input_events, 0, sizeof(input_events));",
     "ngc_clear((UBYTE *)input_events, sizeof(input_events));", 1, "no libc memset with cc900"),
    ("events_update", "    UBYTE * slot_ptr = input_slots;\n    for (UBYTE tmp = joy, key = 1;",
     "    UBYTE * slot_ptr = input_slots;\n    UBYTE tmp, key;\n    script_event_t * event;\n"
     "    SCRIPT_CTX * ctx;\n    for (tmp = ngc_joy, key = 1;", 1, C89),
    ("events_update", "            script_event_t * event = &input_events",
     "            event = &input_events", 1, C89),
    ("events_update", "            if (*slot_ptr & 0x80) joy ^= key;",
     "            if (*slot_ptr & 0x80) { ngc_joy ^= key; ngc_joy_pressed &= (UBYTE)~key; }", 1,
     "NGPC input state of the scene engine (scene_input.c): joy, last_joy, recent_joy are ngc_joy, "
     "ngc_last_joy, ngc_recent_joy, refreshed by ngc_input_update once per game tick as input_update. "
     "GBVM computes 'pressed' on use (input.h INPUT_PRESSED: joy & ~last_joy), so clearing an "
     "overridden button in joy also un-presses it; the scene engine keeps ngc_joy_pressed computed "
     "by ngc_input_update, hence cleared here too (measured: without it, A held with an overriding "
     "input script still triggered the topdown interaction)"),
    ("events_update", "            if (last_joy & key) continue;", "            if (ngc_last_joy & key) continue;", 1,
     "see above"),
    ("events_update", "    recent_joy = recent_joy & joy;", "    ngc_recent_joy = ngc_recent_joy & ngc_joy;", 1,
     "see above"),
    ("events_update",
     "script_execute(event->script_bank, event->script_addr, &event->handle, 1, (int)key);",
     "{ ctx = script_execute(event->script_bank, event->script_addr, &event->handle, 0);\n"
     "                  if (ctx) *(ctx->stack_ptr++) = (UWORD)key; }", 1,
     "no stdarg in cc900 (script_execute nargs = 0): the one thread-local argument, the button, is pushed "
     "as vm.c's varargs block does"),
    ("vm_push_reference", "((((UWORD)(THIS->stack_ptr) - (UWORD)script_memory) >> 1) + idx)",
     "((UWORD)(THIS->stack_ptr - script_memory) + idx)", 1,
     "TLCS-900: pointers are 4 bytes: the word index of the stack slot is a pointer difference in "
     "words, not the difference of 16-bit truncated addresses shifted right"),
    # -- text: ui.c / vm_ui.c (docs/M2_TEXT.md) ---------------------------------
    ("ui_init", "    vwf_current_font_bank       = ui_fonts[0].bank;\n"
     "    MemcpyBanked(&vwf_current_font_desc, ui_fonts[0].ptr, sizeof(font_desc_t), vwf_current_font_bank);",
     "    vwf_current_font_bank       = 0;\n    ngc_ui_font_load(0u);", 1, FONT),
    ("vm_set_font", "    THIS;\n", "    (void)THIS;\n", 1, "unused parameter, C89 statement form"),
    ("vm_set_font", "    vwf_current_font_bank = ui_fonts[font_index].bank;\n"
     "    MemcpyBanked(&vwf_current_font_desc, ui_fonts[font_index].ptr, sizeof(font_desc_t), vwf_current_font_bank);",
     "    ngc_ui_font_load(font_index);", 1, FONT),
    ("ui_init", "#ifdef CGB\n    overlay_priority            = S_PRIORITY;\n"
     "    text_palette                = UI_DEFAULT_PALETTE;\n#endif\n",
     "    overlay_priority            = S_PRIORITY;\n    text_palette                = UI_DEFAULT_PALETTE;\n", 1, CGB),
    ("ui_print_reset", "    if (vwf_current_offset) ui_next_tile();",
     "    ngc_ui_flush();\n    if (vwf_current_offset) ui_next_tile();", 1,
     "tile rows are copied once per tile (emit_ui.py ngc_ui_flush): copy them before the tile changes"),
    ("ui_set_start_tile", "    ui_prev_tile = ui_current_tile = start_tile;",
     "    ngc_ui_flush();\n    ui_prev_tile = ui_current_tile = start_tile;", 1,
     "tile rows are copied once per tile (emit_ui.py ngc_ui_flush): copy them before the tile changes"),
    ("ui_print_reset", "memset(vwf_tile_data, text_bkg_fill, sizeof(vwf_tile_data));",
     "ngc_ui_fill(vwf_tile_data, text_bkg_fill, sizeof(vwf_tile_data));", 1, "no libc memset with cc900"),
    ("ui_set_start_tile", "memset(vwf_tile_data, text_bkg_fill, sizeof(vwf_tile_data));",
     "ngc_ui_fill(vwf_tile_data, text_bkg_fill, sizeof(vwf_tile_data));", 1, "no libc memset with cc900"),
    ("ui_draw_frame", "    if (height == 0) return;\n    UBYTE * base_addr = GetWinAddr() + (y << 5) + x;\n",
     "    volatile UWORD * base_addr;\n    UBYTE i;\n    if (height == 0) return;\n"
     "    base_addr = GetWinAddr() + (y << 5) + x;\n", 1, C89 + "; " + MAP),
    ("ui_draw_frame", "for (UBYTE i = height - 1; i != 0; i--)", "for (i = height - 1; i != 0; i--)", 2, C89),
    ("ui_draw_frame", "#ifdef CGB\n    if (_is_CGB) {\n", "    {\n", 1, CGB),
    ("ui_draw_frame", "        return;\n    }\n#endif\n    ui_draw_frame_row(base_addr, ui_frame_tl_tiles, width);\n"
     "    if (--height == 0) return;\n    if (height > 1)\n        for (i = height - 1; i != 0; i--) {\n"
     "            base_addr += 32;\n            ui_draw_frame_row(base_addr, ui_frame_l_tiles, width);\n        }\n"
     "    base_addr += 32;\n    ui_draw_frame_row(base_addr, ui_frame_bl_tiles, width);\n",
     "        return;\n    }\n", 1, CGB),
    ("ui_draw_text_buffer_char",
     "                    const far_ptr_t * font = ui_fonts + vwf_current_font_idx;\n"
     "                    MemcpyBanked(&vwf_current_font_desc, font->ptr, sizeof(font_desc_t), "
     "vwf_current_font_bank = font->bank);\n",
     "                    ngc_ui_font_load(vwf_current_font_idx);\n", 1, FONT),
    ("ui_draw_text_buffer_char",
     "                current_font_idx = *(++ui_text_ptr) - 1u;\n"
     "                const far_ptr_t * font = ui_fonts + current_font_idx;\n"
     "                UBYTE old_flags = vwf_current_font_desc.attr;\n"
     "                MemcpyBanked(&vwf_current_font_desc, font->ptr, sizeof(font_desc_t), "
     "vwf_current_font_bank = font->bank);\n",
     "                UBYTE old_flags;\n                current_font_idx = *(++ui_text_ptr) - 1u;\n"
     "                old_flags = vwf_current_font_desc.attr;\n                ngc_ui_font_load(current_font_idx);\n",
     1, C89 + "; " + FONT),
    ("ui_draw_text_buffer_char",
     "                ui_dest_ptr = ui_dest_base = text_render_base_addr + (*++ui_text_ptr - 1u) + "
     "(*++ui_text_ptr - 1u) * 32u;\n",
     "                {\n                    INT16 gx, gy;\n                    gx = (INT16)*++ui_text_ptr - 1;\n"
     "                    gy = (INT16)*++ui_text_ptr - 1;\n"
     "                    ui_dest_ptr = ui_dest_base = text_render_base_addr + (INT16)(gx + gy * 32);\n"
     "                }\n", 1,
     "two `*++ui_text_ptr` in one expression are unsequenced in C: read x then y explicitly (the order "
     "of the \\003 code, the order SDCC evaluates); the Game Boy's 16-bit pointer arithmetic wraps, so "
     "the offset is a signed 16-bit value on the 32-bit TLCS-900 pointer"),
    ("ui_draw_text_buffer_char",
     "                BYTE dx = (BYTE)(*++ui_text_ptr);\n                if (dx > 0) dx--;\n"
     "                BYTE dy = (BYTE)(*++ui_text_ptr);\n                if (dy > 0) dy--;\n"
     "                ui_dest_base = ui_dest_ptr += dx + dy * 32u;\n",
     "                BYTE dx, dy;\n                dx = (BYTE)(*++ui_text_ptr);\n                if (dx > 0) dx--;\n"
     "                dy = (BYTE)(*++ui_text_ptr);\n                if (dy > 0) dy--;\n"
     "                ui_dest_base = ui_dest_ptr += (INT16)(dx + dy * 32);\n", 1,
     C89 + "; `dx + dy * 32u` is unsigned: on the Game Boy's 16-bit pointers it wraps to a step back, "
     "on the 32-bit TLCS-900 pointer it would jump forward: a signed 16-bit offset"),
    ("ui_draw_text_buffer_char",
     "                if ((ui_dest_ptr + 32u) > (UBYTE *)((((UWORD)text_scroll_addr + "
     "((UWORD)text_scroll_height << 5)) & 0xFFE0) - 1)) {\n",
     "                if ((UWORD)(ui_dest_ptr - NGC_WIN_MAP + 32) > (UWORD)((((UWORD)(text_scroll_addr - NGC_WIN_MAP)"
     " + ((UWORD)text_scroll_height << 5)) & 0xFFE0u) - 1u)) {\n", 1,
     MAP + "; the Game Boy compares map addresses (window map 0x9C00 is 32-aligned, so `& 0xFFE0` "
     "is the start of a map row): the same test on cell indexes from the window map"),
    ("ui_draw_text_buffer_char",
     "#ifdef CGB\n                    if (_is_CGB) {\n                        VBK_REG = VBK_ATTRIBUTES;\n"
     "                        scroll_rect(text_scroll_addr, text_scroll_width, text_scroll_height, "
     "overlay_priority | (text_palette & 0x07u));\n                        VBK_REG = VBK_TILES;\n"
     "                    }\n#endif\n", "", 1,
     "a K2GE map cell holds the tile and its palette together: one scroll_rect moves both (the CGB "
     "scrolls the attribute bank in a second pass)"),
    ("ui_update",
     "    if ((flag) && (text_sound_bank != SFX_STOP_BANK)) music_play_sfx(text_sound_bank, text_sound_data, "
     "text_sound_mask, MUSIC_SFX_PRIORITY_NORMAL);\n", "    (void)flag;\n", 1,
     "text sound effect not delivered (EVENT_TEXT_SET_SOUND_EFFECT): text_sound_bank stays SFX_STOP_BANK, "
     "so this call never ran"),
    ("ui_run_modal", "    input_update();\n    do {", "    ngc_input_update();\n    do {", 1, INPUT),
    ("ui_run_modal",
     "        ui_update();\n\n        toggle_shadow_OAM();\n        camera_update();\n        scroll_update();\n"
     "        actors_update();\n        actors_render();\n        projectiles_render();\n"
     "        activate_shadow_OAM();\n\n        game_time++;\n        wait_vbl_done();\n        input_update();\n",
     "        ui_update();\n#if NGC_TICK_FRAMES == 2\n        game_time++;\n        ui_update();\n"
     "        game_time--;\n#endif\n        ngc_ui_flush();\n        ngc_ui_modal_logic();\n\n"
     "        game_time += NGC_TICK_FRAMES;\n"
     "        ngc_vm_frame_end();\n        ngc_ui_modal_commit();\n        ngc_input_update();\n", 1,
     "ngc_ui_flush: the rows ui_update drew reach tile RAM in the same frame, as ui.c's per-character "
     "writes did (emit_ui.py); at 30 per second (NGC_TICK_FRAMES 2) a modal tick is two pictures: "
     "ui_update for each, game_time + 2, ngc_vm_frame_end keeps the fixed cadence (wait_vbl_done at 60); "
     "the scene engine's frame (game.c): ngc_ui_modal_logic is camera_update, scroll and activation, "
     "actors_update and actors_render into the shadow OAM, projectiles rendered but not updated "
     "(ngc_actors_update_modal when the export has projectiles); the shadow OAM, "
     "scroll and streaming reach the K2GE in the vertical blank (ngc_ui_modal_commit), as at the start "
     "of every tick; " + INPUT),
    ("load_text", "static const unsigned char * load_text(", "const unsigned char * load_text(", 1,
     "the exporter adds `static` to every vendored function"),
    ("vm_load_text", "void vm_load_text(DUMMY0_t dummy0, DUMMY1_t dummy1, SCRIPT_CTX * THIS, UBYTE nargs) {",
     "void vm_load_text(SCRIPT_CTX * THIS, UBYTE nargs) {", 1,
     "the two dummy words belong to the Game Boy banked-call ABI"),
    ("vm_load_text", "    dummy0; dummy1; \n", "", 1, "see above"),
    ("vm_load_text", "    SWITCH_ROM(THIS->bank);\n    const INT16 * sargs = THIS->PC;\n"
     "    INT16 * dargs = ui_text_data + (sizeof(ui_text_data) - sizeof(INT16));\n",
     "    const UBYTE * sargs;\n    INT16 * dargs;\n    SWITCH_ROM(THIS->bank);\n    sargs = THIS->PC;\n"
     "    dargs = (INT16 *)(ui_text_data + (sizeof(ui_text_data) - sizeof(INT16)));\n", 1,
     C89 + "; the .dw operands are read byte by byte (below)"),
    ("vm_load_text", "        *dargs-- = *((INT16 *)VM_REF_TO_PTR(*sargs));\n        ++sargs;\n",
     "        *dargs-- = *((INT16 *)VM_REF_TO_PTR(NGC_LE16(sargs)));\n        sargs += 2;\n", 1, LE16),
    ("vm_display_text", "#ifdef CGB\n        if (_is_CGB) {\n", "        {\n", 1, CGB),
    ("vm_display_text", "        } else {\n#endif\n            if (start_tile < (UBYTE)(0x100 - TEXT_BUFFER_START)) "
     "ui_set_start_tile(TEXT_BUFFER_START + start_tile, 0);\n#ifdef CGB\n        }\n#endif\n", "        }\n", 1, CGB),
    ("vm_overlay_wait", "    if (is_modal) {", "    UBYTE fail;\n    if (is_modal) {", 1, C89),
    ("vm_overlay_wait", "    UBYTE fail = 0;\n", "    fail = 0;\n", 1, C89),
    ("vm_overlay_clear",
     "#ifdef CGB\n        if (_is_CGB) {\n            VBK_REG = VBK_ATTRIBUTES;\n"
     "            fill_win_rect(x, y, w, h, overlay_priority | (text_palette & 0x07u));\n"
     "            VBK_REG = VBK_TILES;\n        }\n#endif\n", "", 1,
     "a K2GE map cell holds the tile and its palette together: fill_win_rect writes both"),
    ("ui_run_menu",
     "    MemcpyBanked(&current_menu_item, start_item + (current_index - 1u), sizeof(menu_item_t), bank);\n",
     "    (void)bank;\n    ngc_menu_item_load(&current_menu_item, start_item, (UBYTE)(current_index - 1u));\n", 1, MENU),
    ("ui_run_menu",
     "        MemcpyBanked(&current_menu_item, start_item + current_index - 1u, sizeof(menu_item_t), bank);\n",
     "        ngc_menu_item_load(&current_menu_item, start_item, (UBYTE)(current_index - 1u));\n", 1, MENU),
    ("ui_run_menu",
     "#ifdef CGB\n    if (_is_CGB) {\n        \n        VBK_REG = VBK_ATTRIBUTES;\n        set_win_tile_xy(current_menu_item.X, "
     "current_menu_item.Y, overlay_priority | (text_palette & 0x07u));\n        VBK_REG = VBK_TILES;\n    }\n#endif\n",
     "", 1, "a K2GE map cell holds the tile and its palette together: set_win_tile_xy writes both"),
    ("ui_run_menu",
     "#ifdef CGB\n        if (_is_CGB) {\n            \n            VBK_REG = VBK_ATTRIBUTES;\n"
     "            set_win_tile_xy(current_menu_item.X, current_menu_item.Y, overlay_priority | (text_palette & 0x07u));\n"
     "            VBK_REG = VBK_TILES;\n        }\n#endif\n",
     "", 2, "a K2GE map cell holds the tile and its palette together: set_win_tile_xy writes both"),
    ("ui_run_menu",
     "        input_update();\n        ui_update();\n\n        toggle_shadow_OAM();\n        camera_update();\n"
     "        scroll_update();\n        actors_update();\n        actors_render();\n        projectiles_render();\n"
     "        activate_shadow_OAM();\n\n        game_time++;\n        wait_vbl_done();\n",
     "        ngc_input_update();\n        ui_update();\n#if NGC_TICK_FRAMES == 2\n        game_time++;\n"
     "        ui_update();\n        game_time--;\n#endif\n        ngc_ui_flush();\n        ngc_ui_modal_logic();\n\n"
     "        game_time += NGC_TICK_FRAMES;\n        ngc_vm_frame_end();\n        ngc_ui_modal_commit();\n", 1,
     "the scene engine's modal frame, as in ui_run_modal (see there); " + INPUT),
    ("vm_choice", "    THIS->PC += sizeof(menu_item_t) * count;\n",
     "    THIS->PC += NGC_MENU_ITEM_BYTES * count;\n", 1, MENU),
)
VARARGS_BLOCK = re.compile(r"\n    if \(nargs\) \{\s*va_list va;.*?\n    \}\n", re.S)
# fade_manager.c: the CGB branch is the one a colour console runs; the DMG
# call after it is dead there (the NGPC is a colour machine).
CGB_BLOCK = re.compile(r"#ifdef CGB\n[ \t]*if \(_is_CGB\) \{\n[ \t]*ApplyPaletteChangeColor\((\w+)\);\n"
                       r"[ \t]*return;\n[ \t]*\}\n#endif\n([ \t]*)ApplyPaletteChangeDMG\(\w+\);")
CGB_COUNTS = {"fade_init": 1, "fade_in": 2, "fade_out": 2, "fade_update": 1}
RPN_CASE = re.compile(r"^ {16}case (VM_OP_\w+)\s*:")
RPN_END = re.compile(r"^ {16}default:")


class ExtractError(ValueError):
    pass


def cut(source, name):
    # ui.c ui_print_reset has no banking annotation; vm_ui.c load_text is a
    # static function returning a pointer.
    m = re.search(r"^(?:void|UBYTE|UWORD|SCRIPT_CTX \*|static const unsigned char \*) " + name
                  + r"\([^\n]*\)(?: " + ANNOTATION + r")? \{", source, re.M)
    if not m:
        raise ExtractError(f"function {name} not found")
    end, depth = m.end(), 1
    while depth and end < len(source):
        depth += {"{": 1, "}": -1}.get(source[end], 0)
        end += 1
    if depth:
        raise ExtractError(f"function {name} not closed")
    head = re.sub(" " + ANNOTATION + r" \{$", " {", source[m.start():m.end()])
    return head + source[m.end():end]


def split_cases(text):
    """vm_rpn -> parts: {"text"} kept always, {"case", "text"} kept if used."""
    parts, current = [], {"text": ""}
    for line in text.splitlines(keepends=True):
        m = RPN_CASE.match(line)
        if m or RPN_END.match(line):
            parts.append(current)
            current = {"case": m[1], "text": ""} if m else {"text": ""}
        current["text"] += line
    parts.append(current)
    return [p for p in parts if p["text"]]


def localize_rpn(text):
    """vm_rpn with THIS->PC and THIS->stack_ptr held in locals, written back
    at its two exits: cc900 reloads both through THIS at every access. Same
    reads and writes of script memory, same final PC and stack pointer."""
    for old, new in (("        static INT8 op;\n", "        INT8 op;\n"),
                     ("    INT16 idx;\n", "    INT16 idx;\n    const UBYTE * pc;\n    UWORD * sp;\n")):
        if text.count(old) != 1:
            raise ExtractError(f"vm_rpn: {old!r} changed")
        text = text.replace(old, new)
    if text.count("return;") != 2 or "THIS->PC = " in text:
        raise ExtractError("vm_rpn: exits changed")
    text = text.replace("THIS->PC", "pc").replace("THIS->stack_ptr", "sp")
    text, n = re.subn(r"    ARGS = sp;[ \t]*\n", "    pc = THIS->PC;\n    sp = THIS->stack_ptr;\n    ARGS = sp;\n", text)
    if n != 1:
        raise ExtractError("vm_rpn: ARGS initialisation changed")
    return text.replace("return;", "{ THIS->PC = pc; THIS->stack_ptr = sp; return; }")


def localize_runner(text):
    """script_runner_update with executing_ctx / old_executing_ctx in locals,
    written back at both exits (the runner resumes from them while the VM is
    locked). Only the runner and script_runner_init use these two globals; the
    handlers change first_ctx, free_ctxs, vm_lock_state and vm_exception_code,
    which stay global and are read where GBVM reads them. The static locals
    hold no state between calls (each is set on entry)."""
    old = "    static UBYTE waitable;\n    static UBYTE counter;\n    static FASTUBYTE _save;\n"
    new = ("    UBYTE waitable;\n    UBYTE counter;\n    FASTUBYTE _save;\n"
           "    SCRIPT_CTX * ctx;\n    SCRIPT_CTX * old;\n")
    reset = "    if (!vm_lock_state) old_executing_ctx = 0, executing_ctx = first_ctx;\n"
    exception = "                SWITCH_ROM(_save);\n                return RUNNER_EXCEPTION;\n"
    after = "    }\n    SWITCH_ROM(_save);\n\n"
    for target in (old, reset, exception, after):
        if text.count(target) != 1:
            raise ExtractError(f"script_runner_update: {target!r} changed")
    text = text.replace(old, new)
    text = text.replace(reset, "@RESET@\n")
    text = text.replace("old_executing_ctx", "old").replace("executing_ctx", "ctx")
    text = text.replace("@RESET@\n", "    ctx = executing_ctx;\n    old = old_executing_ctx;\n"
                                     "    if (!vm_lock_state) old = 0, ctx = first_ctx;\n")
    write_back = "old_executing_ctx = old, executing_ctx = ctx;\n"
    text = text.replace(exception, "                " + write_back + exception)
    text = text.replace(after, "    }\n    " + write_back + "    SWITCH_ROM(_save);\n\n")
    # Two reads on entry, two names in each of the two write-backs.
    if "old = old_ctx" in text or text.count("executing_ctx") != 6:
        raise ExtractError("script_runner_update: localisation incomplete")
    return text


def strip_comments(raw):
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("utf-8-sig"), flags=re.S)


def extract(root=REFERENCE):
    root = Path(root)
    raw = {k: (root / p).read_bytes() for k, p in SOURCES.items()}
    src = {k: strip_comments(v) for k, v in raw.items() if k != "license"}
    context = re.search(r"typedef struct SCRIPT_CTX\s*\{[^}]+\} SCRIPT_CTX;", src["vm_h"])
    direction = re.search(r"typedef enum \{ FADE_IN, FADE_OUT \} FADE_DIRECTION;", src["fade_h"])
    event = re.search(r"typedef struct script_event_t\s*\{[^}]+\} script_event_t;", src["events_h"])
    # events.h timers (EVENT_SET_TIMER_SCRIPT): the slot record and the slot count.
    timer = re.search(r"typedef struct timer_time_t\s*\{[^}]+\} timer_time_t;", src["events_h"])
    timers = re.findall(r"^#define MAX_CONCURRENT_TIMERS[ \t]+(\d+)[ \t]*$", src["events_h"], re.M)
    if not timer or len(timers) != 1:
        raise ExtractError("timer_time_t or MAX_CONCURRENT_TIMERS not found in events.h")
    if not context or not direction or not event:
        raise ExtractError("SCRIPT_CTX, FADE_DIRECTION or script_event_t not found")
    defines = {}
    for name in DEFINES:
        found = re.findall(r"^#define " + name + r"[ \t]+(0x[0-9A-Fa-f]+|\d+)[ \t]*$", src["vm_h"], re.M)
        if len(found) != 1:
            raise ExtractError(f"#define {name}: {len(found)} numeric definitions")
        defines[name] = int(found[0], 0)
    c_defines = {}
    for key, pattern in C_DEFINES:
        for name, value in re.findall(r"^#define (" + pattern + r")[ \t]+(-?\d+|0x[0-9A-Fa-f]+|'[ -~]')[ \t]*$",
                                      src[key], re.M):
            if name in c_defines:
                raise ExtractError(f"#define {name} twice")
            c_defines[name] = value
    functions, notes = {}, {}
    for key, name in FUNCTIONS:
        text = cut(src[key], name)
        applied = []
        for fn, old, new, count, reason in ADAPTATIONS:
            if fn != name:
                continue
            if text.count(old) != count:
                raise ExtractError(f"{name}: adaptation target changed: {old!r} x{text.count(old)}")
            text = text.replace(old, new)
            applied.append(reason)
        if name == "script_execute":
            text, n = VARARGS_BLOCK.subn("\n    (void)nargs;\n", text)
            if n != 1:
                raise ExtractError("script_execute: varargs block changed")
            applied.append("thread-local arguments pushed from varargs removed (see nargs above)")
        if name in CGB_COUNTS:
            text, n = CGB_BLOCK.subn(r"\2ApplyPaletteChangeColor(\1);", text)
            if n != CGB_COUNTS[name] or "_is_CGB" in text or "DMG" in text:
                raise ExtractError(f"{name}: CGB/DMG palette branches changed")
            applied.append("colour console: the CGB palette branch is kept, the dead DMG call removed; "
                           "ApplyPaletteChangeColor is the NGPC palette writer (emit.py)")
        if name == "script_runner_update":
            text = localize_runner(text)
            applied.append("executing_ctx / old_executing_ctx kept in locals and written back at both exits "
                           "(cc900 reloads globals at every access); handlers only change globals that stay "
                           "global (docs/M2_PERF_PISTES.md)")
        if name == "vm_rpn":
            text = localize_rpn(text)
            applied.append("PC and stack pointer kept in locals during the expression and written back at "
                           "its exits (cc900 reloads them through THIS at every access); same memory "
                           "reads and writes, same final state (docs/M2_PERF_PISTES.md)")
        if name not in PUBLIC:
            text = "static " + text
        text = text.strip("\n") + "\n"
        functions[name] = split_cases(text) if name == "vm_rpn" else text
        notes[name] = applied
    return {
        "reference": LABEL, "gbvm_commit": GBVM_COMMIT,
        "sources": {k: {"path": p, "sha256": hashlib.sha256(raw[k]).hexdigest()} for k, p in SOURCES.items()},
        "license": raw["license"].decode("utf-8"),
        "context": context[0], "fade_direction": direction[0], "script_event": event[0],
        "timer_time": timer[0] + "\n#define MAX_CONCURRENT_TIMERS " + timers[0],
        "defines": defines, "c_defines": c_defines,
        "functions": functions, "adaptations": notes,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("write", "check"))
    a = p.parse_args()
    fresh = extract()
    if a.command == "write":
        write_json(OUT, fresh)
        print(f"written {OUT}: {len(fresh['functions'])} functions")
        return 0
    same = json.loads(OUT.read_text(encoding="utf-8")) == fresh
    print("GBVM runtime up to date" if same else "GBVM runtime differs from the reference: rerun write and review")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
