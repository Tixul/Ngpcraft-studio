"""Sprite palette recycling, guaranteed at compile time (docs/PALETTE_RECYCLE.md).

The K2GE shows 16 sprite palettes at a time, not 16 per scene. A scene's native
sheets may use more: two palettes share a hardware slot only when no camera
position can show both. What can be on screen where:

- an actor no script can move or pin to the screen ("static") is drawn at its
  scene position; its sheet's frames cover a known box of the world;
- everything else (the player, projectiles, sheets set by a script, actors a
  script moves, pinned actors) counts as present everywhere: its palettes get a
  slot of their own.

ngc_sprite_draw draws a frame only when its box overlaps the 160 x 152 screen,
so a frame drawn is a frame that can be seen: two static actors are on screen
together only if their world boxes are within one screen of each other. Each
drawn frame asks for its sheet's shared palettes; the slot is rewritten at the
next commit, with the sprites (scene_sprites.py).

Which actors move: every GBVM instruction that writes an actor position
(VM_ACTOR_SET_POS, VM_ACTOR_MOVE_TO*) or may pin it (VM_ACTOR_SET_FLAGS) names
the actor through a stack variable. Walking back through the straight-line code
before the instruction finds the VM_SET_CONST (or the script argument, then its
call sites) that set it. Anything else, or a native call, and every actor
counts as moving.
"""
import re

from .compiler import CompileError

SCREEN = (160, 152)

# Instructions that write an actor's position (MOVE_TO_SET_DIR only turns it:
# counted too, it is always paired with a move).
POSITION_OPS = frozenset({"VM_ACTOR_SET_POS", "VM_ACTOR_MOVE_TO", "VM_ACTOR_MOVE_TO_INIT", "VM_ACTOR_MOVE_TO_X",
                          "VM_ACTOR_MOVE_TO_Y", "VM_ACTOR_MOVE_TO_XY", "VM_ACTOR_MOVE_TO_SET_DIR_X",
                          "VM_ACTOR_MOVE_TO_SET_DIR_Y"})
PINNED = 0x01                    # .ACTOR_FLAG_PINNED
UNKNOWN = None

_INSTR = re.compile(r"^\s+(VM_[A-Z0-9_]+)\b\s*(.*)$")
_LABEL = re.compile(r"^\s*([A-Za-z0-9_$.]+)::?\s*$")
_EQU = re.compile(r"^(\.[A-Z0-9_]+)\s*=\s*(-?\d+)\s*$")
_REF_SET = re.compile(r"\.R_REF_SET(?:_IND)?\s+(.+)$")


def _operands(text):
    return [o.strip() for o in text.split(";", 1)[0].split(",")] if text.split(";", 1)[0].strip() else []


class _Module:
    """One assembled module: its lines, its stack-variable offsets, its entry symbols."""
    def __init__(self, text):
        self.lines = text.split("\n")
        self.offsets = {}
        self.entries = []
        for line in self.lines:
            code = line.split(";", 1)[0].rstrip()
            m = _EQU.match(code)
            if m:
                self.offsets[m[1]] = int(m[2])
            m = re.match(r"^(_[A-Za-z0-9_]+)::\s*$", code)
            if m:
                self.entries.append(m[1])

    def slot(self, operand):
        """Stack slot an operand names (offset), or the operand itself."""
        expr = operand.strip()
        m = re.fullmatch(r"\^/\((.+)\)/", expr)
        if m:
            expr = m[1].strip()
        m = re.fullmatch(r"(\.[A-Z0-9_]+)\s*([+-])\s*(\d+)", expr)
        if m and m[1] in self.offsets:
            return self.offsets[m[1]] + (int(m[3]) if m[2] == "+" else -int(m[3]))
        return self.offsets.get(expr, expr)


