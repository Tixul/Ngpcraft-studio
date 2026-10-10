"""Bound per-thread storage on the assembled CFG, independently of scheduling.

Every opcode must be explicitly classified before it can shrink storage.
The decoder uses C locals, so its argument bytes need no VM stack scratch.

Far calls are followed into the called script of bank 0 (vm.c vm_call_far
pushes PC and bank: two words; vm_ret_far pops them, then N arguments).
Indirect references read or write slots that already exist (vm.c
vm_set_indirect, vm_get_indirect, vm_push_value_ind, vm_rpn REF_IND...).
Text (vm_ui.c vm_load_text) only reads its arguments; its inline data is
NARGS words then a zero-terminated string (load_text stops at the first 0).
Recursion, other banks and unbalanced returns keep the vendored capacity.
"""
from ..compiler import CompileError


# Audited handlers do not change stack_ptr (emit.py, emit_ui.py, vendored
# vm.c, vm_actor.c, vm_scene.c, vm_ui.c).
NEUTRAL = frozenset((
    "VM_SFX_PLAY", "VM_MUSIC_PLAY", "VM_MUSIC_STOP", "VM_PROJECTILE_LAUNCH", "VM_ACTOR_GET_ANGLE",
    # vm_sio.c (gbvm/link.py): references only; the exchange rewinds onto itself.
    "VM_SIO_SET_MODE", "VM_SIO_EXCHANGE", "VM_MUSIC_MUTE", "VM_MUSIC_ROUTINE", "VM_SET_TEXT_SOUND",
    "VM_LOCK", "VM_UNLOCK", "VM_IDLE", "VM_SET_CONST", "VM_MEMSET", "VM_RAND", "VM_INIT_RNG", "VM_SET", "VM_GET_TLOCAL",
    "VM_SET_SPRITE_MODE", "VM_FADE", "VM_ACTOR_ACTIVATE", "VM_ACTOR_DEACTIVATE", "VM_ACTOR_BEGIN_UPDATE", "VM_SET_SPRITE_VISIBLE", "VM_ACTOR_SET_BOUNDS", "VM_PROJECTILE_LOAD_TYPE",
    "VM_ACTOR_MOVE_TO", "VM_ACTOR_GET_DIR", "VM_ACTOR_GET_POS", "VM_ACTOR_SET_POS", "VM_ACTOR_SET_DIR", "VM_ACTOR_SET_ANIM_FRAME",
    "VM_ACTOR_SET_MOVE_SPEED", "VM_ACTOR_SET_ANIM_TICK", "VM_ACTOR_SET_FLAGS",
    "VM_ACTOR_MOVE_TO_INIT", "VM_ACTOR_MOVE_TO_X", "VM_ACTOR_MOVE_TO_Y",
    "VM_ACTOR_MOVE_TO_XY", "VM_ACTOR_MOVE_TO_SET_DIR_X", "VM_ACTOR_MOVE_TO_SET_DIR_Y",
    "VM_ACTOR_SET_ANIM_MOVING", "VM_ACTOR_MOVE_CANCEL", "VM_ACTOR_TERMINATE_UPDATE", "VM_ACTOR_SET_ANIM_SET", "VM_ACTOR_SET_SPRITESHEET", "VM_ACTOR_EMOTE", "VM_CONTEXT_PREPARE",
    "VM_INPUT_ATTACH", "VM_INPUT_DETACH", "VM_INPUT_WAIT",
    "VM_TIMER_PREPARE", "VM_TIMER_SET", "VM_TIMER_STOP", "VM_TIMER_RESET", "VM_SCENE_PUSH", "VM_SCENE_STACK_RESET",
    "VM_SCENE_POP", "VM_SCENE_POP_ALL", "VM_SET_INDIRECT", "VM_GET_INDIRECT",
    # vm_ui.c: VM_OVERLAY_WAIT may rewind PC onto itself, at the same height.
    "VM_DISPLAY_TEXT_EX", "VM_SWITCH_TEXT_LAYER", "VM_OVERLAY_WAIT",
    "VM_OVERLAY_MOVE_TO", "VM_OVERLAY_SET_SCROLL", "VM_OVERLAY_CLEAR", "VM_OVERLAY_SETPOS", "VM_SET_FONT",
    # vm_ui.c vm_overlay_show (clear + ui_set_pos); emit.py vm_replace_tile_xy
    # (reads its variable by reference).
    "VM_OVERLAY_SHOW", "VM_REPLACE_TILE_XY",
    # vm_gameboy.c vm_replace_tile (emit_ui.py): reads two references.
    "VM_REPLACE_TILE",
    # vm.c vm_poll_loaded and vm_load_save.c vm_save_peek (emit.py) write their
    # results through references (VM_REF_TO_PTR), stack_ptr unchanged.
    "VM_POLL_LOADED", "VM_SAVE_PEEK",
    # vm_load_save.c vm_save_clear (emit.py): no stack access.
    "VM_SAVE_CLEAR",
    # vm_camera.c: both read their position by reference (VM_REF_TO_PTR);
    # vm_camera_move_to rewinds PC onto itself, at the same height.
    "VM_CAMERA_SET_POS", "VM_CAMERA_MOVE_TO",
    # vm.c vm_terminate reads the handle by reference; vm_beginthread writes it
    # by reference and copies NARGS words to the NEW context (see below).
    "VM_TERMINATE",
))
PUSH = frozenset(("VM_PUSH_CONST", "VM_PUSH_VALUE", "VM_PUSH_REFERENCE", "VM_PUSH_VALUE_IND"))
RET_FAR = frozenset(("VM_RET_FAR", "VM_RET_FAR_N"))   # one opcode, N byte (0 for VM_RET_FAR)
BINARY = frozenset("EQ LT LE GT GE NE AND OR ADD SUB MUL DIV MOD B_AND B_OR B_XOR SHL SHR MIN MAX ATAN2".split())
UNARY = frozenset(("NOT", "B_NOT", "NEG"))
# vm_rpn operands after the operator byte, and the expression words they add.
RPN_OPERANDS = {"INT8": (1, 1), "INT16": (2, 1), "REF": (2, 1), "REF_IND": (2, 1),
                "REF_MEM": (3, 1), "REF_MEM_IND": (3, 1),
                "REF_SET": (2, -1), "REF_SET_IND": (2, -1), "REF_MEM_SET": (3, -1)}
