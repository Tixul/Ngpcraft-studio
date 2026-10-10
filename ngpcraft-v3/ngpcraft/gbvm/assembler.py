"""GBVM assembly text (as GB Studio's scriptBuilder writes it) -> bytecode.

GB Studio assembles its scripts with SDCC's sdasgb and the macros of
include/vm.i. This is not a general assembler: it reads the exact layout
toScriptString produces and expands the macros frozen in
ngpcraft/gbvm_model.json (tools/extract_gbvm_model.py). Every other line,
directive, operator or symbol is refused.

Encoding follows vm.i and sdasgb:
- `#>P` high byte, `#<P` (or `#P` in `.db`) low byte;
- `.dw` is little-endian (sdasgb), used by the RPN operands;
- a value must fit the bytes that encode it (a byte: -128..255, a word:
  -32768..65535). No truncation.

NGPC adaptations (docs/BACKEND_GBVM.md §3):
- VM_INVOKE names a native function by bank and address: the address word
  is the index of the function in the runtime's native table, bank 0;
- an RPN memory operand (`.R_REF_MEM*`) names a C variable of the engine by
  address: it is the index of the variable in the runtime's memory table
  (TLCS-900 pointers are 4 bytes and link addresses are unknown here);
- a local label (`1$:`, sdas syntax) is a 16-bit address in the script's
  bank, as on the Game Boy: here a bank is one C array of at most 64 KB
  filled by the exporter, and the address is the offset in that array
  (`base` = where the script starts in it). The runtime adds the bank's
  start (emit.py ngc_addr). Labels are resolved in two passes; a script whose
  size would depend on a label value is refused;
- a scene is named by a far pointer (`IMPORT_FAR_PTR_DATA _scene`, macro.i
  FAR_PTR_DATA: `.db` bank, `.dw` address): the bank is 0 and the address
  word is the index of the scene in the export's scene table (`scenes`);
- a script called with VM_CALL_FAR (a custom script) is named by bank and
  address too: bank 0, its address in the bank. Each script also defines its
  own `___bank_<name> = 255` (the bank packer's placeholder): for a script of
  this bank the table's 0 is kept. assemble_bank resolves the addresses in
  two passes (instruction sizes do not depend on them).
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re

from ..compiler import CompileError

MODEL_PATH = Path(__file__).resolve().parents[1] / "gbvm_model.json"
# Native functions scripts may VM_INVOKE, by index (0 = none).
NATIVES = {"wait_frames": 1, "camera_shake_frames": 2}
# Engine C variables scripts may read or write through RPN, by index.
ENGINE_MEMORY = {"fade_frames_per_step": 1, "camera_settings": 2}
# Platformer variables (scene_platform.memory_names), indexes after the fixed ones.
def _platform_memory():
    from ..scene_platform import memory_names
    return {name: 3 + i for i, name in enumerate(memory_names())}
ENGINE_MEMORY.update(_platform_memory())
# VM_RANDOMIZE's two reads (vm.i), after the existing indexes (emit.MEMORY_POINTER).
ENGINE_MEMORY.update({name: len(ENGINE_MEMORY) + 1 + i for i, name in enumerate(("DIV_REG", "game_time"))})
# joypads (ifInput reads `_joypads + 1`, joypads.joy0): two indexes reserved,
# the next free one after it is joypads + 2.
ENGINE_MEMORY["joypads"] = max(ENGINE_MEMORY.values()) + 1
# ifDeviceCGB: _getMemUInt8(ref, "_is_CGB") writes `__is_CGB` (the name keeps its _).
ENGINE_MEMORY["_is_CGB"] = max(ENGINE_MEMORY.values()) + 2
# Shoot'em up engine field (scene_genres.memory_names), after the existing indexes.
def _genre_memory():
    from ..scene_genres import memory_names
    return {name: max(ENGINE_MEMORY.values()) + 1 + i for i, name in enumerate(memory_names())}
ENGINE_MEMORY.update(_genre_memory())
# ui.c text settings (textSetAnimSpeed: _setConstMemInt8), after the existing indexes.
TEXT_MEMORY = ("text_ff_joypad", "text_draw_speed", "text_out_speed", "text_in_speed")
ENGINE_MEMORY.update({name: max(ENGINE_MEMORY.values()) + 1 + i for i, name in enumerate(TEXT_MEMORY)})
# ifCurrentSceneIs reads _current_scene (bank) and _current_scene + 1 (address):
# two indexes reserved, as for joypads.
ENGINE_MEMORY["current_scene"] = max(ENGINE_MEMORY.values()) + 1
# ifDeviceSGB / ifDeviceGBA read _is_SGB / _is_GBA like _is_CGB (after current_scene + 1).
ENGINE_MEMORY["_is_SGB"] = max(ENGINE_MEMORY.values()) + 2
ENGINE_MEMORY["_is_GBA"] = max(ENGINE_MEMORY.values()) + 1
# sceneUpdatePause / sceneUpdateResume: _setConstMemInt8("pause_state_update").
ENGINE_MEMORY["pause_state_update"] = max(ENGINE_MEMORY.values()) + 1
# textCloseNonModal: _setConstMemUInt8("overlay_cut_scanline", LYC_SYNC_VALUE).
# Only a box at the top reads it (not delivered): written, never read.
UNREAD_MEMORY = ("overlay_cut_scanline",)
ENGINE_MEMORY["overlay_cut_scanline"] = max(ENGINE_MEMORY.values()) + 1
# cameraSetPropertyToScriptValue: camera.c deadzones and offsets (BYTE).
CAMERA_PROPERTIES = ("camera_deadzone_x", "camera_deadzone_y", "camera_offset_x", "camera_offset_y")
ENGINE_MEMORY.update({name: max(ENGINE_MEMORY.values()) + 1 + i for i, name in enumerate(CAMERA_PROPERTIES)})
# textDraw saves, clears and restores ui.c overlay_priority (a text setting).
TEXT_MEMORY += ("overlay_priority",)
ENGINE_MEMORY["overlay_priority"] = max(ENGINE_MEMORY.values()) + 1
# cameraSetBoundsToScriptValues: scroll.c bounds (UINT16, background_stream.py).
SCROLL_BOUNDS = ("scroll_x_min", "scroll_y_min", "scroll_x_max", "scroll_y_max")
ENGINE_MEMORY.update({name: max(ENGINE_MEMORY.values()) + 1 + i for i, name in enumerate(SCROLL_BOUNDS)})
# Translated engine plugins (translated.py): their fields and script-written variables,
# after every core index (a project without them uses none and emits none).
def _translated_memory():
    from ..translated import script_variables
    names = [n for n in script_variables() if n not in ENGINE_MEMORY]
    return {name: max(ENGINE_MEMORY.values()) + 1 + i for i, name in enumerate(names)}
ENGINE_MEMORY.update(_translated_memory())
# RPN pseudo-macros (vm.i) -> the vm_rpn case they need (vm.h VM_OP_*).
RPN_MACROS = {".R_INT8": "VM_OP_INT8", ".R_INT16": "VM_OP_INT16", ".R_REF": "VM_OP_REF",
              ".R_REF_IND": "VM_OP_REF_IND", ".R_REF_SET": "VM_OP_REF_SET",
              ".R_REF_SET_IND": "VM_OP_REF_SET_IND", ".R_REF_MEM": "VM_OP_REF_MEM",
              ".R_REF_MEM_SET": "VM_OP_REF_MEM_SET", ".R_REF_MEM_IND": "VM_OP_REF_MEM_IND"}


def load_model():
    return json.loads(MODEL_PATH.read_text(encoding="utf-8"))


class _Expr:
    TOKEN = re.compile(r"\s*(\^/\(|\)/|0x[0-9A-Fa-f]+|\d+\$|\d+|'[ -~]'|<<|>>|[.A-Za-z_][A-Za-z0-9_.]*|[-+()|&^*/%~])")
    # Binary operators, lowest precedence first (C order).
    LEVELS = (("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*", "/", "%"))

    def __init__(self, text, symbols, where, labels=None):
        self.symbols, self.where, self.tokens, pos = symbols, where, [], 0
        self.labels = labels
        text = text.strip()
        while pos < len(text):
            m = self.TOKEN.match(text, pos)
            if not m:
                raise CompileError(f"{where}: unsupported expression {text!r}")
            self.tokens.append(m[1])
            pos = m.end()
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, expected=None):
        tok = self.peek()
        if tok is None or (expected and tok != expected):
            raise CompileError(f"{self.where}: expected {expected or 'a value'}, got {tok!r}")
        self.i += 1
        return tok

    def value(self):
        result = self.binary(0)
        if self.peek() is not None:
            raise CompileError(f"{self.where}: unexpected {self.peek()!r}")
        return result

    def binary(self, level):
        if level == len(self.LEVELS):
            return self.unary()
        result = self.binary(level + 1)
        while self.peek() in self.LEVELS[level]:
            op, right = self.take(), None
            right = self.binary(level + 1)
            if op in ("/", "%") and right == 0:
                raise CompileError(f"{self.where}: division by zero")
            # Only the operator applied is computed: `x | 0` must not divide by zero.
            result = {"|": lambda a, b: a | b, "^": lambda a, b: a ^ b, "&": lambda a, b: a & b,
                      "<<": lambda a, b: a << b, ">>": lambda a, b: a >> b, "+": lambda a, b: a + b,
                      "-": lambda a, b: a - b, "*": lambda a, b: a * b,
                      "/": lambda a, b: int(a / b), "%": lambda a, b: a - b * int(a / b)}[op](result, right)
        return result

    def unary(self):
        tok = self.take()
        if tok == "-":
            return -self.unary()
        if tok == "~":
            return ~self.unary()
        if tok == "(":
            result = self.binary(0)
            self.take(")")
            return result
        if tok == "^/(":
            result = self.binary(0)
            self.take(")/")
            return result
        if tok.endswith("$"):
            if self.labels is None:
                raise CompileError(f"{self.where}: label {tok} not allowed here")
            if tok not in self.labels:
                if not self.labels.get(None):      # first pass: forward reference
                    return 0
                raise CompileError(f"{self.where}: undefined label {tok}")
            return self.labels[tok]
        if self.labels is not None and tok in self.labels:
            return self.labels[tok]                 # a data table label (bank address)
        if re.fullmatch(r"0x[0-9A-Fa-f]+", tok):
            return int(tok, 16)
        if tok.isdigit():
            return int(tok)
        if re.fullmatch(r"'[ -~]'", tok):
            return ord(tok[1])
        if tok in self.symbols:
            return self.symbols[tok]
        hint = (" (a variable named only in a GBVM Script event must be listed in its references, "
                "as in GB Studio)") if tok.startswith("VAR_") else ""
        raise CompileError(f"{self.where}: unknown symbol {tok!r}{hint}")


def _split_args(text):
    return [a.strip() for a in text.split(",")] if text.strip() else []


def _asciz(text, where):
    """`.asciz "..."` as GB Studio writes it (helpers encodeString): printable
    ASCII, other bytes as a 3-digit octal escape, then a zero. One more:
    scriptBuilderBase.ts _getAvatarCode writes a literal \\n between the two
    avatar rows, which sdas reads as 0x0A (asxxsrc/aslex.c getmap). sdas
    knows other escapes (\\t, \\r...) that GB Studio never writes on purpose
    (a backslash typed in a text): refused rather than guessed."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and text[i + 1:i + 2] == "n":
            out.append(0x0A)
            i += 2
            continue
        if c == "\\":
            m = re.match(r"[0-7]{3}", text[i + 1:i + 4])
            if not m:
                raise CompileError(f"{where}: .asciz escape {text[i:i + 4]!r} not delivered "
                                   "(only \\ooo, as GB Studio encodes text)")
            out.append(int(m[0], 8) & 0xFF)
            i += 4
            continue
        if not " " <= c <= "~" or c == '"':
            raise CompileError(f"{where}: .asciz character {c!r} not encoded by GB Studio")
        out.append(ord(c))
        i += 1
    return out + [0]