class _Analysis:
    def __init__(self, texts, constants):
        self.modules = [_Module(t) for t in texts]
        self.constants = constants
        self.memo = {}
        self.calls = {}          # entry symbol -> [(module, line index of the call)]
        for mod in self.modules:
            for n, line in enumerate(mod.lines):
                m = _INSTR.match(line.split(";", 1)[0])
                if m and m[1] == "VM_CALL_FAR":
                    ops = _operands(m[2])
                    if len(ops) == 2:
                        self.calls.setdefault(ops[1], []).append((mod, n))

    def values(self, mod, n, operand, depth=0):
        """Actor indices `operand` may hold at line n of mod: a set, or UNKNOWN."""
        operand = operand.strip()
        if re.fullmatch(r"-?\d+", operand):
            return {int(operand)}
        if depth > 16:
            return UNKNOWN
        m = re.fullmatch(r"\.SCRIPT_ARG_(\d+)_[A-Z0-9_]+", operand)
        if m and "INDIRECT" not in operand:
            return self.argument(mod, int(m[1]), depth)
        m = re.fullmatch(r"\.ARG(\d+)", operand)
        if m:
            return self.rpn_argument(mod, n, int(m[1]), depth)
        target = mod.slot(operand)
        if not isinstance(target, int):
            return UNKNOWN       # a global variable or an expression
        for k in range(n - 1, -1, -1):
            code = mod.lines[k].split(";", 1)[0].rstrip()
            if not code.strip():
                continue
            if _LABEL.match(code):
                return UNKNOWN   # another path may join here
            ref = _REF_SET.search(code)
            if ref and mod.slot(ref[1]) == target:
                return UNKNOWN
            ins = _INSTR.match(code)
            if not ins:
                continue
            op, ops = ins[1], _operands(ins[2])
            if op in ("VM_CALL_FAR", "VM_CALL", "VM_CALL_NATIVE", "VM_JUMP", "VM_RET", "VM_RET_FAR",
                      "VM_RET_FAR_N", "VM_STOP", "VM_RESERVE", "VM_PUSH_CONST", "VM_PUSH_VALUE", "VM_POP"):
                return UNKNOWN   # stack or flow change: offsets no longer line up
            if not ops or mod.slot(ops[0]) != target:
                continue
            if op == "VM_SET_CONST" and len(ops) == 2 and re.fullmatch(r"-?\d+", ops[1]):
                return {int(ops[1])}
            if op == "VM_SET" and len(ops) == 2:
                return self.values(mod, k, ops[1], depth + 1)
            if op.startswith("VM_ACTOR_") or op.startswith("VM_IF") or op in ("VM_INVOKE",):
                continue         # reads the variable
            return UNKNOWN
        return UNKNOWN

    def rpn_argument(self, mod, n, index, depth):
        """.ARG<index> at line n: the value the VM_RPN block just before pushed
        (GB Studio actorMoveRelative: actor, x, y). Only actor instructions,
        which leave the stack alone, may stand between."""
        for k in range(n - 1, -1, -1):
            code = mod.lines[k].split(";", 1)[0].rstrip()
            if not code.strip() or code.lstrip().startswith(".R_"):
                continue
            ins = _INSTR.match(code)
            if not ins:
                return UNKNOWN   # a label
            if ins[1].startswith("VM_ACTOR_"):
                continue
            if ins[1] != "VM_RPN":
                return UNKNOWN
            pushed = []
            for j in range(k + 1, len(mod.lines)):
                term = mod.lines[j].split(";", 1)[0].strip()
                if not term:
                    continue
                if term == ".R_STOP":
                    break
                lit = re.fullmatch(r"\.R_INT(?:8|16|32)\s+(-?\d+)", term)
                ref = re.fullmatch(r"\.R_REF\s+(\S+)", term)
                if lit:
                    pushed.append({int(lit[1])})
                elif ref:
                    pushed.append(self.values(mod, k, ref[1], depth + 1))
                else:
                    return UNKNOWN   # an operator: computed, not followed
            if index >= len(pushed):
                return UNKNOWN
            return pushed[-1 - index]
        return UNKNOWN

    def argument(self, mod, index, depth):
        """Values of script argument `index` of mod's (single) entry, over its call sites."""
        if len(mod.entries) != 1:
            return UNKNOWN
        key = (mod.entries[0], index)
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = UNKNOWN          # a recursive call: unknown
        sites = self.calls.get(mod.entries[0], ())
        if not sites:
            return UNKNOWN
        found = set()
        for caller, n in sites:
            pushes = []
            k = n - 1
            while k >= 0:
                code = caller.lines[k].split(";", 1)[0].rstrip()
                if not code.strip():
                    k -= 1
                    continue
                ins = _INSTR.match(code)
                if not ins or ins[1] not in ("VM_PUSH_CONST", "VM_PUSH_VALUE"):
                    break
                pushes.append((ins[1], _operands(ins[2]), k))
                k -= 1
            # pushes[0] is the last push: argument 0 (GB Studio .SCRIPT_ARG_n numbering)
            if index >= len(pushes):
                return UNKNOWN
            op, ops, at = pushes[index]
            if not ops:
                return UNKNOWN
            if op == "VM_PUSH_CONST":
                if not re.fullmatch(r"-?\d+", ops[0]):
                    return UNKNOWN
                found.add(int(ops[0]))
                continue
            # VM_PUSH_VALUE of a stack variable: the pushes before it moved the
            # stack, so only a value read before any push is followed.
            if at != min(p[2] for p in pushes) and isinstance(caller.slot(ops[0]), int):
                return UNKNOWN
            v = self.values(caller, at, ops[0], depth + 1)
            if v is UNKNOWN:
                return UNKNOWN
            found |= v
        self.memo[key] = found
        return found

    def flags_may_pin(self, mod, ops):
        """VM_ACTOR_SET_FLAGS actor, flags, mask: may the mask change PINNED?"""
        if len(ops) != 3:
            return True
        total = 0
        for term in ops[2].split("|"):
            term = term.strip()
            if re.fullmatch(r"\d+", term):
                total |= int(term)
            elif re.fullmatch(r"0x[0-9A-Fa-f]+", term):
                total |= int(term, 16)
            elif term in self.constants and isinstance(self.constants[term], int):
                total |= self.constants[term]
            else:
                return True
        return bool(total & PINNED)