CALL_FRAME = 2      # vm_call_far: PC, bank


class _Fallback(Exception):
    pass


def stack_budget(scripts, model, runtime, contexts=None, entry_arguments=None):
    """Return one safe stride for all contexts, with reasons per reachable script.

Only equal-height joins are accepted; a growing loop or unsupported instruction
retains 64 words. Positive bounds above that capacity are rejected explicitly.
Each assembled bank script is an entry (including input scripts); a far call
adds the return frame and the called script's own bound at the call height.
"""
    default = runtime["defines"]["VM_CONTEXT_STACK_SIZE"]
    specs = {}
    for name, spec in model["macros"].items():
        opcode = model["opcodes"][spec["bytes"][0]["opcode"]]
        specs[opcode] = (name, spec)
    # A composite that is one raw instruction (VM_SIO_EXCHANGE: .db OP_..., #<(SIZE << 1), ...):
    # its length is the count of .db items; no operand is read here.
    for name, spec in model["composite_macros"].items():
        first = spec["lines"][0] if spec["lines"] else ""
        if first.startswith(".db OP_") and len(spec["lines"]) == 1:
            items = [i.strip() for i in first[4:].split(",")]
            specs[model["opcodes"][items[0]]] = (name, {"bytes": [{"opcode": items[0]}] +
                                                        [{"byte": 0}] * (len(items) - 1)})
    rpn_ops = {int(v) & 255: k.removeprefix("VM_OP_")
               for k, v in runtime["c_defines"].items()
               if k.startswith("VM_OP_") and not k.startswith("VM_OP_MEM_")}
    analysis = _Analysis(scripts, specs, rpn_ops)
    reports = {}
    for script in scripts:
        initial = (entry_arguments or {}).get(script.symbol, 0)
        analysis.active = [script.symbol]
        try:
            words, _ = analysis.bound(script, initial)
            if words > default:
                raise CompileError(f"script {script.symbol}: {words} VM stack words required; "
                                   f"supported capacity is {default}")
            reports[script.symbol] = {"words": max(1, words), "entry_words": initial, "method": "bytecode_cfg"}
            called = sorted(analysis.calls.get(script.symbol, ()))
            if called:
                reports[script.symbol]["far_calls"] = called
        except _Fallback as exc:
            reports[script.symbol] = {"words": default, "method": "vendored_capacity", "reason": str(exc)}
    stride = max((r["words"] for r in reports.values()), default=default)
    count = runtime["defines"]["VM_MAX_CONTEXTS"] if contexts is None else contexts
    return {"words_per_context": stride, "contexts": count, "stack_bytes": 2 * count * stride,
            "saved_bytes": 2 * count * (default - stride),
            "default_words_per_context": default, "scripts": reports}