def _fit(value, low, high, where, what):
    if not low <= value <= high:
        raise CompileError(f"{where}: {what} = {value} does not fit in {low}..{high}")
    return value


class _Assembler:
    def __init__(self, model, symbols):
        self.model, self.symbols = model, symbols
        self.out, self.used, self.rpn = [], set(), set()
        self.debug = []             # (GBVM$ debug symbol, bank address)
        self.op_names = {v: k for k, v in model["constants"].items()}

    def evaluate(self, text, env, where):
        return _Expr(re.sub(r"\^!(\w+)!", r"\1", text), {**self.symbols, **env}, where).value()

    def macro(self, name, values, where, depth=0):
        if depth > 8:
            raise CompileError(f"{where}: macro nesting too deep")
        model = self.model
        self.used.add(name)
        if name in RPN_MACROS:
            self.rpn.add(RPN_MACROS[name])
        if name == ".R_OPERATOR":
            # Named by value: vm.i and vm.h do not share the names (.LTE is
            # VM_OP_LE, .GTE VM_OP_GE) and vm.i reuses the values for other
            # constants (1 is .EQ and .GET_WORD). emit.rpn_cases maps it.
            self.rpn.add(f"operator {values[0]}")
        if name in model["macros"]:
            spec = model["macros"][name]
            env = dict(zip(spec["params"], values))
            halves = {}
            for b in spec["bytes"][1:]:
                if "param" in b:
                    halves.setdefault(b["param"], set()).add(b["part"])
            for p, parts in halves.items():
                low, high = (-32768, 65535) if "high" in parts else (-128, 255)
                _fit(env[p], low, high, where, f"{name} {p}")
            for b in spec["bytes"]:
                if "opcode" in b:
                    self.out.append(model["opcodes"][b["opcode"]])
                elif "byte" in b:
                    self.out.append(b["byte"] & 0xFF)
                else:
                    v = env[b["param"]] & 0xFFFF
                    self.out.append(v >> 8 if b["part"] == "high" else v & 0xFF)
            return
        spec = model["composite_macros"][name]
        env = dict(zip(spec["params"], values))
        active = [True]
        for line in spec["lines"]:
            here = f"{where} ({name}: {line})"
            head, _, rest = line.partition(" ")
            if head == ".if":
                active.append(active[-1] and bool(self.evaluate(rest, env, here)))
                continue
            if head == ".else":
                active[-1] = active[-2] and not active[-1]
                continue
            if head == ".endif":
                active.pop()
                continue
            if not active[-1]:
                continue
            if head in (".db", ".dw"):
                for item in _split_args(rest):
                    m = re.fullmatch(r"#([<>]?)(.+)", item)
                    part, expr = (m[1], m[2]) if m else ("", item)
                    value = self.evaluate(expr, env, here)
                    if head == ".dw":
                        if part:
                            raise CompileError(f"{here}: unsupported .dw operand {item!r}")
                        v = _fit(value, -32768, 65535, here, item) & 0xFFFF
                        self.out += [v & 0xFF, v >> 8]          # sdasgb .dw: little-endian
                    elif part == ">":
                        self.out.append((_fit(value, -32768, 65535, here, item) >> 8) & 0xFF)
                    else:
                        self.out.append(_fit(value, -128, 255, here, item) & 0xFF)
                continue
            if head in model["macros"] or head in model["composite_macros"]:
                self.macro(head, [self.evaluate(a, env, here) for a in _split_args(rest)], here, depth + 1)
                continue
            raise CompileError(f"{here}: macro line not delivered")
        if len(active) != 1:
            raise CompileError(f"{where}: unbalanced .if in {name}")