def moved_actors(texts, constants):
    """(indices of the actors a reachable script may move or pin, None) or
    (None, why) when a target cannot be resolved (every actor then moves)."""
    analysis = _Analysis(texts, constants)
    moved = set()
    for mod in analysis.modules:
        for n, line in enumerate(mod.lines):
            ins = _INSTR.match(line.split(";", 1)[0])
            if not ins:
                continue
            op, ops = ins[1], _operands(ins[2])
            if op == "VM_CALL_NATIVE":
                return None, f"a native call ({line.strip()})"
            if op in POSITION_OPS or (op == "VM_ACTOR_SET_FLAGS" and analysis.flags_may_pin(mod, ops)):
                if not ops:
                    return None, line.strip()
                v = analysis.values(mod, n, ops[0])
                if v is UNKNOWN:
                    where = next((e for e in reversed(mod.entries)), "?")
                    return None, f"{op} on an actor chosen at run time ({where})"
                moved |= v
    return frozenset(moved), None


def sheet_box(sheet):
    """Inclusive box (x0, x1, y0, y1) of every frame of a sheet, relative to the actor."""
    from .scene_sprites import frame_box
    boxes = [frame_box(f) for f in sheet.frames if f]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes), max(b[3] for b in boxes))


def together(a, b, screen=SCREEN):
    """Can one camera show world boxes a and b (x0, x1, y0, y1) together?"""
    return (max(a[0], b[0]) - (screen[0] - 1) <= min(a[1], b[1]) and
            max(a[2], b[2]) - (screen[1] - 1) <= min(a[3], b[3]))


def assign(presence, slots, where, why_moving=None):
    """presence: per logical palette, None (everywhere) or world boxes.
    -> hardware slot index (0..len(slots)-1) per palette, or a located CompileError."""
    count = len(presence)
    neighbours = [set() for _ in range(count)]
    for p in range(count):
        for q in range(p + 1, count):
            a, b = presence[p], presence[q]
            if a is None or b is None or any(together(x, y) for x in a for y in b):
                neighbours[p].add(q)
                neighbours[q].add(p)
    everywhere = [p for p in range(count) if presence[p] is None]
    if len(everywhere) > len(slots):
        raise CompileError(f"{where}: {len(everywhere)} native sprite palettes are used by the player, "
                           f"projectiles, sheets set by a script or actors a script moves; "
                           f"{len(slots)} slots at most in this scene (only actors that never move "
                           f"share slots{'; every actor counts as moving: ' + why_moving if why_moving else ''}; "
                           "docs/PALETTE_RECYCLE.md)")
    slot = [None] * count
    order = set(range(count))
    while order:
        p = max(order, key=lambda q: (len({slot[n] for n in neighbours[q] if slot[n] is not None}),
                                      len(neighbours[q]), -q))
        taken = {slot[n] for n in neighbours[p] if slot[n] is not None}
        free = [s for s in range(len(slots)) if s not in taken]
        if not free:
            group = sorted({p} | {n for n in neighbours[p] if slot[n] is not None})
            box = next((b for b in presence[p] or ()), None)
            at = f"around ({box[0]}, {box[2]}) px " if box else ""
            error = CompileError(f"{where}: {at}{len(group)} native sprite palettes can be on screen together; "
                                 f"{len(slots)} slots at most in this scene (sprite palette recycling, "
                                 "docs/PALETTE_RECYCLE.md)")
            error.position = (box[0], box[2]) if box else None
            raise error
        slot[p] = free[0]
        order.discard(p)
    return tuple(slot)