class _Analysis:
    def __init__(self, scripts, specs, rpn_ops):
        self.by_address = {s.address: s for s in scripts}
        self.specs, self.rpn_ops = specs, rpn_ops
        self.functions = {}       # symbol -> (peak, arguments popped or None: never returns)
        self.active = []
        self.calls = {}           # symbol -> symbols called directly or not

    def function(self, address):
        """Bound of a called script, relative to the height after its return frame."""
        callee = self.by_address.get(address)
        if callee is None:
            raise _Fallback(f"far call to {address:#06x} is not the start of a bank script")
        if callee.symbol in self.active:
            raise _Fallback(f"recursive far call to {callee.symbol}")
        if callee.symbol not in self.functions:
            self.active.append(callee.symbol)
            try:
                self.functions[callee.symbol] = self.bound(callee, 0)
            except _Fallback as exc:
                raise _Fallback(f"called script {callee.symbol}: {exc}") from None
            finally:
                self.active.pop()
        for caller in self.active:
            self.calls.setdefault(caller, set()).update({callee.symbol, *self.calls.get(callee.symbol, ())})
        return callee.symbol, self.functions[callee.symbol]

    def bound(self, script, initial):
        """(peak words, words popped by its VM_RET_FAR_N or None if it never returns)."""
        data, base = script.data, script.address
        specs, rpn_ops = self.specs, self.rpn_ops
        pending, heights, spans = [(0, initial)], {}, {}
        peak, pops = initial, set()
        while pending:
            pc, height = pending.pop()
            if not 0 <= pc < len(data):
                raise _Fallback("control flow leaves script")
            if pc in heights:
                if heights[pc] != height:
                    raise _Fallback("unequal stack heights at control-flow join")
                continue
            if any(start < pc < end for start, end in spans.items()):
                raise _Fallback("jump into instruction data")
            heights[pc] = height
            start = pc
            if data[pc] not in specs:
                raise _Fallback("unclassified opcode")
            name, spec = specs[data[pc]]
            end = pc + len(spec["bytes"])
            if end > len(data):
                raise _Fallback("truncated instruction")
            args = {}
            for offset, field in enumerate(spec["bytes"][1:], 1):
                if "param" in field:
                    key = field["param"]
                    args[key] = args.get(key, 0) | (data[pc + offset] << (8 if field["part"] == "high" else 0))
            pc = end
            destinations = None
            if name == "VM_STOP":
                destinations = []
            elif name in NEUTRAL:
                pass
            elif name in PUSH:
                height += 1
            elif name == "VM_POP":
                height -= args["N"]
            elif name == "VM_RESERVE":
                height += args["N"] if args["N"] < 128 else args["N"] - 256
            elif name == "VM_CALL_NATIVE":
                if args["BANK"] != 0 or args["PTR"] not in (3, 4):
                    raise _Fallback("unclassified synchronous native")
                if height < (3 if args["PTR"] == 3 else 1):
                    raise _Fallback("synchronous native argument underflow")
            elif name == "VM_INVOKE":
                # wait_frames stores its persistent countdown at *stack_ptr,
                # outside the reserved locals/params, without advancing that pointer.
                if args["BANK"] != 0 or args["ADDR"] not in (1, 2):
                    raise _Fallback("unclassified native")
                peak = max(peak, height + 1)
                height -= args["N"]
            elif name == "VM_JUMP":
                destinations = [args["LABEL"] - base]
            elif name == "VM_LOOP":
                # vm.c vm_loop: jumps to LABEL while the counter is not zero, else
                # pops N and continues (the builder always uses N = 0).
                if args["N"]:
                    raise _Fallback("VM_LOOP popping on exit")
                destinations = [pc, args["LABEL"] - base]
            elif name == "VM_RATE_LIMIT_CONST":
                # vm.c vm_rate_limit_const: jumps to LABEL while limited, else continues.
                destinations = [pc, args["LABEL"] - base]
            elif name in ("VM_IF_CONST", "VM_IF"):
                # vm.c vm_if / vm_if_const: compare, jump to LABEL or continue, pop N.
                height -= args["N"]
                destinations = [pc, args["LABEL"] - base]
            elif name == "VM_SWITCH":
                # vm.c vm_switch: pops N, then SIZE cases of two .dw words
                # (value, label) at PC; no match continues after the table.
                height -= args["N"]
                table, pc = pc, pc + 4 * args["SIZE"]
                if pc > len(data):
                    raise _Fallback("truncated switch table")
                destinations = [pc] + [(data[table + 4 * i + 2] | (data[table + 4 * i + 3] << 8)) - base
                                       for i in range(args["SIZE"])]
            elif name == "VM_BEGINTHREAD":
                # vm.c vm_beginthread: NARGS .dw operands follow, copied to the
                # new context's stack (read only here), so this height is unchanged.
                pc += 2 * args["NARGS"]
                if pc > len(data):
                    raise _Fallback("truncated thread arguments")
            elif name == "VM_RAISE":
                # vm_raise records the SIZE parameter bytes that follow and skips them.
                pc += args["SIZE"]
                if pc > len(data):
                    raise _Fallback("truncated exception parameters")
            elif name == "VM_CHOICE":
                # vm_choice: the result is written by reference (an existing cell);
                # COUNT .MENUITEM records of 6 bytes follow in line (THIS->PC += ...).
                pc += 6 * args["COUNT"]
            elif name == "VM_LOAD_PALETTE":
                # vm_palette.c vm_load_palette: one palette_entry_t (four words)
                # per set MASK bit follows in line; stack_ptr unchanged.
                pc += 8 * bin(args["MASK"] & 0xFF).count("1")
                if pc > len(data):
                    raise _Fallback("truncated palette data")
            elif name == "VM_LOAD_TEXT":
                # NARGS variable references, then the text up to its first zero byte.
                text = pc + 2 * args["NARGS"]
                if text > len(data) or 0 not in data[text:]:
                    raise _Fallback("unterminated text data")
                pc = data.index(0, text) + 1
            elif name == "VM_CALL_FAR":
                if args["BANK"] != 0:
                    raise _Fallback("far call to another bank")
                _, (callee_peak, callee_pops) = self.function(args["ADDR"])
                peak = max(peak, height + CALL_FRAME + callee_peak)
                if callee_pops is None:
                    destinations = []
                else:
                    height -= callee_pops
            elif name in RET_FAR:
                if height != initial:
                    raise _Fallback("far return at a stack height different from the entry")
                pops.add(data[start + 1])
                destinations = []
            elif name == "VM_RPN":
                # ARGS is the entry stack_ptr, not the changing expression top.
                expression = 0
                while True:
                    if pc >= len(data) or data[pc] not in rpn_ops:
                        raise _Fallback("unclassified RPN operation")
                    op = rpn_ops[data[pc]]
                    pc += 1
                    if op == "STOP":
                        break
                    if op in RPN_OPERANDS:
                        size, change = RPN_OPERANDS[op]
                        if expression + change < 0:
                            raise _Fallback("RPN expression underflow")
                        pc += size
                        expression += change
                    elif op in BINARY:
                        if expression < 2:
                            raise _Fallback("RPN binary expression underflow")
                        expression -= 1
                    elif op in UNARY:
                        if expression < 1:
                            raise _Fallback("RPN unary expression underflow")
                    else:
                        raise _Fallback(f"RPN {op} needs the vendored capacity")
                    if pc > len(data):
                        raise _Fallback("truncated RPN operand")
                    peak = max(peak, height + expression)
                height += expression
            else:
                raise _Fallback(f"{name} needs the vendored capacity")
            if height < 0:
                raise _Fallback("stack underflow")
            peak = max(peak, height)
            # Detect joins into decoded operands regardless of traversal order.
            if any(start < target < pc for target in heights):
                raise _Fallback("jump into instruction data")
            spans[start] = pc
            for target in ([pc] if destinations is None else destinations):
                pending.append((target, height))
        if len(pops) > 1:
            raise _Fallback("far returns pop different argument counts")
        return peak, (pops.pop() if pops else None)