def _pass(text, variables, model, labels, base, scenes=None, scripts=None, sprites=None, constants=None):
    symbols = dict(model["constants"])
    # vm.i OP_VM_* equates: VM_SIO_EXCHANGE, a composite (#<(SIZE << 1)), names its opcode.
    symbols.update(model["opcodes"])
    # engine.json consts (PLATFORM_*_STATE...), as GB Studio's generated includes define them.
    from ..scene_platform import runtime as _platform_runtime
    symbols.update(_platform_runtime()["consts"])
    from ..scene_genres import runtime as _genre_runtime
    symbols.update(_genre_runtime("adventure")["consts"])      # ADVENTURE_*_STATE, ADVENTURE_*_INIT/END
    symbols.update(variables)
    # generateGBVMData.ts: includes entity locals and T0/T1, excludes stacks.
    symbols["MAX_GLOBAL_VARS"] = len(variables)
    # game_globals.i constants other than variables (project constants, font
    # symbols: ui_fonts index); one name, one value.
    clashes = sorted(set(constants or {}) & set(symbols))
    if clashes:
        raise CompileError(f"constant symbols {', '.join(clashes)} are already defined (variables, engine constants)")
    symbols.update(constants or {})
    from .platform_callbacks import CALL_NATIVES, CONSTANTS
    symbols.update(CONSTANTS)
    for name, index in {**NATIVES, **CALL_NATIVES}.items():
        symbols[f"_{name}"] = index
        symbols[f"b_{name}"] = 0
    for name, index in ENGINE_MEMORY.items():
        symbols[f"_{name}"] = index
    for name, index in (sprites or {}).items():
        symbols[f"_{name}"] = index
        symbols[f"___bank_{name}"] = 0
    for name, index in (scenes or {}).items():
        symbols[f"_{name}"] = index
        symbols[f"___bank_{name}"] = 0
    banked = set()
    for name, address in (scripts or {}).items():
        symbols[f"_{name}"] = address
        symbols[f"___bank_{name}"] = 0
        banked.add(f"___bank_{name}")
    name, started, found = None, False, {}
    asm = _Assembler(model, symbols)
    for number, line in enumerate(text.split("\n"), 1):
        where = f"line {number}"
        # Text data (scriptBuilderBase _string, _dw), read before the comment
        # split: a ';' may be part of the text.
        m = re.fullmatch(r'        \.asciz "(.*)"', line.rstrip()) if started else None
        if m:
            asm.out += _asciz(m[1], where)
            continue
        code = line.split(";", 1)[0].rstrip()
        # Indentation is free, as for sdasgb (EVENT_GBVM_SCRIPT lines come as typed).
        m = re.fullmatch(r"\s+\.dw\s+(.+)", code) if started else None
        if m:
            for item in _split_args(m[1]):
                v = _fit(_Expr(item, symbols, f"{where}: .dw", labels).value(), -32768, 65535, where, item) & 0xFFFF
                asm.out += [v & 0xFF, v >> 8]                    # sdasgb .dw: little-endian
            continue
        if not code.strip():
            continue
        if not started:
            m = re.fullmatch(r"\.module (\w+)", code)
            if m:
                name = m[1]
                continue
            if re.fullmatch(r'\.include "[\w./]+"|\.globl [\w, ]+|\.area _CODE_255', code):
                continue
            m = re.fullmatch(r"(\.LOCAL_\w+|\.SCRIPT_ARG_\w+|___bank_\w+) = (-?\d+)", code)
            if m and m[1] in banked:
                continue                       # this bank's script: bank 0 (see above)
            if m:
                if m[1] in symbols:
                    raise CompileError(f"{where}: {m[1]} defined twice")
                symbols[m[1]] = int(m[2])
                continue
            if name and code == f"_{name}::":
                started = True
                continue
            raise CompileError(f"{where}: unsupported line {code!r}")
        # scriptBuilderBase addDebugSymbol / addDebugEndSymbol (debug builds,
        # docs/M7_DEBUG.md): `GBVM$... = .` and its `.globl`, no byte.
        m = re.fullmatch(r"(GBVM(?:_END)?\$[\w$]+) = \.", code)
        if m:
            asm.debug.append((m[1], base + len(asm.out)))
            continue
        if re.fullmatch(r"\.globl GBVM(?:_END)?\$[\w$]+", code):
            continue
        # A local label, or a data table label (scriptBuilderBase toScriptString
        # writes `symbol:` after the script, then its `.dw` rows).
        m = re.fullmatch(r"\s*(\d+\$):", code) or re.fullmatch(r"([A-Za-z_]\w*):", code)
        if m:
            if m[1] in symbols:
                raise CompileError(f"{where}: label {m[1]} is already a symbol")
            if m[1] in found:
                raise CompileError(f"{where}: label {m[1]} defined twice")
            found[m[1]] = _fit(base + len(asm.out), 0, 0xFFFF, where, f"label {m[1]}")
            continue
        # macro.i IMPORT_FAR_PTR_DATA (scriptBuilderBase._importFarPtrData pads
        # "    IMPORT_FAR_PTR_DATA"): FAR_PTR_DATA = .db ___bank'SYM, .dw SYM.
        m = re.fullmatch(r"\s+IMPORT_FAR_PTR_DATA\s+(_\w+)", code)
        if m:
            sym = m[1]
            if sym[1:] not in (scenes or {}):
                raise CompileError(f"{where}: far pointer {sym}: not a scene of this export")
            asm.used.add("IMPORT_FAR_PTR_DATA")
            asm.out.append(symbols[f"___bank{sym}"])
            value = _fit(symbols[sym], 0, 0xFFFF, where, sym)
            asm.out += [value & 0xFF, value >> 8]                # .dw: little-endian
            continue
        # An instruction (8 spaces), or an RPN operand line after VM_RPN (12
        # spaces, ScriptBuilderBase._rpn: .R_REF, .R_OPERATOR, .R_STOP...).
        # .MENUITEM data after VM_CHOICE (12 spaces: _menuItem pads "    .MENUITEM"),
        # .SAVE_SLOT after VM_RAISE EXCEPTION_SAVE/LOAD (_saveSlot, same padding).
        # .CGB_PAL after VM_LOAD_PALETTE (_paletteColor: _addCmd, 8 spaces).
        m = re.fullmatch(r"\s+([A-Z][A-Z0-9_]*|\.CGB_PAL|\.R_[A-Z0-9_]+|\.MENUITEM|\.SAVE_SLOT)(?:\s+(.*))?", code)
        if not m:
            raise CompileError(f"{where}: unsupported line {code!r}")
        macro, args = m[1], _split_args(m[2] or "")
        if macro not in model["macros"] and macro not in model["composite_macros"]:
            raise CompileError(f"{where}: unknown macro {macro}")
        params = (model["macros"].get(macro) or model["composite_macros"][macro])["params"]
        if len(args) != len(params):
            raise CompileError(f"{where}: {macro} takes {len(params)} arguments, got {len(args)}")
        asm.macro(macro, [_Expr(a, symbols, f"{where}: {macro} {p}", labels).value() for p, a in zip(params, args)],
                  f"{where}: {macro}")
    if not started:
        raise CompileError("script entry label not found")
    return name, asm, found


def assemble(text, variables, model=None, base=0, scenes=None, scripts=None, sprites=None, constants=None,
             debug=None):
    """Script text -> (symbol, bytes, macros used, RPN cases used).
    `variables`: VAR_* symbol -> index. `base`: offset of the script in its
    bank (local labels are bank addresses)."""
    if type(base) is not int or not 0 <= base <= 0xFFFF:
        raise CompileError(f"script base {base!r}: expected an integer address in 0..65535")
    model = model or load_model()
    # Data table labels follow the code that reads them: 0 in the first pass.
    named = {n: 0 for n in re.findall(r"^([A-Za-z_]\w*):\r?$", text, re.M)}
    name, first, labels = _pass(text, variables, model, {**named, None: False}, base, scenes, scripts, sprites,
                                constants)
    if base + len(first.out) > 0x10000:
        raise CompileError(f"script {name}: {len(first.out)} bytes at {base} exceed the 64 KB bank")
    name, asm, final = _pass(text, variables, model, {**labels, None: True}, base, scenes, scripts, sprites, constants)
    if final != labels:
        raise CompileError("script size depends on a label value (not supported)")
    if debug is not None:
        debug.extend(asm.debug)
    return name, bytes(asm.out), asm.used, asm.rpn


@dataclass(frozen=True)
class BankScript:
    symbol: str
    source: str
    address: int
    data: bytes
    debug: tuple = ()           # (GBVM$ debug symbol, bank address), debug builds only


def assemble_bank(texts, variables, model=None, scenes=None, sprites=None, constants=None):
    """Pack reachable scripts in caller order in bank 0, without padding.

    Each script has its own local labels, relocated to its bank address.
    Return (bank bytes, BankScript entries, used macros, used RPN cases).
    Cross-script calls and splitting across multiple banks are not delivered.
    """
    model = model or load_model()
    # Scripts of this bank, for VM_CALL_FAR: placeholders first (sizes), then
    # the addresses found by the first layout.
    names = []
    for index, text in enumerate(texts):
        m = re.search(r"^\.module (\w+)$", text, re.M)
        if not m:
            raise CompileError(f"bank 0 script[{index}]: no .module line")
        names.append(m[1])
    called = {n: 0 for n in names}
    for _ in range(2):
        offset, layout = 0, {}
        for index, text in enumerate(texts):
            layout[names[index]] = offset
            try:
                _, code, _, _ = assemble(text, variables, model, base=offset, scenes=scenes, scripts=called, sprites=sprites,
                                         constants=constants)
            except CompileError as exc:
                raise CompileError(f"bank 0 script[{index}]: {exc}") from None
            offset += len(code)
        called = layout
    data, scripts, symbols = bytearray(), [], set()
    macros, rpn = set(), set()
    for index, text in enumerate(texts):
        debug = []
        try:
            name, code, used, cases = assemble(text, variables, model, base=len(data), scenes=scenes,
                                               scripts=called, sprites=sprites, constants=constants, debug=debug)
        except CompileError as exc:
            raise CompileError(f"bank 0 script[{index}]: {exc}") from None
        if name in symbols:
            raise CompileError(f"bank 0 script[{index}]: duplicate script symbol {name}")
        if not code:
            raise CompileError(f"bank 0 script[{index}]: empty script {name}")
        symbols.add(name)
        scripts.append(BankScript(name, text, len(data), code, tuple(debug)))
        data.extend(code)
        macros.update(used)
        rpn.update(cases)
    if not scripts:
        raise CompileError("bank 0: no scripts to assemble")
    return bytes(data), tuple(scripts), macros, rpn
