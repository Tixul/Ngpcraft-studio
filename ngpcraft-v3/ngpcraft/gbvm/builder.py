"""Port of GB Studio 4.3.2's ScriptBuilder, only what delivered events use.

Source: src/lib/compiler/scriptBuilder/scriptBuilderBase.ts and
scriptBuilder.ts (reference: tools/reference.py). Method names keep GB
Studio's (snake_case) so both can be read side by side; the output lines are
the same text, checked against GB Studio's own tests
(test/compiler/scriptBuilder.test.ts) in tests/test_gbvm_builder.py.

Anything not ported raises NotDelivered with the reason (for example actor
properties and engine fields). Value and reference arguments are supported.
"""
import math
import re

from ..compiler import CompileError
from .expressions import JS_WHITESPACE, OPERATORS, expression_rpn, expression_to_script_value


class NotDelivered(CompileError):
    pass


DMG_PALETTE_COLORS = ("E8F8E0", "B0F088", "509878", "202850")   # consts.ts DMG_PALETTE


def js_str(value):
    """JavaScript String(value) for the numbers and strings a command takes."""
    if isinstance(value, bool):
        raise NotDelivered(f"boolean command argument {value!r}")
    if isinstance(value, float):
        if value != value or value in (math.inf, -math.inf):
            raise NotDelivered(f"non-finite command argument {value!r}")
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def js_round(value):
    """Math.round: halves go towards +infinity."""
    return math.floor(value + 0.5)


# shared/lib/entities/entitiesHelpers.ts
LOCAL_VARIABLE_CODES = ("L0", "L1", "L2", "L3", "L4", "L5")


def is_variable_local(variable):
    return variable in LOCAL_VARIABLE_CODES


def is_variable_temp(variable):
    return variable in ("T0", "T1")


def to_variable_number(variable):
    return re.sub(r"[^0-9]", "", variable)


def js_parse_int_string(text):
    """String(parseInt(text || "0")) as getVariableId uses it."""
    m = re.match(r"\s*([+-]?\d+)", text or "0")
    return str(int(m[1])) if m else "NaN"


def get_variable_id(variable, entity):
    """scriptBuilder/helpers.ts getVariableId."""
    if is_variable_local(variable):
        if entity:
            return f"{entity['id']}__{variable}"
    elif is_variable_temp(variable):
        return variable
    return js_parse_int_string(variable)


def to_asm_var(symbol):
    return re.sub(r"[^A-Z0-9]", "_", symbol.upper())


def to_valid_symbol(input_symbol):
    """shared/lib/helpers/symbols.ts toValidSymbol."""
    symbol = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", str(input_symbol or "symbol").lower()))[:27]
    return f"_{symbol}" if re.match(r"\d", symbol) else symbol


def to_valid_label(label):
    """scriptBuilder/helpers.ts toValidLabel."""
    return re.sub(r"[^A-Za-z0-9]", "_", label)


# shared/lib/scriptValue/types.ts
VALUE_OPERATOR_TYPES = ("add", "sub", "mul", "div", "mod", "min", "max", "eq", "ne", "gt", "gte", "lt", "lte",
                        "and", "or", "atan2", "shl", "shr", "bAND", "bOR", "bXOR")
VALUE_UNARY_OPERATOR_TYPES = ("rnd", "not", "isqrt", "abs", "neg", "bNOT")


def _js_int32(value):
    """ECMAScript ToInt32, for the bitwise operators."""
    if value != value or value in (math.inf, -math.inf):
        return 0
    value = int(math.trunc(value)) & 0xFFFFFFFF
    return value - 2**32 if value >= 2**31 else value


def _js_truthy(value):
    return value == value and value != 0


def _js_floor(value, where):
    if value != value or value in (math.inf, -math.inf):
        # GB Studio would write NaN or Infinity into the script, which its
        # assembler rejects: refused here, with the reason.
        raise CompileError(f"constant value {where} is not a finite number")
    return math.floor(value)


def _fold(op, a, b):
    """optimiseScriptValue constant folding (JavaScript number semantics)."""
    if op in ("add", "sub", "mul", "div", "mod"):
        if op in ("div", "mod") and b == 0:
            _js_floor(math.nan, f"{op} by zero")
        value = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b,
                 "div": lambda: a / b, "mod": lambda: math.fmod(a, b)}[op]()
        return _js_floor(value, op)
    if op in ("gt", "gte", "lt", "lte", "eq", "ne"):
        return int({"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b, "eq": a == b, "ne": a != b}[op])
    if op == "min":
        return min(a, b)
    if op == "max":
        return max(a, b)
    if op == "and":
        return b if _js_truthy(a) else a
    if op == "or":
        return a if _js_truthy(a) else b
    if op == "shl":
        return _js_int32(_js_int32(a) << (_js_int32(b) & 31))
    if op == "shr":
        return _js_int32(a) >> (_js_int32(b) & 31)
    if op in ("bAND", "bOR", "bXOR"):
        x, y = _js_int32(a), _js_int32(b)
        return {"bAND": x & y, "bOR": x | y, "bXOR": x ^ y}[op]
    # atan2: Math.floor(wrap8Bit(Math.atan2(a, b) * (128 / Math.PI) + 64))
    angle = math.atan2(a, b) * (128 / math.pi) + 64
    return _js_floor(math.fmod(256 + math.fmod(angle, 256), 256), op)


def _js_object(value):
    """`input.valueA ? ... : zero`: an object is truthy even when empty."""
    return isinstance(value, dict) or (value is not None and value is not False and value != 0 and value != "")


def _is_number(value):
    return isinstance(value, dict) and value.get("type") == "number"


def optimise_script_value(value):
    """shared/lib/scriptValue/helpers.ts optimiseScriptValue."""
    zero = {"type": "number", "value": 0}
    kind = value.get("type") if isinstance(value, dict) else None
    if kind in VALUE_OPERATOR_TYPES:
        a = optimise_script_value(value["valueA"]) if _js_object(value.get("valueA")) else zero
        b = optimise_script_value(value["valueB"]) if _js_object(value.get("valueB")) else zero
        if _is_number(a) and _is_number(b):
            return {"type": "number", "value": _fold(kind, a["value"], b["value"])}
        return {**value, "valueA": a, "valueB": b}
    if kind in VALUE_UNARY_OPERATOR_TYPES:
        inner = optimise_script_value(value["value"]) if _js_object(value.get("value")) else zero
        if _is_number(inner):
            v = inner["value"]
            if kind == "not":
                return {"type": "number", "value": int(not _js_truthy(v))}
            if kind == "abs":
                return {"type": "number", "value": _js_floor(abs(v), kind)}
            if kind == "isqrt":
                return {"type": "number", "value": _js_floor(math.sqrt(v) if v >= 0 else math.nan, kind)}
            if kind == "bNOT":
                return {"type": "number", "value": ~_js_int32(v)}
            if kind == "rnd":
                return {"type": "rnd", "value": {"type": "number", "value": v}}
            return {"type": "number", "value": _js_floor(-v, kind)}
        return {**value, "value": inner}
    if kind == "expression":
        return optimise_script_value(expression_to_script_value(value.get("value")))
    return value


_UNDEFINED = object()   # a JavaScript argument that was not passed
FADE_SPEEDS = (0x0, 0x1, 0x3, 0x7, 0xF, 0x1F, 0x3F)   # scriptBuilder/helpers.ts fadeSpeeds


def fade_speed(speed):
    """`fadeSpeeds[speed] ?? 0x3`, with fadeIn's default `speed = 1`."""
    if speed is _UNDEFINED:
        speed = 1
    if isinstance(speed, str) and re.fullmatch(r"0|[1-9]\d*", speed):
        speed = int(speed)                       # JS: arr["2"] is arr[2]
    if isinstance(speed, float) and speed.is_integer():
        speed = int(speed)
    if type(speed) is int and 0 <= speed < len(FADE_SPEEDS):
        return FADE_SPEEDS[speed]
    return 0x3


def _js_gt_zero(value):
    """JavaScript `value > 0` for the values a fadeSpeed field holds (number,
    numeric string, null). Anything else is refused rather than guessed."""
    if value is None:
        return False
    if type(value) in (int, float):
        return value > 0
    if isinstance(value, str):
        if value.strip() == "":
            return False                         # Number("") is 0
        try:
            return float(value) > 0
        except ValueError:
            return False                         # NaN > 0
    raise CompileError(f"fadeSpeed {value!r} is not a number")


def to_asm_sprite_mode(mode):
    return ".MODE_8X8" if mode == "8x8" else ".MODE_8X16"


def default_variable_for_context(context):
    """shared/lib/scripts/context.ts"""
    if context == "script":
        return "V0"
    if context == "entity":
        return "L0"
    return "0"


def js_object_keys(mapping):
    """Object.keys order: array-index keys ascending, then insertion order."""
    def is_index(k):
        return re.fullmatch(r"0|[1-9]\d*", k) is not None and int(k) < 2**32 - 1
    return sorted((k for k in mapping if is_index(k)), key=int) + [k for k in mapping if not is_index(k)]


NUM_SUBPIXEL_BITS = 5    # src/consts.ts


def subpx_shift_for_units(units):
    """shared/lib/helpers/subpixels.ts subpxShiftForUnits."""
    return NUM_SUBPIXEL_BITS + 3 if units == "tiles" else NUM_SUBPIXEL_BITS


def vm_position_shift(units):
    """Shift from `units` to a script's position words: GB Studio's subpixels,
    or pixels in a wide world (world.py: positions over 2048 px do not fit a
    16-bit word in 1/32 px; the engine converts at the native boundary).
    Collision boxes and speeds stay in subpixels (subpx_shift_for_units)."""
    from ..world import wide
    return subpx_shift_for_units(units) - (NUM_SUBPIXEL_BITS if wide() else 0)


# Instructions whose operands hold positions (GBVM vm_actor.c, vm_camera.c,
# vm_projectiles.c): refused in hand-written GBVM of a wide world.
RAW_POSITION_OPS = frozenset(("VM_ACTOR_SET_POS", "VM_ACTOR_GET_POS", "VM_ACTOR_MOVE_TO", "VM_ACTOR_MOVE_TO_INIT",
                              "VM_ACTOR_MOVE_TO_X", "VM_ACTOR_MOVE_TO_Y", "VM_ACTOR_MOVE_TO_XY",
                              "VM_ACTOR_MOVE_TO_SET_DIR_X", "VM_ACTOR_MOVE_TO_SET_DIR_Y",
                              "VM_CAMERA_SET_POS", "VM_CAMERA_MOVE_TO", "VM_PROJECTILE_LAUNCH"))


def vm_position_px(subpixels):
    """A constant offset in subpixels (GB Studio) as a script position word."""
    from ..world import wide
    return subpixels >> NUM_SUBPIXEL_BITS if wide() else subpixels


def precompile_script_value(value, locals_label="", rpn_operations=None, fetch_operations=None):
    """shared/lib/scriptValue/helpers.ts precompileScriptValue."""
    return precompile_optimised_script_value(
        optimise_script_value(value), locals_label,
        [] if rpn_operations is None else rpn_operations, [] if fetch_operations is None else fetch_operations)


def precompile_optimised_script_value(value, locals_label="", rpn_operations=None, fetch_operations=None):
    """shared/lib/scriptValue/helpers.ts precompileOptimisedScriptValue."""
    rpn = [] if rpn_operations is None else rpn_operations
    fetch = [] if fetch_operations is None else fetch_operations
    kind = value.get("type")
    pos_x, pos_y = ("xpos", "pxpos", "spxpos"), ("ypos", "pypos", "spypos")

    def shift(prop):
        from ..world import wide
        if prop in ("xpos", "ypos"):
            rpn.extend([{"type": "number", "value": vm_position_shift("tiles")}, {"type": "shr"}])
        elif prop in ("pxpos", "pypos"):
            if not wide():       # world.py: the word is already pixels
                rpn.extend([{"type": "number", "value": subpx_shift_for_units("pixels")}, {"type": "shr"}])
        elif wide():
            raise CompileError(f"script value {prop}: an actor position in subpixels does not fit a "
                               "16-bit value in a scene over 255 tiles (docs/PLAN_GRANDES_CARTES.md)")

    if kind == "property":
        prop = value.get("property")
        if value.get("target") == "camera":
            memory = {"xdeadzone": ("memI8", "camera_deadzone_x"), "ydeadzone": ("memI8", "camera_deadzone_y"),
                      "xoffset": ("memI8", "camera_offset_x"), "yoffset": ("memI8", "camera_offset_y"),
                      "xscroll": ("memI16", "scroll_x"), "yscroll": ("memI16", "scroll_y")}
            if prop in pos_x + pos_y:
                rpn.append({"type": "memI16", "value": "camera_x" if prop in pos_x else "camera_y"})
                shift(prop)
            elif prop in memory:
                rpn.append({"type": memory[prop][0], "value": memory[prop][1]})
            else:
                raise CompileError(f'Unsupported property type "{prop}"')
        else:
            local_name = f"local_{locals_label}{len(fetch)}"
            if prop in pos_x + pos_y:
                fetch.append({"local": local_name, "value": {"type": "actorPosition", "target": value.get("target")}})
                rpn.append({"type": "local", "value": local_name, "offset": 1 if prop in pos_x else 2})
                shift(prop)
            elif prop == "direction":
                fetch.append({"local": local_name, "value": {"type": "actorDirection", "target": value.get("target")}})
                rpn.append({"type": "local", "value": local_name})
            elif prop == "frame":
                fetch.append({"local": local_name, "value": {"type": "actorFrame", "target": value.get("target")}})
                rpn.append({"type": "local", "value": local_name, "offset": 1})
            else:
                raise CompileError(f'Unsupported property type "{prop}"')
    elif kind in VALUE_OPERATOR_TYPES:
        if _js_object(value.get("valueA")):
            precompile_script_value(value["valueA"], locals_label, rpn, fetch)
        if _js_object(value.get("valueB")):
            precompile_script_value(value["valueB"], locals_label, rpn, fetch)
        rpn.append({"type": kind})
    elif kind in VALUE_UNARY_OPERATOR_TYPES:
        if _js_object(value.get("value")):
            precompile_script_value(value["value"], locals_label, rpn, fetch)
        rpn.append({"type": kind})
    elif kind == "true":
        rpn.append({"type": "number", "value": 1})
    elif kind == "false":
        rpn.append({"type": "number", "value": 0})
    elif kind == "numberSymbol":
        rpn.append({"type": "numberSymbol", "value": value.get("value")})
    elif kind == "engineField":
        local_name = f"local_{locals_label}{len(fetch)}"
        fetch.append({"local": local_name, "value": {"type": "engineField", "value": value.get("value")}})
        rpn.append({"type": "local", "value": local_name})
    else:
        rpn.append(value)
    # peepholeRPN returns a new list: the caller's list keeps its content
    # (GB Studio's recursion pushes into the same array and ignores the result).
    return peephole_rpn(rpn), fetch


def peephole_rpn(rpn):
    """shared/lib/scriptValue/helpers.ts peepholeRPN."""
    optimised, i = [], 0
    while i < len(rpn):
        t = [op.get("type") for op in rpn[i:i + 6]]
        # SHR X + Add/Sub Y + SHL X
        if (len(t) == 6 and t[0] == "number" and t[1] == "shr" and t[2] == "number" and t[3] in ("add", "sub")
                and t[4] == "number" and t[5] == "shl"):
            shr_amount, add_amount, shl_amount = rpn[i]["value"], rpn[i + 2]["value"], rpn[i + 4]["value"]
            if shr_amount == shl_amount:
                combined = _js_int32(_js_int32(add_amount) << (_js_int32(shl_amount) & 31))
                mask = ~(_js_int32(_js_int32(1) << (_js_int32(shl_amount) & 31)) - 1) & 0xFFFF
                optimised += [{"type": "number", "value": combined}, rpn[i + 3],
                              {"type": "number", "value": mask}, {"type": "bAND"}]
                i += 6
                continue
        # SHR N followed by SHL N = noop
        if (len(t) >= 4 and t[0] == "number" and t[1] == "shr" and t[2] == "number" and t[3] == "shl"
                and rpn[i]["value"] == rpn[i + 2]["value"]):
            i += 4
            continue
        optimised.append(rpn[i])
        i += 1
    return optimised


# scriptBuilder/helpers.ts valueFunctionToScriptOperator and rpnUnaryOperators.
SCRIPT_OPERATORS = {"add": ".ADD", "sub": ".SUB", "div": ".DIV", "mul": ".MUL", "mod": ".MOD", "eq": ".EQ",
                    "ne": ".NE", "lt": ".LT", "lte": ".LTE", "gt": ".GT", "gte": ".GTE", "min": ".MIN",
                    "max": ".MAX", "and": ".AND", "or": ".OR", "abs": ".ABS", "atan2": ".ATAN2",
                    "isqrt": ".ISQRT", "not": ".NOT", "shl": ".SHL", "shr": ".SHR", "bAND": ".B_AND",
                    "bOR": ".B_OR", "bXOR": ".B_XOR", "bNOT": ".B_NOT", "rnd": ".RND", "neg": ".NEG"}
RPN_UNARY_OPERATORS = (".ABS", ".NOT", ".B_NOT", ".ISQRT", ".RND", ".NEG")


def to_asm_dir(direction):
    """scriptBuilder/helpers.ts toASMDir."""
    return {"left": ".DIR_LEFT", "right": ".DIR_RIGHT", "up": ".DIR_UP", "down": ".DIR_DOWN"}.get(direction,
                                                                                               ".DIR_DOWN")


# src/lib/compiler/helpers.ts KEY_BITS (GBDK J_* bits).
KEY_BITS = {"left": 0x02, "right": 0x01, "up": 0x04, "down": 0x08,
            "a": 0x10, "b": 0x20, "select": 0x40, "start": 0x80}


def input_dec(input_):
    """helpers.ts inputDec. GB Studio ignores an unknown key in a list (`|=
    undefined`) and crashes on an unknown single key: both refused here. An
    empty selection becomes 255 (any button), as in GB Studio, which says why."""
    keys = input_ if isinstance(input_, list) else [input_]
    unknown = [k for k in keys if not isinstance(k, str) or k not in KEY_BITS]
    if unknown:
        raise CompileError(f"input {input_!r}: unknown button(s) {unknown} (expected {', '.join(KEY_BITS)})")
    output = 0
    for k in keys:
        output |= KEY_BITS[k]
    if output == 0:
        # "If no input set game would hang as could not continue on, assume
        # this isn't what user wants and instead allow any input."
        output = 255
    return output


def union_flags(flags, default_value="0"):
    """scriptBuilder/helpers.ts unionFlags."""
    if not flags:
        return default_value
    if len(flags) == 1:
        return flags[0]
    return f"^/({' | '.join(flags)})/"


def and_flags(flags, default_value="0"):
    """scriptBuilder/helpers.ts andFlags."""
    if not flags:
        return default_value
    if len(flags) == 1:
        return flags[0]
    return f"^/({' & '.join(flags)})/"


def to_asm_move_flags(move_type, use_collisions, relative=False, relative_units=None):
    """scriptBuilder/helpers.ts toASMMoveFlags. use_collisions: True, a list of
    "walls" / "actors", or anything else (no collision flag), as in GB Studio."""
    is_list = isinstance(use_collisions, list)
    flags = []
    if use_collisions is True:
        flags.append(".ACTOR_ATTR_CHECK_COLL")
    if is_list and "walls" in use_collisions:
        flags.append(".ACTOR_ATTR_CHECK_COLL_WALLS")
    if is_list and "actors" in use_collisions:
        flags.append(".ACTOR_ATTR_CHECK_COLL_ACTORS")
    if move_type == "horizontal":
        flags.append(".ACTOR_ATTR_H_FIRST")
    if move_type == "diagonal":
        flags.append(".ACTOR_ATTR_DIAGONAL")
    if relative and relative_units == "pixels":
        flags.append(".ACTOR_ATTR_RELATIVE_SNAP_PX")
    if relative and relative_units == "tiles":
        flags.append(".ACTOR_ATTR_RELATIVE_SNAP_TILE")
    return union_flags(flags)


class _RPN:
    """ScriptBuilderBase._rpn: RPN lines are collected, then written after
    VM_RPN by stop()."""

    def __init__(self, sb):
        self.sb, self.output, self.size = sb, [], 0

    def _cmd(self, cmd, *args):
        self.output.append(self.sb._pad_cmd(cmd, ", ".join(self.sb._offset_stack_addr(a) for a in args), 12, 12))

    def actor_id(self, actor_id):
        kind, value = self.sb.resolve_actor_id(actor_id)
        self._cmd(".R_INT16" if kind == "number" else ".R_REF", value)
        self.size += 1
        return self

    def ref(self, variable):
        self._cmd(".R_REF", variable)
        self.size += 1
        return self

    def ref_variable(self, variable):
        alias = self.sb.get_variable_alias(variable)
        if self.sb._is_indirect_variable(variable):
            self._cmd(".R_REF_IND", alias)
            self.size += 1
            return self
        return self.ref(alias)

    def ref_set(self, variable):
        self._cmd(".R_REF_SET", variable)
        self.size -= 1
        return self

    def ref_set_variable(self, variable):
        alias = self.sb.get_variable_alias(variable)
        if self.sb._is_indirect_variable(variable):
            self._cmd(".R_REF_SET_IND", alias)
            self.size -= 1
            return self
        return self.ref_set(alias)

    def ref_mem(self, mem_type, address):
        self._cmd(".R_REF_MEM", mem_type, f"_{address}")
        self.size += 1
        return self

    def mem_set(self, mem_type, address):
        self._cmd(".R_REF_MEM_SET", mem_type, f"_{address}")
        self.size -= 1
        return self

    def ref_mem_ind(self, mem_type, pointer_address):
        self._cmd(".R_REF_MEM_IND", mem_type, pointer_address)
        self.size += 1
        return self

    def int8(self, value):
        self._cmd(".R_INT8", value)
        self.size += 1
        return self

    def int16(self, value):
        self._cmd(".R_INT16", value)
        self.size += 1
        return self

    def operator(self, op):
        self._cmd(".R_OPERATOR", op)
        if op not in RPN_UNARY_OPERATORS:
            self.size -= 1
        return self

    def stop(self):
        self._cmd(".R_STOP")
        self.sb._add_cmd("VM_RPN")
        self.sb.output.extend(self.output)
        self.sb.stack_ptr += self.size


class ScriptBuilder:
    def __init__(self, output, options):
        self.output = output
        self.options = {
            "context": "entity", "scriptSymbolName": "script_1", "sceneIndex": 0, "entityIndex": 0,
            "entityType": "scene", "entityScriptKey": "script", "variablesLookup": {},
            "variableAliasLookup": {}, "entity": None, "argLookup": {"actor": {}, "variable": {}},
            # scriptBuilderBase constructor defaults (custom scripts, scene list).
            "scenes": [], "customEvents": [], "additionalScripts": {}, "symbols": {}, "maxDepth": 5,
            "compiledCustomEventScriptCache": {}, "recursiveSymbolMap": {}, "additionalScriptsCache": {},
            "entities": {},
            # Located warnings of the export (compileData's shared options carry one list).
            "warnings": [],
            **{k: v for k, v in options.items() if v is not None},
        }
        self.source = None          # location of the event being compiled (events._compile_list)
        self.event = None           # that event (helpers.event in GB Studio)
        self.dependencies = []
        self.next_label = 1
        self.label_lookup = {}
        self.label_stack_size = {}
        self.locals_lookup = {}
        self.locals_size = 0
        self.stack_ptr = 0
        self.headers = ["vm.i", "data/game_globals.i"]
        self.data_tables = {}
        # scriptBuilderBase constructor: helpers.getActorIndex (findIndex + 1).
        entity, actors = self.options.get("entity"), (self.options.get("scene") or {}).get("actors") or []
        self.actor_index = (next((i + 1 for i, a in enumerate(actors) if a["id"] == entity["id"]), 0)
                            if entity else 0)

    # -- debugger (scriptBuilderBase.ts addDebugSymbol / addDebugEndSymbol) ----
    def add_debug_symbol(self, script_symbol_name, script_event_id):
        if self.options.get("debugEnabled"):
            scene_id = (self.options.get("scene") or {}).get("id") or ""
            entity = self.options.get("entity")
            parts = ([script_symbol_name, script_event_id, scene_id, "scene", scene_id, "script"]
                     if script_event_id == "autofade" else
                     [script_symbol_name, script_event_id, scene_id, self.options["entityType"],
                      (entity or {}).get("id") or "", self.options.get("entityScriptKey") or "script"])
            symbol = "$".join(str(p).replace("-", "_") for p in parts)
            self.output.append(f"GBVM${symbol} = .")
            self.output.append(f".globl GBVM${symbol}")

    def add_debug_end_symbol(self, script_symbol_name, script_event_id):
        if self.options.get("debugEnabled"):
            symbol = "$".join(str(p).replace("-", "_") for p in (script_symbol_name, script_event_id))
            self.output.append(f"GBVM_END${symbol} = .")
            self.output.append(f".globl GBVM_END${symbol}")

    # -- output --------------------------------------------------------------
    def _add_banked_fn_dependency(self, symbol):
        for dep in (f"b_{symbol}", f"_{symbol}"):
            if dep not in self.dependencies:
                self.dependencies.append(dep)

    def _add_comment(self, comment):
        self.output.append(f"        ; {comment}")

    def _add_nl(self):
        self.output.append("")

    def _add_cmd(self, cmd, *args):
        args = list(args)
        comment = ""
        last = args[-1] if args else None
        if isinstance(last, str) and (last.startswith(";") or last == ""):
            comment = last
            args.pop()
        self.output.append(self._pad_cmd(
            cmd, ", ".join(self._offset_stack_addr(a) for a in args) + (f" {comment}" if comment else ""), 8, 24))

    @staticmethod
    def _pad_cmd(cmd, args, n_pad_start, n_pad_cmd):
        start = " " * n_pad_start
        if args:
            return f"{start}{cmd.ljust(max(n_pad_cmd, len(cmd) + 1))}{args}"
        return f"{start}{cmd}"

    def _assert_stack_neutral(self, expected=0):
        if self.stack_ptr != expected:
            diff = self.stack_ptr - expected
            if diff > 0:
                raise CompileError(f"Script was not stack neutral! Stack grew by {diff}")
            raise CompileError(f"Script was not stack neutral! Stack shrank by {-diff}")

    def _assert_label_stack_neutral(self, label):
        # `!this.labelStackSize[label]`: a label first seen at stack size 0 is
        # recorded again at its next use (0 is falsy). Kept as is.
        if not self.label_stack_size.get(label):
            self.label_stack_size[label] = self.stack_ptr
        elif self.stack_ptr != self.label_stack_size[label]:
            raise CompileError(f"Jump to label with different stack size. First call size="
                               f"{self.label_stack_size[label]}, this call size={self.stack_ptr}")

    # -- low level (scriptBuilderBase.ts) ------------------------------------
    def _stack_push_const(self, value, comment=None):
        self._add_cmd("VM_PUSH_CONST", value, f"; {comment}" if comment else "")
        self.stack_ptr += 1

    def _stack_push(self, addr):
        self._add_cmd("VM_PUSH_VALUE", addr)
        self.stack_ptr += 1

    def _stack_push_ind(self, addr):
        self._add_cmd("VM_PUSH_VALUE_IND", addr)
        self.stack_ptr += 1

    def _stack_pop(self, num):
        self._add_cmd("VM_POP", num)
        self.stack_ptr -= num

    def _stack_push_reference(self, addr, comment=None):
        self._add_cmd("VM_PUSH_REFERENCE", addr, f"; {comment}" if comment else "")
        self.stack_ptr += 1

    def _mark_local_use(self, asm_symbol):
        if asm_symbol in self.locals_lookup:
            self.locals_lookup[asm_symbol]["lastUse"] = len(self.output)

    def _reserve(self, size):
        self._add_cmd("VM_RESERVE", size)

    def _call_far(self, symbol, args_len):
        self._add_cmd("VM_CALL_FAR", f"___bank_{symbol}", f"_{symbol}")
        if args_len > 0:
            # Args are popped by called script with ret_far_n
            self.stack_ptr -= args_len

    def _return_far(self):
        self._add_cmd("VM_RET_FAR")

    def _return_far_n(self, locals_size):
        self._add_cmd("VM_RET_FAR_N", locals_size)

    def return_far(self):
        """scriptBuilder.ts returnFar."""
        size = len(self.options["argLookup"]["variable"]) + len(self.options["argLookup"]["actor"])
        if size == 0:
            self._return_far()
        else:
            self._return_far_n(size)

    def unreserve_locals(self):
        size = self._calc_locals_size()
        if size != 0:
            self._reserve(-size)

    @staticmethod
    def _is_function_arg(x):
        return isinstance(x, dict) and isinstance(x.get("type"), str) and x["type"] == "argument"

    def _is_indirect_variable(self, x):
        return self._is_function_arg(x) and bool(x["indirect"])

    def _context_hash(self):
        """_contextHash: scene.hash is stood for by the scene id (custom_events.py)."""
        o = self.options
        scene, entity = o.get("scene") or {}, o.get("entity")
        return f"{scene.get('id', '')}_{o['context']}_{o['entityType']}_{entity['id'] if entity else ''}"

    def _get_available_symbol(self, name, register=True):
        symbols = self.options["symbols"]
        if name not in symbols:
            if register:
                symbols[name] = name
            return name
        counter, new_name = 0, name
        while True:
            new_name = f"{new_name}_0" if counter == 0 else f"{re.sub(r'_[0-9]+$', '', new_name)}_{counter}"
            if new_name not in symbols:
                if register:
                    symbols[new_name] = new_name
                return new_name
            counter += 1

    def call_script(self, script_id, input_):
        from .custom_events import call_script
        call_script(self, script_id, input_)

    def platform_callback(self, event, script=None, remove=False, kind="platform"):
        """eventPlatformerCallbackScriptSet/Remove.js, eventAdventureCallbackScriptSet/Remove.js."""
        from .platform_callbacks import KINDS, label
        from .custom_events import compile_sub_script
        prefix = KINDS[kind][0]
        slot = label(event, kind)
        name = "Platformer" if kind == "platform" else "Adventure"
        self._add_comment(f"Remove {name} State Script" if remove else f"Set {name} Script")
        if not remove:
            script = script or []
            where = f"{self.source}.children.script" if self.source else f"{kind} state script"
            ref = compile_sub_script(self, "state", script, f"{prefix}_callback_" + slot, {},
                                      [f"{where}[{i}]" for i in range(len(script))])
        self._stack_push_const(slot)
        if not remove:
            self._stack_push_const(f"___bank_{ref}")
            self._stack_push_const(f"_{ref}")
        native = f"{prefix}_callback_detach" if remove else f"{prefix}_callback_attach"
        self.dependencies += [f"b_{native}", f"_{native}"]
        self._add_cmd("VM_CALL_NATIVE", f"b_{native}", f"_{native}")
        self._stack_pop(1 if remove else 3)
        self._add_nl()

    # -- NGPC events (not in GB Studio) ----------------------------------------
    def ngpc_background_variant(self, variant):
        """EVENT_NGPC_BACKGROUND_VARIANT (native_variants.py, PLAN_MEGAMAN L5): the
        GB Studio plugin idiom, argument pushed, native called, argument popped."""
        if type(variant) is not int or not 0 <= variant <= 15:
            raise CompileError(f"{self.source}: background colour variant {variant!r}: expected 0..15 "
                               "(0 = as drawn)")
        self._add_comment(f"NGPC background colour variant {variant}")
        self._stack_push_const(variant)
        self.dependencies += ["b_ngc_bg_variant", "_ngc_bg_variant"]
        self._add_cmd("VM_CALL_NATIVE", "b_ngc_bg_variant", "_ngc_bg_variant")
        self._stack_pop(1)
        self._add_nl()

    def _store_arg0(self, variable):
        """variable = .ARG0 (direct or indirect variable)."""
        alias = self.get_variable_alias(variable)
        if self._is_indirect_variable(variable):
            self._set_ind(alias, ".ARG0")
        else:
            self._set(alias, ".ARG0")

    def ngpc_clock_get(self, variables):
        """EVENT_NGPC_CLOCK_GET (gbvm/ngpc_system.py): {field: variable} from the
        console clock, one read for all (first field: CLOCK_READ)."""
        from .ngpc_system import CLOCK_FIELDS, CLOCK_NATIVE, CLOCK_READ
        unknown = sorted(set(variables) - set(CLOCK_FIELDS))
        if unknown:
            raise CompileError(f"{self.source}: clock fields {unknown}: expected {list(CLOCK_FIELDS)}")
        self._add_comment("NGPC Store Clock In Variables")
        first = True
        for code, field in enumerate(CLOCK_FIELDS):
            if field not in variables:
                continue
            if first:
                self.dependencies += [f"b_{CLOCK_NATIVE}", f"_{CLOCK_NATIVE}"]
            self._stack_push_const(code | (CLOCK_READ if first else 0))
            first = False
            self._add_cmd("VM_CALL_NATIVE", f"b_{CLOCK_NATIVE}", f"_{CLOCK_NATIVE}")
            self._store_arg0(variables[field])
            self._stack_pop(1)
        self._add_nl()

    def ngpc_battery_get(self, variable):
        """EVENT_NGPC_BATTERY_GET: Battery_Voltage (0x6F80, 0..1023, SDK SysWork.txt)."""
        self._add_comment("NGPC Store Battery Voltage In Variable")
        self._stack_push_const(0)
        self._get_mem_int16(".ARG0", "ngc_battery")
        self._store_arg0(variable)
        self._stack_pop(1)
        self._add_nl()

    def ngpc_if_language(self, language, true_path=None, false_path=None):
        """EVENT_NGPC_IF_LANGUAGE: Language (0x6F87): 0 = Japanese, 1 = English (SDK SysWork.txt)."""
        codes = {"japanese": 0, "english": 1}
        if language not in codes:
            raise CompileError(f"{self.source}: console language {language!r}: expected 'english' or 'japanese'")
        self._if_device("ngc_language", f"NGPC If Console Language {language}", "ngc_language",
                        true_path, false_path, value=codes[language])

    def ngpc_shutdown(self):
        """EVENT_NGPC_SHUTDOWN: VECT_SHUTDOWN (gbvm/ngpc_system.py)."""
        from .ngpc_system import SHUTDOWN_NATIVE
        self._add_comment("NGPC Switch Off")
        self.dependencies += [f"b_{SHUTDOWN_NATIVE}", f"_{SHUTDOWN_NATIVE}"]
        self._add_cmd("VM_CALL_NATIVE", f"b_{SHUTDOWN_NATIVE}", f"_{SHUTDOWN_NATIVE}")
        self._add_nl()

    # -- input scripts (scriptBuilder.ts inputScriptSet / inputScriptRemove) --
    def input_script_set(self, input_, override, script, symbol=None):
        from .custom_events import compile_sub_script
        self._add_comment("Input Script Attach")
        where = f"{self.source}.children.true" if self.source else "input script"
        script = script or []
        script_ref = compile_sub_script(self, "input", script, symbol, {},
                                        [f"{where}[{i}]" for i in range(len(script))])
        input_value = input_dec(input_)
        self._warn_any_button(input_)
        # inputValue.toString(2).padStart(8, "0").indexOf("1") + 1: the
        # highest button bit gives the slot (start 1 ... right 8).
        ctx = format(input_value, "08b").find("1") + 1
        if ctx <= 0:
            ctx = 1
        self._input_context_prepare(script_ref, ctx)
        self._input_context_attach(input_value, ctx, override)
        self._add_nl()

    # -- timer scripts (scriptBuilder.ts timerScriptSet / timerRestart / timerDisable) --
    def timer_script_set(self, frames, script, symbol=None, timer=1):
        from .custom_events import compile_sub_script
        self._add_comment("Timer Start")
        where = f"{self.source}.children.script" if self.source else "timer script"
        script = script or []
        script_ref = compile_sub_script(self, "timer", script, symbol, {},
                                        [f"{where}[{i}]" for i in range(len(script))])
        timer_cycles = 16
        # (frames / TIMER_CYCLES + 0.5) | 0: JavaScript ToInt32 (truncate, wrap at 2^32).
        value = frames / timer_cycles + 0.5
        duration_ticks = 0 if value != value or value in (math.inf, -math.inf) else \
            ((int(value) + 2 ** 31) % 2 ** 32) - 2 ** 31
        if duration_ticks <= 0:
            duration_ticks = 1
        if duration_ticks >= 256:
            duration_ticks = 255
        self._timer_context_prepare(script_ref, timer)
        self._timer_start(timer, duration_ticks)
        self._add_nl()

    def timer_restart(self, timer=1):
        self._add_comment("Timer Restart")
        self._timer_reset(timer)

    def timer_disable(self, timer=1):
        self._add_comment("Timer Disable")
        self._timer_stop(timer)

    # -- EVENT_GBVM_SCRIPT (scriptBuilder.ts appendRaw / compileReferencedAssets) --
    def append_raw(self, code):
        """Each line as a command; the assembler accepts delivered instructions
        only and refuses the others with their line (ECARTS 12)."""
        for number, line in enumerate(code.split("\n"), 1):
            self._check_raw_line(number, line)
            self._add_cmd(line)
        self._add_nl()

    @staticmethod
    def _check_raw_line(number, line):
        """Refuse, at the event, an instruction the NGPC does not deliver: the
        block line is named (the assembler would only name the generated line)."""
        from .assembler import load_model
        from .emit import DECODERS
        model = load_model()
        word = line.split(";", 1)[0].split()
        if not word or not word[0][0].isupper():
            return                     # comment, label, .R_ / .dw data: the assembler checks them
        if word[0] not in model["macros"] and word[0] not in model["composite_macros"]:
            raise CompileError(f"GBVM script line {number}: unknown instruction {word[0]}")
        from ..world import wide
        if wide() and word[0] in RAW_POSITION_OPS:
            # world.py: script position words are pixels in a game with a scene
            # over 255 tiles; hand-written GBVM computes GB Studio's subpixels.
            raise CompileError(f"GBVM script line {number}: {word[0]} reads or writes positions in GB Studio "
                               "subpixels, which do not fit 16 bits in a game with a scene over 255 tiles "
                               "(docs/PLAN_GRANDES_CARTES.md)")

        def missing(macro, seen=()):
            if macro in model["macros"]:
                return None if macro == "VM_STOP" or macro in DECODERS else macro
            for sub in model["composite_macros"].get(macro, {}).get("lines", ()):
                name = sub.split()[0] if sub.split() else ""
                if name and name[0].isupper() and name not in seen:
                    found = missing(name, seen + (macro,))
                    if found:
                        return found
            return None
        absent = missing(word[0])
        if absent:
            needs = "" if absent == word[0] else f" (it expands to {absent})"
            raise CompileError(f"GBVM script line {number}: {word[0]}{needs} is not delivered on the NGPC "
                               "(ECARTS 12: only delivered instructions are accepted)")

    def compile_referenced_assets(self, references):
        from .custom_events import compile_custom_event_script
        for custom_event_id in [r.get("id") for r in references if r.get("type") == "script"]:
            compile_custom_event_script(self, custom_event_id)

    # -- threads (scriptBuilder.ts threadStart / threadTerminate) --
    def thread_start(self, handle_variable, script, symbol=None):
        from .custom_events import compile_sub_script
        self._add_comment("Thread Start")
        where = f"{self.source}.children.true" if self.source else "thread script"
        script = script or []
        script_ref = compile_sub_script(self, "thread", script, symbol, {},
                                        [f"{where}[{i}]" for i in range(len(script))])
        self._add_cmd("VM_UNLOCK")
        self._thread_start_with_variable_handle(script_ref, handle_variable, 0)
        self._add_nl()

    def thread_terminate(self, handle_variable):
        self._add_comment("Thread Stop")
        self._thread_terminate_with_variable_handle(handle_variable)
        self._add_nl()

    def _thread_start(self, symbol, handle_addr, num_args):
        self._add_cmd("VM_BEGINTHREAD", f"___bank_{symbol}", f"_{symbol}", handle_addr, num_args)

    def _thread_start_with_variable_handle(self, symbol, handle_variable, num_args):
        alias = self.get_variable_alias(handle_variable)
        if self._is_indirect_variable(handle_variable):
            value_tmp = self._declare_local("value_tmp", 1, True)
            self._thread_start(symbol, value_tmp, num_args)
            self._set_ind(alias, value_tmp)
        else:
            self._thread_start(symbol, alias, num_args)

    def _thread_terminate(self, handle_addr):
        self._add_cmd("VM_TERMINATE", handle_addr)

    def _thread_terminate_with_variable_handle(self, handle_variable):
        alias = self.get_variable_alias(handle_variable)
        if self._is_indirect_variable(handle_variable):
            self._stack_push_ind(alias)
            self._thread_terminate(".ARG0")
            self._stack_pop(1)
        else:
            self._thread_terminate(alias)

    def _timer_context_prepare(self, symbol, context):
        self._add_cmd("VM_TIMER_PREPARE", context, f"___bank_{symbol}", f"_{symbol}")

    def _timer_start(self, context, interval):
        self._add_cmd("VM_TIMER_SET", context, interval)

    def _timer_stop(self, context):
        self._add_cmd("VM_TIMER_STOP", context)

    def _timer_reset(self, context):
        self._add_cmd("VM_TIMER_RESET", context)

    def input_script_remove(self, input_):
        self._add_comment("Input Script Remove")
        self._input_context_detach(input_dec(input_))
        self._warn_any_button(input_)
        self._add_nl()

    def _warn_any_button(self, input_):
        if input_ == []:
            self.warn("no button selected: compiled as GB Studio does, for ANY button (inputDec: 255); "
                      "select the button(s) explicitly to remove this warning")

    def _input_context_prepare(self, symbol, context):
        self._add_cmd("VM_CONTEXT_PREPARE", context, f"___bank_{symbol}", f"_{symbol}")

    def _input_context_attach(self, button_mask, context, override):
        self._add_cmd("VM_INPUT_ATTACH", button_mask,
                      union_flags([str(context)] + ([".OVERRIDE_DEFAULT"] if override else [])))

    def _input_context_detach(self, button_mask):
        self._add_cmd("VM_INPUT_DETACH", button_mask)

    def _label(self, label):
        label = to_valid_label(label)
        self._assert_label_stack_neutral(label)
        self.output.append(f"{label}$:")

    def _jump(self, label):
        label = to_valid_label(label)
        self._assert_label_stack_neutral(label)
        self._add_cmd("VM_JUMP", f"{label}$")

    def _if(self, operator, variable_a, variable_b, label, pop_num):
        self._add_cmd("VM_IF", operator, variable_a, variable_b, f"{label}$", pop_num)
        self.stack_ptr -= pop_num

    def _if_const(self, operator, variable, value, label, pop_num):
        self._add_cmd("VM_IF_CONST", operator, variable, value, f"{label}$", pop_num)
        self.stack_ptr -= pop_num

    def _switch(self, variable, switch_cases, pop_num):
        """scriptBuilderBase _switch: the cases follow as .dw value, label."""
        self._add_cmd("VM_SWITCH", variable, len(switch_cases), pop_num)
        for switch_case in switch_cases:
            self._dw(*switch_case)
        self.stack_ptr -= pop_num

    def _switch_variable(self, variable, switch_cases, pop_num):
        alias = self.get_variable_alias(variable)
        if self._is_indirect_variable(variable):
            self._stack_push_ind(alias)
            self._switch(".ARG0", switch_cases, pop_num + 1)
        else:
            self._switch(alias, switch_cases, pop_num)

    def case_variable_const_value(self, variable, cases, false_path=None):
        """scriptBuilder.ts caseVariableConstValue; cases: [(value, branch)]."""
        false_path = [] if false_path is None else false_path
        if not cases:
            self._compile_path(false_path)
            return
        case_labels = [self.get_next_label() for _ in cases]
        end_label = self.get_next_label()

        def extract_symbol(value):
            if value["type"] == "number":
                return value["value"]
            if value["type"] == "constant":
                return self.get_constant_symbol(value["value"])
            raise CompileError(f"switch case value of type {value['type']!r}: GB Studio 4.3.2 compiles it as 0")
        symbols = [extract_symbol(value) for value, _ in cases]
        self._add_comment("Switch Variable")
        self._switch_variable(variable, [(symbol, f"{label}$") for symbol, label in zip(symbols, case_labels)], 0)
        self._add_nl()
        # Default
        self._compile_path(false_path)
        self._jump(end_label)
        # Cases
        for symbol, label, (_, branch) in zip(symbols, case_labels, cases):
            self._add_comment(f"case {js_str(symbol)}:")
            self._label(label)
            self._compile_path(branch)
            self._jump(end_label)
        self._label(end_label)
        self._add_nl()

    def _if_variable_const(self, operator, variable, value, label, pop_num):
        alias = self.get_variable_alias(variable)
        if self._is_indirect_variable(variable):
            self._stack_push_ind(alias)
            self._if_const(operator, ".ARG0", value, label, pop_num + 1)
        else:
            self._if_const(operator, alias, value, label, pop_num)

    def _compile_path(self, path=None):
        """_compilePath: a function is called, an event list is compiled."""
        if callable(path):
            path()
        elif path is not None:
            self.options["compileEvents"](self, path)

    def _vm_lock(self):
        self._add_cmd("VM_LOCK")

    def _idle(self):
        self._add_cmd("VM_IDLE")

    def lock(self):
        """Released lock helper; locks belong to the current script context."""
        self._vm_lock()

    def unlock(self):
        """Released unlock helper; runtime preserves nested lock counts."""
        self._add_cmd("VM_UNLOCK")

    def idle(self):
        """Released idle helper: one waitable VM instruction."""
        self._add_comment("Idle")
        self._idle()
        self._add_nl()

    def _invoke(self, fn, pop_num, addr):
        self._add_banked_fn_dependency(fn)
        self._add_cmd("VM_INVOKE", f"b_{fn}", f"_{fn}", pop_num, addr)
        self.stack_ptr -= pop_num

    def _set(self, addr, value):
        self._add_cmd("VM_SET", addr, value)

    def _set_const(self, addr, value):
        self._add_cmd("VM_SET_CONST", addr, value)

    def _set_variable_const(self, variable, value):
        alias = self.get_variable_alias(variable)
        if self._is_indirect_variable(variable):
            ref = self._declare_local("value_tmp", 1, True)
            self._set_const(ref, value)
            self._set_ind(alias, ref)
        else:
            self._set_const(alias, value)

    def _set_variable_to_variable(self, variable_a, variable_b):
        alias_a = self.get_variable_alias(variable_a)
        alias_b = self.get_variable_alias(variable_b)
        dest = alias_b
        if self._is_indirect_variable(variable_b):
            self._stack_push_ind(alias_b)
            dest = ".ARG0"
        if self._is_indirect_variable(variable_a):
            self._set_ind(alias_a, dest)
        else:
            self._set(alias_a, dest)
        if self._is_indirect_variable(variable_b):
            self._stack_pop(1)

    def _set_ind(self, addr, value):
        self._add_cmd("VM_SET_INDIRECT", addr, value)

    def _get_ind(self, addr, value):
        self._add_cmd("VM_GET_INDIRECT", addr, value)

    def _add_dependency(self, symbol):
        if f"_{symbol}" not in self.dependencies:
            self.dependencies.append(f"_{symbol}")

    def _set_const_mem_int8(self, c_variable, value):
        self._add_dependency(c_variable)
        self._add_cmd("VM_SET_CONST_INT8", f"_{c_variable}", value)

    def _fade_in(self, is_modal):
        self._add_cmd("VM_FADE_IN", 1 if is_modal else 0)

    def _fade_out(self, is_modal):
        self._add_cmd("VM_FADE_OUT", 1 if is_modal else 0)

    def _set_sprite_mode(self, mode):
        self._add_cmd("VM_SET_SPRITE_MODE", mode)

    def _actor_set_position(self, addr):
        self._add_cmd("VM_ACTOR_SET_POS", addr)

    def _actor_set_direction(self, addr, asm_dir):
        self._add_cmd("VM_ACTOR_SET_DIR", addr, asm_dir)

    def _raise_exception(self, exception, num_args):
        self._add_cmd("VM_RAISE", exception, num_args)

    def _include_header(self, filename):
        if filename not in self.headers:
            self.headers.append(filename)

    def _add_banked_data_dependency(self, symbol):
        for dep in (f"___bank_{symbol}", f"_{symbol}"):
            if dep not in self.dependencies:
                self.dependencies.append(dep)

    def _import_far_ptr_data(self, far_ptr):
        self._include_header("macro.i")
        self._add_banked_data_dependency(far_ptr)
        self._add_cmd("    IMPORT_FAR_PTR_DATA", f"_{far_ptr}")

    def music_play(self, music_id, loop=False):
        self._add_comment("Music Play")
        tracks = self.options["music"] if "music" in self.options else []
        track = next((track for track in tracks if track["id"] == music_id), None)
        if track is None:
            raise NotDelivered(f"{self.source}: unknown music asset {music_id!r}; no silent omission")
        symbol = track["dataName"] + "_Data"
        self._add_cmd("VM_MUSIC_PLAY", "___bank_" + symbol, "_" + symbol,
                      ".MUSIC_LOOP" if loop else ".MUSIC_NO_LOOP")
        self._add_nl()

    def music_stop(self):
        self._add_comment("Music Stop")
        self._add_cmd("VM_MUSIC_STOP")
        self._add_nl()

    def music_set_mute_mask(self, duty1, duty2, wave, noise):
        """scriptBuilder.ts musicSetMuteMask: a set bit mutes a channel (vm.i
        .MUTE_DUTY1..NOISE); the NGPC voices are ECARTS §7 (docs/M5_MUTE_CHANNEL.md)."""
        self._add_comment("Mute Channel")
        self._add_cmd("VM_MUSIC_MUTE", and_flags(["0x0F"] + [flag for flag, active in (
            ("0x0E", duty1), ("0x0D", duty2), ("0x0B", wave), ("0x07", noise)) if active]))
        self._add_nl()

    def music_routine_set(self, routine, script, symbol=None):
        """scriptBuilder.ts musicRoutineSet: the routine a song's call-routine
        effect (hUGE 6xy, y & 3) starts, with x as its argument (docs/M5_MUSIC_ROUTINE.md).
        Number(routine) is NaN for a non-number (GB Studio then fails to assemble): refused."""
        from .custom_events import compile_sub_script
        self._add_comment("Music Routine Attach")
        where = f"{self.source}.children.true" if self.source else "music routine"
        script = script or []
        script_ref = compile_sub_script(self, "music", script, symbol, {},
                                        [f"{where}[{i}]" for i in range(len(script))])
        if type(routine) not in (int, float) or routine != int(routine):
            raise CompileError(f"Set Music Routine: routine {routine!r} must be an integer 0..3")
        self._add_cmd("VM_MUSIC_ROUTINE", int(routine), f"___bank_{script_ref}", f"_{script_ref}")
        self._add_nl()

    # -- link cable (scriptBuilder.ts linkHost / linkJoin / linkClose / linkTransfer,
    #    scriptBuilderBase.ts _sioSetMode / _sioExchangeVariables; docs/M6_LINK.md) --
    def link_host(self):
        self._add_cmd("VM_SIO_SET_MODE", ".SIO_MODE_SLAVE")

    def link_join(self):
        self._add_cmd("VM_SIO_SET_MODE", ".SIO_MODE_MASTER")

    def link_close(self):
        self._add_cmd("VM_SIO_SET_MODE", ".SIO_MODE_NONE")

    def link_transfer(self, send_variable, receive_variable, packet_size):
        alias_a = self.get_variable_alias(send_variable)
        alias_b = self.get_variable_alias(receive_variable)
        pop = 0
        dest = alias_b
        if self._is_indirect_variable(receive_variable):
            pop += 1
            self._stack_push_const(0)
            dest = ".ARG1" if self._is_indirect_variable(send_variable) else ".ARG0"
        if self._is_indirect_variable(send_variable):
            pop += 1
            self._stack_push_ind(alias_a)
            self._add_cmd("VM_SIO_EXCHANGE", ".ARG0", dest, packet_size)
        else:
            self._add_cmd("VM_SIO_EXCHANGE", alias_a, dest, packet_size)
        if self._is_indirect_variable(receive_variable):
            self._set_ind(alias_b, dest)
        if pop > 0:
            self._stack_pop(pop)

    def _stop(self):
        self._assert_stack_neutral()
        self._add_comment("Stop Script")
        self._add_cmd("VM_STOP")

    # -- locals ----------------------------------------------------------------
    def _declare_local(self, symbol, size, is_temporary=False):
        postfix = re.sub(r"[^A-Z0-9]", "_", symbol.upper())
        asm = f".LOCAL_TMP{len(self.locals_lookup)}_{postfix}" if is_temporary else f".LOCAL_{postfix}"
        n = len(self.output)
        if asm not in self.locals_lookup:
            self.locals_size += size
            self.locals_lookup[asm] = {"symbol": asm, "size": size, "addr": 0, "firstUse": n, "lastUse": n}
        else:
            entry = self.locals_lookup[asm]
            entry["size"] = max(size, entry["size"])
            entry["firstUse"] = min(n, entry["firstUse"])
            entry["lastUse"] = max(n, entry["lastUse"])
        return asm

    def _offset_stack_addr(self, symbol, offset=0):
        if not isinstance(symbol, str):
            return js_str(symbol)
        if not (symbol.startswith(".SCRIPT_ARG_") or symbol.startswith(".LOCAL_")):
            return symbol
        if self.stack_ptr == 0 and offset == 0:
            return symbol
        return (f"^/({symbol}{f' + {offset}' if offset != 0 else ''}"
                f"{f' - {self.stack_ptr}' if self.stack_ptr != 0 else ''})/")

    def _pack_locals(self):
        packed = []
        for local in self.locals_lookup.values():
            for group in packed:
                if local["firstUse"] > group["lastUse"] or local["lastUse"] < group["firstUse"]:
                    group["size"] = max(group["size"], local["size"])
                    group["firstUse"] = min(group["firstUse"], local["firstUse"])
                    group["lastUse"] = max(group["lastUse"], local["lastUse"])
                    group["symbols"].append(local)
                    break
            else:
                packed.append({"size": local["size"], "firstUse": local["firstUse"],
                               "lastUse": local["lastUse"], "symbols": [local]})
        addr, lookup = 0, {}
        for group in packed:
            addr += group["size"]
            for local in group["symbols"]:
                lookup[local["symbol"]] = {**local, "size": group["size"], "addr": addr}
        self.locals_lookup = lookup
        return self._calc_locals_size()

    def _calc_locals_size(self):
        return max((local["addr"] for local in self.locals_lookup.values()), default=0)

    # -- data tables (scriptBuilderBase.ts _registerDataTable) ------------------
    def _register_data_table(self, name, type_, row_size, data, row_labels=None):
        symbol = self._get_available_symbol(name)
        if len(data) % row_size != 0:
            raise CompileError(f"Data length {len(data)} is not divisible by row size {row_size}")
        self.data_tables[symbol] = {"symbol": symbol, "type": type_, "rowSize": row_size, "data": data,
                                    "rowLabels": row_labels}
        return symbol

    def variable_data_table_lookup(self, index_variable, table):
        """scriptBuilder.ts variableDataTableLookup. The table follows the
        script in its bank (toScriptString); its label is a bank address, read
        by .R_REF_MEM_IND in the script's bank (docs/M4_BATCH6_EVENTS.md)."""
        if not isinstance(table, dict) or not isinstance(table.get("variables"), list) \
                or not isinstance(table.get("rows"), list):
            raise NotDelivered(f"data table {table!r}: expected {{variables, rows}} (isScriptDataTable)")
        variables, rows = table["variables"], table["rows"]
        if not variables or not rows:
            return                               # No data provided, skip instruction
        self._add_comment("Variable Data Table")
        data = []
        for row in rows:
            values = row.get("values") or []
            for col_index in range(len(variables)):
                value = values[col_index] if col_index < len(values) else None
                if isinstance(value, dict) and value.get("type") == "number":
                    data.append(js_str(value["value"]))
                elif isinstance(value, dict) and value.get("type") == "constant":
                    data.append(self.get_constant_symbol(value["value"]))
                else:
                    data.append("0")
        labels = [row.get("label") for row in rows]
        data_symbol = self._register_data_table(to_valid_symbol(table.get("label") or "data_table"), ".dw",
                                                len(variables), data, labels)
        addr_ref = self._declare_local("data_addr", 1, True)
        rpn = (self._rpn().int16(data_symbol).ref_variable(index_variable).int16(len(variables) * 2)
               .operator(".MUL").operator(".ADD").ref_set(addr_ref))
        for i, column_variable in enumerate(variables):
            rpn.ref_mem_ind(".MEM_I16", addr_ref).ref_set_variable(column_variable)
            if i < len(variables) - 1:
                rpn.ref(addr_ref).int16(2).operator(".ADD").ref_set(addr_ref)
        rpn.stop()
        self._add_nl()

    # -- variables -------------------------------------------------------------
    def variables_reset(self):
        """Released variablesReset: aliases only; context stacks stay intact."""
        self._add_comment("Variables Reset")
        self._add_cmd("VM_MEMSET", 0, 0, "MAX_GLOBAL_VARS")

    def _argument_symbol(self, arg):
        return arg["symbol"]

    def get_constant_symbol(self, constant_id):
        """scriptBuilderBase.ts getConstantSymbol: an engine constant (engine::NAME)
        by its name, a project constant by its symbol in upper case (defined in
        game_globals.i, emit.global_constants), an unknown id as "0"."""
        if not isinstance(constant_id, str):
            raise CompileError(f"constant id {constant_id!r} is not a string")
        if constant_id.startswith("engine::"):
            return constant_id[len("engine::"):]
        constant = self.options.get("constantsLookup", {}).get(constant_id)
        if not constant:
            return "0"
        return constant["symbol"].upper()

    def get_variable_alias(self, variable=""):
        if self._is_function_arg(variable):
            return self._argument_symbol(variable)
        if not isinstance(variable, (str, int)) or isinstance(variable, bool):
            raise NotDelivered(f"variable reference {variable!r} (not delivered)")
        if isinstance(variable, str) and variable.startswith(".LOCAL"):
            return variable
        if variable == "":
            variable = default_variable_for_context(self.options["context"])
        variable = str(variable)
        if re.fullmatch(r"V[0-9]", variable):
            # _resolveVariableRef: V0..V9 name the arguments of a custom script.
            arg = self.options["argLookup"]["variable"].get(variable)
            if arg is None:
                raise CompileError("Cant find arg")
            return self._argument_symbol(arg)
        o = self.options
        entity, lookup, aliases = o["entity"], o["variablesLookup"], o["variableAliasLookup"]
        vid = get_variable_id(variable, entity)
        named = lookup.get(vid or "0")
        if named and named.get("symbol") and not is_variable_local(variable):
            symbol = named["symbol"].upper()
            aliases[vid] = {"symbol": symbol, "name": named.get("name"), "id": named.get("id"), "isLocal": False,
                            "entityType": "scene", "entityId": "", "sceneId": ""}
            return symbol
        existing = aliases.get(vid or "0")
        if existing:
            return existing["symbol"]
        name = ""
        is_local = is_variable_local(variable)
        if entity and is_local:
            num = to_variable_number(variable)
            local_name = (lookup.get(f"{entity['id']}__L{num}") or {}).get("name") or f"Local {num}"
            if o["entityType"] == "scene":
                name = f"S{o['sceneIndex']}_{local_name}"
            elif o["entityType"] == "actor":
                name = f"S{o['sceneIndex']}A{o['entityIndex']}_{local_name}"
            elif o["entityType"] == "trigger":
                name = f"S{o['sceneIndex']}T{o['entityIndex']}_{local_name}"
        elif is_variable_temp(variable):
            name = f"Temp {to_variable_number(variable)}"
        else:
            num = to_variable_number(variable or "0")
            name = (named or {}).get("name") or f"Variable {num}"
        alias = "VAR_" + to_asm_var(name)
        new_alias, counter = alias, 1
        used = [v["symbol"] for v in aliases.values() if v]
        while new_alias in used:
            new_alias = f"{alias}_{counter}"
            counter += 1
        aliases[vid] = {"symbol": new_alias, "id": vid, "name": name, "isLocal": is_local,
                        "entityType": o["entityType"], "entityId": (entity or {}).get("id", ""),
                        "sceneId": (o.get("scene") or {}).get("id", "")}
        return new_alias

    # -- script values (scriptBuilderBase.ts) ---------------------------------
    def _rpn(self):
        return _RPN(self)

    def _expression_to_human_readable(self, expression):
        """scriptBuilderBase.ts _expressionToHumanReadable: variables by alias,
        project constants (@uuid@) by symbol; engine constants stay as written."""
        if not isinstance(expression, str):
            expression_rpn(expression)          # the located refusal of a non-text expression
        readable = JS_WHITESPACE.sub("", expression)
        readable = re.sub(r"(\$L[0-9]\$|\$T[0-1]\$|\$[0-9]+\$)",
                          lambda m: self.get_variable_alias(m[0].replace("$", "")), readable)
        return re.sub(r"@([a-z0-9-]{36})@", lambda m: self.get_constant_symbol(m[1]), readable)

    def variable_evaluate_expression(self, variable, expression):
        """Published variableEvaluateExpression."""
        self._add_comment(f"Variable {variable} = {self._expression_to_human_readable(expression)}")
        self._stack_push_evaluated_expression(expression, variable)
        self._add_nl()

    def _stack_push_evaluated_expression(self, expression, result_variable=_UNDEFINED):
        """scriptBuilderBase.ts _stackPushEvaluatedExpression: the value stays on the
        stack (.ARG0) unless a result variable is given."""
        tokens = expression_rpn(expression)
        if not tokens:
            # "If expression empty use value 0"
            if result_variable is not _UNDEFINED:
                self._set_variable_const(result_variable, 0)
            else:
                self._stack_push_const(0)
            return
        rpn = self._rpn()
        for kind, value in tokens:
            if kind == "VAL":
                rpn.int16(value)
            elif kind == "VAR":
                rpn.ref_variable(self._resolve_variable_ref(value.replace("$", "")))
            elif kind == "CONST":
                rpn.int16(self.get_constant_symbol(value))           # rpn.intConstant
            else:
                op = OPERATORS[value] if kind == "OP" else value
                if kind == "FUN" and value == "neg":
                    raise NotDelivered("neg() in Evaluate Expression emits binary SUB in GB Studio 4.3.2; use unary '-' instead")
                # abs, atan2, isqrt: GBDK library calls in vm_rpn, replaced by
                # NGPC functions with the same results (emit.RPN_LIBRARY).
                rpn.operator(SCRIPT_OPERATORS[op])
        if result_variable is not _UNDEFINED:
            rpn.ref_set_variable(result_variable)
        rpn.stop()

    @staticmethod
    def _precompile(value):
        if not isinstance(value, dict):
            # GB Studio fails on `input.type` of a missing value (TypeError).
            raise CompileError(f"{value!r} is not a script value")
        return precompile_script_value(optimise_script_value(value))

    def _perform_fetch_operations(self, fetch_ops):
        """Published _performFetchOperations, selected position/direction reads.

        Sort and share identical reads inside one expression. No cached value
        survives the expression: a later evaluation observes the live actor.
        """
        lookup, previous = {}, None
        def key(fetch):
            value = fetch["value"]
            target = value.get("target") or "player"
            symbol = target if isinstance(target, str) else target["symbol"]
            return f"{symbol}::{value['type']}"
        for fetch in sorted(fetch_ops, key=key):
            value = fetch["value"]
            kind = value["type"]
            if kind not in ("actorPosition", "actorDirection"):
                raise NotDelivered(f"script value reading {kind} (not delivered yet)")
            target = value.get("target") or "player"
            symbol = target if isinstance(target, str) else target["symbol"]
            identity = (symbol, kind)
            if identity == previous:
                lookup[fetch["local"]] = local
                continue
            self._add_comment(f"-- Fetch {symbol} {kind}")
            if kind == "actorPosition":
                local = self._declare_local("actor_pos", 4, True)
                self.set_actor_id(local, target)
                self._add_cmd("VM_ACTOR_GET_POS", local)
            else:
                local = self._declare_local("actor_dir", 1, True)
                actor = self._declare_local("actor", 4)
                self.set_actor_id(actor, target)
                self._add_cmd("VM_ACTOR_GET_DIR", actor, local)
            lookup[fetch["local"]] = local
            previous = identity
        return lookup

    def _perform_value_rpn(self, rpn, rpn_ops, locals_lookup):
        """_performValueRPN, for the operations whose runtime is delivered."""
        for op in rpn_ops:
            kind = op.get("type")
            if kind == "number":
                rpn.int16(op.get("value") if op.get("value") is not None else 0)
            elif kind == "variable":
                rpn.ref_variable(self._resolve_variable_ref(op["value"]))
            elif kind == "direction":
                rpn.int16(to_asm_dir(op.get("value")))
            elif kind in ("memI16", "memI8", "memU8"):
                # camera_x, scroll_x...: engine variables of the camera and
                # scroll managers, not delivered (assembler.ENGINE_MEMORY).
                raise NotDelivered(f"script value reading engine variable {op.get('value')} (not delivered yet)")
            elif kind == "local":
                local = locals_lookup[op["value"]]
                self._mark_local_use(local)
                rpn.ref(self._offset_stack_addr(local, op.get("offset", 0)))
            elif kind == "constant":
                rpn.int16(self.get_constant_symbol(op["value"]))     # rpn.intConstant
            elif kind in ("numberSymbol", "indirect"):
                raise NotDelivered(f"script value operand of type {kind!r} (not delivered yet)")
            elif kind in SCRIPT_OPERATORS:
                # .ABS, .ATAN2, .ISQRT, .RND: GBDK library calls in vm_rpn,
                # replaced by NGPC functions (emit.RPN_LIBRARY, rand_support).
                rpn.operator(SCRIPT_OPERATORS[kind])
            else:
                # valueFunctionToScriptOperator: assertUnreachable.
                raise CompileError(f"script value operation {kind!r} unknown to GB Studio")

    # -- high level (scriptBuilder.ts) ----------------------------------------
    def wait_script_value(self, duration, units):
        wait_args = self._declare_local("wait_args", 1, True)
        stack_ptr = self.stack_ptr
        rpn_ops, fetch_ops = self._precompile(duration)
        if len(rpn_ops) == 1 and rpn_ops[0].get("type") == "number":
            value = rpn_ops[0]["value"]
            frames = math.ceil(value * 60) if units == "time" else value
            self._add_comment(f"Wait {js_str(frames)} frames")
            if frames < 5:
                i = 0
                while i < frames:
                    self._idle()
                    i += 1
            else:
                self._set_const(wait_args, js_round(frames))
                self._invoke("wait_frames", 0, wait_args)
        else:
            self._add_comment(f"Wait frames {units}")
            locals_lookup = self._perform_fetch_operations(fetch_ops)
            rpn = self._rpn()
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            if units == "time":
                rpn.int16(60)
                rpn.operator(".MUL")
            rpn.ref_set_variable(wait_args).stop()
            self._invoke("wait_frames", 0, wait_args)
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    def variable_set_to_script_value(self, variable, value):
        self._add_comment("Variable Set To")
        rpn_ops, fetch_ops = self._precompile(value)
        if len(rpn_ops) == 1 and rpn_ops[0].get("type") == "number":
            self._set_variable_const(variable, rpn_ops[0]["value"])
        elif len(rpn_ops) == 1 and rpn_ops[0].get("type") == "variable":
            self._set_variable_to_variable(variable, rpn_ops[0]["value"])
        else:
            locals_lookup = self._perform_fetch_operations(fetch_ops)
            self._add_comment("-- Calculate value")
            rpn = self._rpn()
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.ref_set_variable(variable).stop()
        self._add_nl()

    def variable_inc(self, variable):
        self._add_comment("Variable Increment By 1")
        self._rpn().ref_variable(variable).int8(1).operator(".ADD").ref_set_variable(variable).stop()
        self._add_nl()

    def variable_dec(self, variable):
        self._add_comment("Variable Decrement By 1")
        self._rpn().ref_variable(variable).int8(1).operator(".SUB").ref_set_variable(variable).stop()
        self._add_nl()

    def variable_add(self, variable, value):
        """scriptBuilder.ts variableAdd (used by eventReplaceTileXYSequence)."""
        self._add_comment("Variable Increment By " + js_str(value))
        self._rpn().ref_variable(variable).int8(value).operator(".ADD").ref_set_variable(variable).stop()
        self._add_nl()

    def mark_locals_used(self, *locals_):
        for local in locals_:
            self._mark_local_use(local)

    def _if_variable_cmp_variable(self, operator, variable_a, variable_b, label, pop_num):
        alias_a = self.get_variable_alias(variable_a)
        alias_b = self.get_variable_alias(variable_b)
        dest, pop = alias_b, pop_num
        if self._is_indirect_variable(variable_b):
            self._stack_push_ind(alias_b)
            dest = ".ARG1" if self._is_indirect_variable(variable_a) else ".ARG0"
            pop += 1
        if self._is_indirect_variable(variable_a):
            self._stack_push_ind(alias_a)
            self._if(operator, ".ARG0", dest, label, pop + 1)
        else:
            self._if(operator, alias_a, dest, label, pop)

    def if_variable_compare(self, variable_a, operator, variable_b, true_path=None, false_path=None):
        """scriptBuilder.ts ifVariableCompare."""
        true_label = self.get_next_label()
        end_label = self.get_next_label()
        self._add_comment(f"If Variable {operator} Variable")
        self._if_variable_cmp_variable(operator, variable_a, variable_b, true_label, 0)
        self._compile_path(false_path if false_path is not None else [])
        self._jump(end_label)
        self._label(true_label)
        self._compile_path(true_path if true_path is not None else [])
        self._label(end_label)
        self._add_nl()

    def if_variable_compare_script_value(self, variable, operator, value, true_path=None, false_path=None):
        """scriptBuilder.ts ifVariableCompareScriptValue (the `direction` branch as released)."""
        rpn_ops, fetch_ops = self._precompile(value)
        self._add_comment(f"If Variable {operator} Value")
        true_label = self.get_next_label()
        end_label = self.get_next_label()
        if len(rpn_ops) == 1 and rpn_ops[0].get("type") == "number":
            self._if_variable_const(operator, variable, rpn_ops[0]["value"], true_label, 0)
        elif len(rpn_ops) == 1 and rpn_ops[0].get("type") == "direction":
            self._if_variable_cmp_variable(operator, variable, rpn_ops[0]["value"], true_label, 0)
        else:
            self._add_comment("-- Calculate value")
            locals_lookup = self._perform_fetch_operations(fetch_ops)
            if_value_ref = self._declare_local("if_value", 1, True)
            rpn = self._rpn()
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.ref_set(if_value_ref).stop()
            self._if_variable_cmp_variable(operator, variable, if_value_ref, true_label, 0)
        self._compile_path(false_path if false_path is not None else [])
        self._jump(end_label)
        self._label(true_label)
        self._compile_path(true_path if true_path is not None else [])
        self._label(end_label)
        self._add_nl()

    # -- deprecatedAPI.ts, still compiled by released events ------------------
    def _deprecated(self, old_fn, new_fn):
        """deprecatedAPI.ts _deprecated: the released warning (WARNING_FN_DEPRECATED)."""
        command = (self.event or {}).get("command", "")
        self.warn(f'"{old_fn}" is deprecated and will be removed in a future release. '
                  f'Use "{new_fn}" instead. ({command})')

    def variable_set_to_true(self, variable):
        """deprecatedAPI.ts variableSetToTrue."""
        self._deprecated("variableSetToTrue", "variableSetToScriptValue")
        self._add_comment("Variable Set To True")
        self._set_variable_const(variable, 1)
        self._add_nl()

    def variable_set_to_false(self, variable):
        """deprecatedAPI.ts variableSetToFalse."""
        self._deprecated("variableSetToFalse", "variableSetToScriptValue")
        self._add_comment("Variable Set To False")
        self._set_variable_const(variable, 0)
        self._add_nl()

    def _if_path(self, emit_test, true_path, false_path):
        true_label = self.get_next_label()
        end_label = self.get_next_label()
        emit_test(true_label)
        self._compile_path(false_path if false_path is not None else [])
        self._jump(end_label)
        self._label(true_label)
        self._compile_path(true_path if true_path is not None else [])
        self._label(end_label)
        self._add_nl()

    def if_variable_true(self, variable, true_path=None, false_path=None):
        """deprecatedAPI.ts ifVariableTrue."""
        self._deprecated("ifVariableTrue", "ifScriptValue")
        def test(label):
            self._add_comment("If Variable True")
            self._if_variable_const(".GT", variable, 0, label, 0)
        self._if_path(test, true_path, false_path)

    def if_variable_value(self, variable, operator, value, true_path=None, false_path=None):
        """deprecatedAPI.ts ifVariableValue."""
        self._deprecated("ifVariableValue", "ifScriptValue")
        def test(label):
            self._add_comment(f"If Variable {operator} Value")
            self._if_variable_const(operator, variable, value, label, 0)
        self._if_path(test, true_path, false_path)

    def if_expression(self, expression, true_path=None, false_path=None):
        """deprecatedAPI.ts ifExpression."""
        self._deprecated("ifExpression", "ifScriptValue")
        def test(label):
            self._add_comment(f"If {self._expression_to_human_readable(expression)}")
            self._stack_push_evaluated_expression(expression)
            self._if_const(".GT", ".ARG0", 0, label, 1)
        self._if_path(test, true_path, false_path)

    # -- scriptBuilder.ts textSetAnimSpeed -------------------------------------
    def text_set_anim_speed(self, speed_in, speed_out, text_speed=_UNDEFINED, allow_fast_forward=_UNDEFINED):
        """scriptBuilder.ts textSetAnimSpeed (default parameters apply to undefined only)."""
        text_speed = 1 if text_speed is _UNDEFINED else text_speed
        allow_fast_forward = True if allow_fast_forward is _UNDEFINED else allow_fast_forward
        def speed(value, what):
            if value is _UNDEFINED or isinstance(value, bool) or value is None:
                raise CompileError(f"text {what} speed {value!r} (GB Studio would write it as is)")
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise CompileError(f"text {what} speed {value!r} is not a number") from None
            if number == -3:
                return ".OVERLAY_SPEED_INSTANT"
            if number != int(number):
                raise CompileError(f"text {what} speed {value!r} is not an integer")
            return int(number)
        if isinstance(text_speed, bool) or not isinstance(text_speed, int):
            raise CompileError(f"text draw speed {text_speed!r}: expected an integer")
        self._add_comment("Text Set Animation Speed")
        self._set_const_mem_int8("text_ff_joypad", 1 if allow_fast_forward else 0)
        self._set_const_mem_int8("text_draw_speed", text_speed)
        self._set_const_mem_int8("text_out_speed", speed(speed_out, "out"))
        self._set_const_mem_int8("text_in_speed", speed(speed_in, "in"))
        self._add_nl()

    def _palette(self, palette_id, fallback_id):
        """helpers.ts getPalette; an unknown id, which GB Studio replaces by the
        fallback or the DMG palette silently, is refused (scene_assets.get_palette)."""
        palettes = self.options.get("palettes") or {}
        for candidate in (palette_id, fallback_id):
            if candidate == "dmg":
                # The id itself (getPalette), or a fallback no palette has: DMG_PALETTE.
                return DMG_PALETTE_COLORS
            if candidate:
                if candidate not in palettes:
                    raise CompileError(f"unknown palette {candidate!r} (GB Studio would fall back silently)")
                return palettes[candidate]
        return DMG_PALETTE_COLORS

    def _palette_color(self, colors):
        """scriptBuilder.ts parseR/parseG/parseB, then _paletteColor (.CGB_PAL)."""
        def channel(color, at, minimum=0):
            return max(minimum, math.floor(int(color[at:at + 2], 16) * (32 / 256)))
        self._add_cmd(".CGB_PAL", *[channel(c, at, 1 if at == 4 else 0) for c in colors for at in (0, 2, 4)])

    def _palette_mono_refused(self):
        if (self.options.get("paletteSettings") or {}).get("colorMode") == "mono":
            raise NotDelivered("palette events in a mono project (GBVM's DMG palette path)")

    def palette_set_background(self, palette_ids):
        """scriptBuilder.ts paletteSetBackground."""
        self._palette_mono_refused()
        settings = self.options.get("paletteSettings") or {}
        defaults = settings.get("defaultBackgroundPaletteIds") or []
        scene = self.options.get("scene") or {}
        mask, writes = 0, []
        for i, palette_id in enumerate(palette_ids):
            default = defaults[i] if i < len(defaults) else ""
            if palette_id == "keep":
                continue
            if palette_id == "restore":
                if (scene.get("background") or {}).get("autoPalettes"):
                    raise NotDelivered("restore of automatic background palettes")
                ids = scene.get("paletteIds") or []
                colors = self._palette(ids[i] if i < len(ids) and ids[i] else default, default)
            else:
                colors = self._palette(palette_id, default)
            mask += 1 << i
            writes.append(colors)
        if mask == 0:
            return
        self._add_cmd("VM_LOAD_PALETTE", mask, union_flags([".PALETTE_BKG", ".PALETTE_COMMIT"]))
        for colors in writes:
            self._palette_color(colors)

    def palette_set_sprite(self, palette_ids):
        """scriptBuilder.ts paletteSetSprite: colours [c0, c0, c1, c3]."""
        self._palette_mono_refused()
        settings = self.options.get("paletteSettings") or {}
        defaults = settings.get("defaultSpritePaletteIds") or []
        scene = self.options.get("scene") or {}
        mask, writes = 0, []
        for i, palette_id in enumerate(palette_ids):
            default = defaults[i] if i < len(defaults) else ""
            if palette_id == "keep":
                continue
            if palette_id == "restore":
                ids = scene.get("spritePaletteIds") or []
                colors = self._palette(ids[i] if i < len(ids) and ids[i] else default, default)
            else:
                colors = self._palette(palette_id, default)
            mask += 1 << i
            writes.append(colors)
        if mask == 0:
            return
        self._add_cmd("VM_LOAD_PALETTE", mask, union_flags([".PALETTE_SPRITE", ".PALETTE_COMMIT"]))
        for colors in writes:
            self._palette_color((colors[0], colors[0], colors[1], colors[3]))

    def palette_set_emote(self, palette_id):
        """scriptBuilder.ts paletteSetEmote: sprite palette 7, colours [c0, c0, c1, c3]."""
        self._palette_mono_refused()
        defaults = (self.options.get("paletteSettings") or {}).get("defaultSpritePaletteIds") or []
        colors = self._palette(palette_id, defaults[7] if len(defaults) > 7 else "")
        self._add_cmd("VM_LOAD_PALETTE", 128, union_flags([".PALETTE_SPRITE", ".PALETTE_COMMIT"]))
        self._palette_color((colors[0], colors[0], colors[1], colors[3]))

    def palette_set_ui(self, palette_id):
        """scriptBuilder.ts paletteSetUI: background palette 7."""
        self._palette_mono_refused()
        defaults = (self.options.get("paletteSettings") or {}).get("defaultBackgroundPaletteIds") or []
        colors = self._palette(palette_id, defaults[7] if len(defaults) > 7 else "")
        self._add_cmd("VM_LOAD_PALETTE", 128, union_flags([".PALETTE_BKG", ".PALETTE_COMMIT"]))
        self._palette_color(colors)

    def _replace_tile_xy(self, x, y, symbol, tile_index):
        self._add_cmd("VM_REPLACE_TILE_XY", x, y, f"___bank_{symbol}", f"_{symbol}", tile_index)

    def replace_tile_xy_script_value(self, x, y, tileset_id, tile_index_value, tile_size=_UNDEFINED):
        """scriptBuilder.ts replaceTileXYScriptValue. An unknown tileset, which
        GB Studio replaces by the first one, is refused; so are non-constant
        coordinates (GB Studio reads the map with VM_GET_TILE_XY: not delivered)."""
        tilesets = self.options.get("tilesets") or []
        tileset = next((t for t in tilesets if t["id"] == tileset_id), None)
        if tileset is None:
            raise CompileError(f"tileset {tileset_id!r} unknown (GB Studio would use the first tileset)")
        rpn_x, _ = self._precompile(x)
        rpn_y, _ = self._precompile(y)
        rpn_tile, fetch_tile = self._precompile(tile_index_value)
        if not (len(rpn_x) == 1 and rpn_x[0].get("type") == "number" and
                len(rpn_y) == 1 and rpn_y[0].get("type") == "number"):
            raise NotDelivered("replace tile at computed coordinates (VM_GET_TILE_XY, not delivered)")
        tile_index = self._declare_local("tile_index", 1, True)
        self._add_comment("Replace Tile XY")
        locals_lookup = self._perform_fetch_operations(fetch_tile)
        const_x, const_y = rpn_x[0]["value"], rpn_y[0]["value"]
        rpn = self._rpn()
        self._perform_value_rpn(rpn, rpn_tile, locals_lookup)
        rpn.ref_set(tile_index)
        rpn.stop()
        if tile_size == "16px":
            self._replace_tile_xy(const_x, const_y, tileset["symbol"], tile_index)
            self._rpn().ref(tile_index).int8(1).operator(".ADD").ref_set(tile_index).stop()
            self._replace_tile_xy(const_x + 1, const_y, tileset["symbol"], tile_index)
            self._rpn().ref(tile_index).int8(tileset["width"]).operator(".ADD").ref_set(tile_index).stop()
            self._replace_tile_xy(const_x + 1, const_y + 1, tileset["symbol"], tile_index)
            self._rpn().ref(tile_index).int8(1).operator(".SUB").ref_set(tile_index).stop()
            self._replace_tile_xy(const_x, const_y + 1, tileset["symbol"], tile_index)
        else:
            self._replace_tile_xy(const_x, const_y, tileset["symbol"], tile_index)

    # -- EVENT_IF_INPUT / EVENT_AWAIT_INPUT (scriptBuilder.ts) -----------------
    def _get_mem_int8(self, addr, c_variable):
        self._add_cmd("VM_GET_INT8", addr,
                      c_variable if c_variable.startswith(("^", "_")) else f"_{c_variable}")

    def _get_mem_int16(self, addr, c_variable):
        """scriptBuilderBase.ts _getMemInt16."""
        self._add_cmd("VM_GET_INT16", addr,
                      c_variable if c_variable.startswith(("^", "_")) else f"_{c_variable}")

    def _set_to_variable(self, addr, variable):
        """scriptBuilderBase.ts _setToVariable."""
        alias = self.get_variable_alias(variable)
        if self._is_indirect_variable(variable):
            self._stack_push_ind(alias)
            self._set(addr, ".ARG0")
            self._stack_pop(1)
        else:
            self._set(addr, alias)

    # -- deprecatedAPI.ts actor variants still compiled ------------------------
    def actor_set_position_to_variables(self, variable_x, variable_y, units="tiles"):
        """deprecatedAPI.ts actorSetPositionToVariables."""
        self._deprecated("actorSetPositionToVariables", "actorSetPositionToScriptValues")
        actor_ref = self._declare_local("actor", 4)
        stack_ptr = self.stack_ptr
        self._add_comment("Actor Set Position To Variables")
        shift = vm_position_shift(units)
        (self._rpn().ref_variable(variable_x).int16(shift).operator(".SHL")
         .ref_set(self._offset_stack_addr(actor_ref, 1))
         .ref_variable(variable_y).int16(shift).operator(".SHL")
         .ref_set(self._offset_stack_addr(actor_ref, 2)).stop())
        self._actor_set_position(actor_ref)
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    def actor_move_to_variables(self, variable_x, variable_y, use_collisions, move_type="horizontal", units="tiles"):
        """deprecatedAPI.ts actorMoveToVariables (eventActorMoveToVariables.js passes
        "tiles" as useCollisions: no collision flag, as released)."""
        self._deprecated("actorMoveToVariables", "actorMoveToScriptValues")
        actor_ref = self._declare_local("actor", 4)
        stack_ptr = self.stack_ptr
        self._add_comment("Actor Move To Variables")
        shift = vm_position_shift(units)
        (self._rpn().ref_variable(variable_x).int16(shift).operator(".SHL")
         .ref_set(self._offset_stack_addr(actor_ref, 1))
         .ref_variable(variable_y).int16(shift).operator(".SHL")
         .ref_set(self._offset_stack_addr(actor_ref, 2)).stop())
        self._set_const(self._offset_stack_addr(actor_ref, 3), to_asm_move_flags(move_type, use_collisions))
        self._add_cmd("VM_ACTOR_MOVE_TO", actor_ref)
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    def actor_set_frame_to_variable(self, variable):
        """deprecatedAPI.ts actorSetFrameToVariable."""
        self._deprecated("actorSetFrameToVariable", "actorSetFrameToScriptValue")
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Set Animation Frame To Variable")
        self._set_to_variable(self._offset_stack_addr(actor_ref, 1), variable)
        self._add_cmd("VM_ACTOR_SET_ANIM_FRAME", actor_ref)
        self._add_nl()

    # -- scriptBuilder.ts ---------------------------------------------------------
    def actor_start_update(self):
        """scriptBuilder.ts actorStartUpdate."""
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Start Update Script")
        self._add_cmd("VM_ACTOR_BEGIN_UPDATE", actor_ref)
        self._add_nl()

    def overlay_hide(self):
        """scriptBuilder.ts overlayHide (VM_OVERLAY_HIDE = VM_OVERLAY_SETPOS 0, .MENU_CLOSED_Y)."""
        self._add_comment("Overlay Hide")
        self._add_cmd("VM_OVERLAY_HIDE")
        self._add_nl()

    def sprites_show(self):
        """scriptBuilder.ts spritesShow."""
        self._add_comment("Show Sprites")
        self._add_cmd("VM_SHOW_SPRITES")

    def sprites_hide(self):
        """scriptBuilder.ts spritesHide."""
        self._add_comment("Hide Sprites")
        self._add_cmd("VM_HIDE_SPRITES")

    def if_current_scene_is(self, scene_id, true_path=None, false_path=None):
        """scriptBuilder.ts ifCurrentSceneIs: _current_scene is the far pointer
        (bank, address) of the loaded scene; an unknown scene takes the else path."""
        scene = next((s for s in self.options.get("scenes") or [] if s.get("id") == scene_id), None)
        symbol = scene.get("symbol") if scene else None
        self._add_comment(f"If Current Scene Is {symbol if symbol else 'undefined'}")
        if symbol:
            false_label = self.get_next_label()
            end_label = self.get_next_label()
            bank_ref = self._declare_local("bank", 1, True)
            addr_ref = self._declare_local("addr", 1, True)
            self._get_mem_int8(bank_ref, "_current_scene")
            self._get_mem_int16(addr_ref, "^/(_current_scene+1)/")
            (self._rpn().ref(bank_ref).int8(f"___bank_{symbol}").operator(".EQ")
             .ref(addr_ref).int16(f"_{symbol}").operator(".EQ").operator(".AND").stop())
            self._if_const(".EQ", ".ARG0", 0, false_label, 1)
            self._add_nl()
            self._compile_path(true_path if true_path is not None else [])
            self._jump(end_label)
            self._label(false_label)
            self._compile_path(false_path if false_path is not None else [])
            self._label(end_label)
            self._add_nl()
        else:
            self._compile_path(false_path if false_path is not None else [])

    def _rate_limit_const(self, frames, variable, label):
        self._add_cmd("VM_RATE_LIMIT_CONST", frames, variable, f"{label}$")

    def rate_limit_const_value(self, delay, units, timer_variable, true_path=None):
        """scriptBuilder.ts rateLimitConstValue."""
        true_path = [] if true_path is None else true_path
        self._add_comment("Rate Limit")
        end_label = self.get_next_label()
        alias = self.get_variable_alias(timer_variable)
        frames = 0
        if isinstance(delay, dict) and delay.get("type") == "number":
            value = delay.get("value")
            if type(value) not in (int, float):
                raise CompileError(f"rate limit delay {value!r} is not a number")
            frames = math.ceil(value * 60) if units == "time" else value
        elif isinstance(delay, dict) and delay.get("type") == "constant":
            symbol = self.get_constant_symbol(delay["value"])
            frames = f"^/({symbol} * 60)/" if units == "time" else symbol
        if self._is_indirect_variable(timer_variable):
            self._stack_push_ind(alias)
            self._rate_limit_const(frames, ".ARG0", end_label)
        else:
            self._rate_limit_const(frames, alias, end_label)
        self._compile_path(true_path)
        self._label(end_label)
        if self._is_indirect_variable(timer_variable):
            self._set_ind(alias, ".ARG0")
            self._stack_pop(1)

    def if_device_cgb(self, true_path=None, false_path=None):
        self._if_device("is_cgb", "If Color Supported", "_is_CGB", true_path, false_path)

    def if_device_sgb(self, true_path=None, false_path=None):
        """scriptBuilder.ts ifDeviceSGB: _is_SGB is 0 on the NGPC (ECARTS 12)."""
        self._if_device("is_sgb", "If Device SGB", "_is_SGB", true_path, false_path)

    def if_device_gba(self, true_path=None, false_path=None):
        """scriptBuilder.ts ifDeviceGBA: _is_GBA is 0 on the NGPC (ECARTS 12)."""
        self._if_device("is_gba", "If Device GBA", "_is_GBA", true_path, false_path)

    def _if_device(self, local, comment, memory, true_path, false_path, value=1):
        true_path = [] if true_path is None else true_path
        false_path = [] if false_path is None else false_path
        is_cgb_ref = self._declare_local(local, 1, True)
        false_label = self.get_next_label()
        end_label = self.get_next_label()
        self._add_comment(comment)
        self._get_mem_uint8(is_cgb_ref, memory)
        self._if_const(".NE", is_cgb_ref, value, false_label, 0)
        self._add_nl()
        self._compile_path(true_path)
        self._jump(end_label)
        self._label(false_label)
        self._compile_path(false_path)
        self._label(end_label)
        self._add_nl()

    def _input_wait(self, mask):
        self._add_cmd("VM_INPUT_WAIT", mask)

    def input_await(self, input_):
        self._add_comment("Wait For Input")
        self._input_wait(input_dec(input_))
        self._add_nl()

    def if_input(self, input_, true_path=None, false_path=None):
        true_path = [] if true_path is None else true_path
        false_path = [] if false_path is None else false_path
        input_ref = self._declare_local("input", 1, True)
        true_label = self.get_next_label()
        end_label = self.get_next_label()
        self._add_comment("If Input")
        self._get_mem_int8(input_ref, "^/(_joypads + 1)/")
        self._rpn().ref(input_ref).int8(input_dec(input_)).operator(".B_AND").stop()
        self._if_const(".NE", ".ARG0", 0, true_label, 1)
        self._add_nl()
        self._compile_path(false_path)
        self._jump(end_label)
        self._label(true_label)
        self._compile_path(true_path)
        self._label(end_label)
        self._add_nl()

    # -- EVENT_VARIABLE_MATH / EVENT_RNG_SEED helpers (scriptBuilder.ts) --------
    def variable_set_to_value(self, variable, value):
        self._add_comment("Variable Set To Value")
        self._set_variable_const(variable, value)
        self._add_nl()

    def variable_copy(self, set_variable, other_variable):
        self._add_comment("Variable Copy")
        self._set_variable_to_variable(set_variable, other_variable)
        self._add_nl()

    def _randomize(self):
        self._add_cmd("VM_RANDOMIZE")

    def _rand(self, addr, min_, range_):
        self._add_cmd("VM_RAND", addr, min_, range_)

    def _rand_variable(self, variable, min_, range_):
        alias = self.get_variable_alias(variable)
        if self._is_indirect_variable(variable):
            tmp = self._declare_local("value_tmp", 1, True)
            self._rand(tmp, min_, range_)
            self._set_ind(alias, tmp)
        else:
            self._rand(alias, min_, range_)

    def variable_set_to_random(self, variable, min_, range_):
        self._add_comment("Variable Set To Random")
        self._rand_variable(variable, min_, range_)
        self._add_nl()

    def seed_rng(self):
        self._add_comment("Seed RNG")
        self._randomize()
        self._add_nl()

    def _clamped_operation(self, set_variable, operation, clamp, push_operand):
        rpn = self._rpn()
        if clamp:
            rpn.int16(0).int16(255)
        rpn.ref_variable(set_variable)
        push_operand(rpn)
        rpn.operator(operation)
        if clamp:
            rpn.operator(".MIN").operator(".MAX")
        rpn.ref_set_variable(set_variable)
        rpn.stop()
        self._add_nl()

    def variables_operation(self, set_variable, operation, other_variable, clamp):
        self._add_comment(f"Variables {operation}")
        self._clamped_operation(set_variable, operation, clamp, lambda rpn: rpn.ref_variable(other_variable))

    def variable_value_operation(self, set_variable, operation, value, clamp):
        self._add_comment(f"Variables {operation} Value")
        self._clamped_operation(set_variable, operation, clamp, lambda rpn: rpn.int16(value))

    def variable_random_operation(self, variable, operation, min_, range_, clamp):
        rand_ref = self._declare_local("random_var", 1, True)
        self._add_comment(f"Variables {operation} Random")
        self._rand(rand_ref, min_, range_)
        self._clamped_operation(variable, operation, clamp, lambda rpn: rpn.ref(rand_ref))

    # -- memory writes (scriptBuilderBase.ts) and platformer / engine fields ----
    def _set_const_mem_int16(self, c_variable, value):
        self._add_dependency(c_variable)
        self._add_cmd("VM_SET_CONST_INT16", f"_{c_variable}", value)

    def _set_mem_int8(self, c_variable, addr):
        self._add_dependency(c_variable)
        self._add_cmd("VM_SET_INT8", f"_{c_variable}", addr)

    def _set_mem_int16(self, c_variable, addr):
        self._add_dependency(c_variable)
        self._add_cmd("VM_SET_INT16", f"_{c_variable}", addr)

    def _set_mem_to_variable(self, c_variable, variable, wide):
        """_setMemInt8ToVariable / _setMemInt16ToVariable."""
        alias = self.get_variable_alias(variable)
        self._add_dependency(c_variable)
        write = self._set_mem_int16 if wide else self._set_mem_int8
        if self._is_indirect_variable(variable):
            self._stack_push_ind(alias)
            write(c_variable, ".ARG0")
            self._stack_pop(1)
        else:
            write(c_variable, alias)

    def player_bounce(self, height):
        """scriptBuilder.ts playerBounce: platformer scenes only (pxToSubpxVel)."""
        if (self.options.get("scene") or {}).get("type") == "PLATFORM":
            self._add_comment("Player Bounce")
            value = -0x400 * 16
            if height == "low":
                value = -0x200 * 16
            elif height == "high":
                value = -0x600 * 16
            self._set_const_mem_int16("plat_vel_y", value)
            self._add_nl()

    def _engine_field_wide(self, key):
        """is16BitCType, ADAPTED: the platformer's UBYTE/BYTE engine fields are
        16-bit on the NGPC (scene_platform.py: cc900 does not promote bytes), so
        they are written with the 16-bit commands (a byte write would lose the
        sign of a BYTE). Same values, different command width."""
        from ..scene_platform import widened_fields
        from ..scene_genres import widened_fields as genre_widened
        return (self._engine_fields()[key]["cType"] in ("WORD", "UWORD") or key in widened_fields()
                or key in genre_widened()
                # A translated platformer's byte fields (emit.py, only with translated plugins).
                or ("translatedWidenedFields" in self.options and key in self.options["translatedWidenedFields"]))

    def _engine_fields(self):
        """options.engineFields (compileData: engine.json fields), from the frozen model."""
        if "engineFields" in self.options:
            return self.options["engineFields"]
        from ..project import load_model
        return load_model()["engineFields"]

    def engine_field_set_to_script_value(self, key, value):
        """scriptBuilder.ts engineFieldSetToScriptValue."""
        fields = self._engine_fields()
        if key not in fields:
            return
        wide = self._engine_field_wide(key)
        self._add_comment("Engine Field Set To Value")
        rpn_ops, fetch_ops = self._precompile(value)
        if len(rpn_ops) == 1 and rpn_ops[0].get("type") == "number":
            (self._set_const_mem_int16 if wide else self._set_const_mem_int8)(key, rpn_ops[0]["value"])
        elif len(rpn_ops) == 1 and rpn_ops[0].get("type") == "variable":
            self._set_mem_to_variable(key, rpn_ops[0]["value"], wide)
        else:
            ref = self._declare_local("engine_field_val", 1, True)
            locals_lookup = self._perform_fetch_operations(fetch_ops)
            self._add_comment("-- Calculate value")
            rpn = self._rpn()
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.ref_set_variable(ref).stop()
            self._set_mem_to_variable(key, ref, wide)
        self._add_nl()

    def camera_set_property_to_script_value(self, prop=_UNDEFINED, value=None):
        """scriptBuilder.ts cameraSetPropertyToScriptValue: a BYTE of camera.c,
        deadzones clamped to 0..40 (_setMemToScriptValue, BYTE)."""
        from .assembler import CAMERA_PROPERTIES
        prop = "camera_deadzone_x" if prop is _UNDEFINED else prop
        if prop not in CAMERA_PROPERTIES:
            raise CompileError(f"camera property {prop!r}: expected one of {', '.join(CAMERA_PROPERTIES)}")
        if not isinstance(value, dict):
            raise CompileError(f"{value!r} is not a script value")
        self._add_comment(f"Camera Set Property {prop}")
        if prop in ("camera_deadzone_x", "camera_deadzone_y"):
            value = {"type": "max", "valueA": {"type": "number", "value": 0},
                     "valueB": {"type": "min", "valueA": {"type": "number", "value": 40}, "valueB": value}}
        rpn_ops, fetch_ops = self._precompile(value)
        if len(rpn_ops) == 1 and rpn_ops[0].get("type") == "number":
            self._set_const_mem_int8(prop, rpn_ops[0]["value"])
        elif len(rpn_ops) == 1 and rpn_ops[0].get("type") == "variable":
            self._set_mem_to_variable(prop, rpn_ops[0]["value"], False)
        else:
            ref = self._declare_local("mem_set_value", 1, True)
            locals_lookup = self._perform_fetch_operations(fetch_ops)
            self._add_comment("-- Calculate value")
            rpn = self._rpn()
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.ref_set_variable(ref).stop()
            self._set_mem_to_variable(prop, ref, False)
        self._add_nl()

    def scene_update_pause(self):
        """scriptBuilder.ts sceneUpdatePause."""
        self._add_comment("Pause Scene Type Update")
        self._set_const_mem_int8("pause_state_update", 1)
        self._add_nl()

    def scene_update_resume(self):
        """scriptBuilder.ts sceneUpdateResume."""
        self._add_comment("Resume Scene Type Update")
        self._set_const_mem_int8("pause_state_update", 0)
        self._add_nl()

    def engine_field_store_in_variable(self, key, variable):
        """scriptBuilder.ts engineFieldStoreInVariable: the field's memory read
        into the variable, 16-bit for 16-bit fields (and the platformer fields
        widened on the NGPC, _engine_field_wide), else signed 8-bit as GBVM."""
        # engineField.key is always set in GB Studio (the frozen model keys by it).
        if key not in self._engine_fields():
            return
        self._add_comment("Engine Field Store In Variable")
        read = self._get_mem_int16 if self._engine_field_wide(key) else self._get_mem_int8
        alias = self.get_variable_alias(variable)
        if self._is_indirect_variable(variable):
            value_tmp = self._declare_local("value_tmp", 1, True)
            read(value_tmp, key)
            self._set_ind(alias, value_tmp)
        else:
            read(alias, key)
        self._add_nl()

    def engine_field_set_to_default(self, key):
        """scriptBuilder.ts engineFieldSetToDefault: defaultValue || 0 (booleans as numbers)."""
        fields = self._engine_fields()
        if key not in fields:
            return
        default = fields[key]["defaultValue"]
        value = int(default) if isinstance(default, bool) else (default or 0)
        self._add_comment("Engine Field Set To Default")
        (self._set_const_mem_int16 if self._engine_field_wide(key) else self._set_const_mem_int8)(key, value)
        self._add_nl()

    # -- actors (scriptBuilder.ts, scriptBuilderBase.ts) -----------------------
    def get_actor_index(self, actor_id):
        """scriptBuilderBase.getActorIndex. An id that is not in the scene falls
        back to the current entity (index 0, the player, when it is not an
        actor of the scene), else the player: reproduced with a located
        warning, as decided for A9 (gbs2's turnip prefab relies on it)."""
        entity, entity_type = self.options.get("entity"), self.options["entityType"]
        actors = (self.options.get("scene") or {}).get("actors") or []

        def index_of(aid):
            return next((i + 1 for i, a in enumerate(actors) if a["id"] == aid), 0)
        if actor_id == "player" or (actor_id == "$self$" and entity_type != "actor"):
            return 0
        if actor_id == "$self$" and entity:
            return index_of(entity["id"])
        index = index_of(actor_id)
        if index == 0:
            index = index_of(entity["id"]) if entity else 0
            what = "the player" if index == 0 else f"the current actor {entity['symbol'] if 'symbol' in entity else entity['id']}"
            if "warnings" in self.options:
                self.warn(f"actor {actor_id!r} is not in this scene: {what} is used, as GB Studio does")
            else:
                raise CompileError(f"actor {actor_id!r} is not in this scene ({what} would be used): "
                                   "no warning list to report it")
        return index

    def unset_actor_index(self):
        """scriptBuilderBase.getActorIndex("") for a custom event argument left
        unset (REPRISE A9, reproduced and warned): "" is in no scene, so the
        current entity when it is an actor of the scene, else 0 (the player).
        Returns (index, what the warning calls it)."""
        entity = self.options.get("entity")
        actors = (self.options.get("scene") or {}).get("actors") or []
        if entity:
            for i, a in enumerate(actors):
                if a["id"] == entity["id"]:
                    return i + 1, f"the current actor {entity['symbol']}"
        return 0, "the player"

    def warn(self, message):
        """A located warning of the export (export.json "warnings")."""
        self.options["warnings"].append(f"{self.source}: {message}" if self.source else message)

    def resolve_actor_id(self, actor_id):
        if isinstance(actor_id, int) and not isinstance(actor_id, bool):
            return ("number", actor_id)
        if isinstance(actor_id, str):
            if actor_id.startswith("."):
                return ("reference", actor_id)
            return ("number", self.get_actor_index(actor_id))
        if self._is_function_arg(actor_id):
            return ("reference", actor_id["symbol"])      # resolveActorId: { symbol: id.symbol }
        raise NotDelivered(f"actor reference {actor_id!r} (not delivered)")

    def set_actor_id(self, addr, actor_id):
        kind, value = self.resolve_actor_id(actor_id)
        if kind == "number":
            self.actor_index = value
            self._set_const(addr, value)
        else:
            self.actor_index = -1
            self._set(addr, value)

    def actor_set_by_id(self, actor_id):
        self.set_actor_id(self._declare_local("actor", 4), actor_id)

    def actor_set_active(self, actor_id):
        self._add_comment("Actor Set Active")
        self.actor_set_by_id(actor_id)
        self._add_nl()

    # -- EVENT_ACTOR_EFFECTS (scriptBuilder.ts actorFXSplitIn / SplitOut / Flicker) --
    def _fx_steps(self, distance, speed, units):
        if units not in ("pixels", "tiles"):
            raise CompileError(f"Actor Effects: units {units!r}: expected pixels or tiles")
        for name, value in (("distance", distance), ("speed", speed)):
            if type(value) not in (int, float) or isinstance(value, bool) or value != value:
                raise CompileError(f"Actor Effects: {name} {value!r} is not a number")
        pixel_distance = distance * (8 if units == "tiles" else 1)
        if not speed or math.floor(pixel_distance / speed) <= 0:
            raise CompileError(f"Actor Effects: {pixel_distance} px at speed {speed}: no step "
                               "(GB Studio would divide by zero and emit Infinity)")
        steps = math.floor(pixel_distance / speed)
        # Wide world: whole pixels per step (ECARTS_GBSTUDIO.md; same end position).
        return steps, vm_position_px(math.floor(pixel_distance * 32 / steps))

    def actor_fx_split_in(self, actor_id, distance, speed, units="pixels"):
        steps, delta = self._fx_steps(distance, speed, units)
        actor_ref = self._declare_local("actor", 4)
        loop_ref = self._declare_local("loop", 1, True)
        final_x = self._declare_local("final_x", 1, True)
        loop_label = self.get_next_label()
        self._add_comment("Actor Effect : Split In")
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_ACTOR_ACTIVATE", actor_ref)
        self._add_cmd("VM_SET_CONST", loop_ref, steps)
        self._add_cmd("VM_ACTOR_GET_POS", actor_ref)
        self._add_cmd("VM_SET", final_x, self._offset_stack_addr(actor_ref, 1))
        self._label(loop_label)
        for op in (".ADD", ".SUB"):
            self._rpn().ref(final_x).ref(loop_ref).int16(delta).operator(".MUL").operator(op) \
                .ref_set(self._offset_stack_addr(actor_ref, 1)).stop()
            self._add_cmd("VM_ACTOR_SET_POS", actor_ref)
            self._idle()
        self._add_cmd("VM_LOOP", loop_ref, f"{loop_label}$", 0)

    def actor_fx_split_out(self, actor_id, distance, speed, units="pixels"):
        steps, delta = self._fx_steps(distance, speed, units)
        actor_ref = self._declare_local("actor", 4)
        loop_ref = self._declare_local("loop", 1, True)
        final_x = self._declare_local("final_x", 1, True)
        loop_label = self.get_next_label()
        self._add_comment("Actor Effect : Split Out")
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_SET_CONST", loop_ref, steps)
        self._add_cmd("VM_ACTOR_GET_POS", actor_ref)
        self._add_cmd("VM_SET", final_x, self._offset_stack_addr(actor_ref, 1))
        self._label(loop_label)
        for op in (".ADD", ".SUB"):
            self._rpn().ref(final_x).int16(steps).ref(loop_ref).operator(".SUB").int16(delta).operator(".MUL") \
                .operator(op).ref_set(self._offset_stack_addr(actor_ref, 1)).stop()
            self._add_cmd("VM_ACTOR_SET_POS", actor_ref)
            self._idle()
        self._add_cmd("VM_LOOP", loop_ref, f"{loop_label}$", 0)
        self._rpn().ref(final_x).ref_set(self._offset_stack_addr(actor_ref, 1)).stop()
        self._add_cmd("VM_ACTOR_SET_POS", actor_ref)
        self._add_cmd("VM_ACTOR_DEACTIVATE", actor_ref)

    def actor_fx_flicker(self, actor_id, frames):
        if frames == 0:
            return
        steps = math.ceil(frames / 4)
        actor_ref = self._declare_local("actor", 4)
        loop_ref = self._declare_local("loop", 1, True)
        loop_label = self.get_next_label()
        self._add_comment("Actor Effect : Flicker")
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_SET_CONST", loop_ref, steps)
        self._label(loop_label)
        self._add_cmd("VM_ACTOR_SET_HIDDEN", actor_ref, 1)
        self._idle()
        self._idle()
        self._add_cmd("VM_ACTOR_SET_HIDDEN", actor_ref, 0)
        self._idle()
        self._idle()
        self._add_cmd("VM_LOOP", loop_ref, f"{loop_label}$", 0)

    def actor_invoke(self):
        """scriptBuilder.ts actorInvoke: far call to the actor's interact script.
        GB Studio 4.3.2 reads scene.actors[actorIndex], one past the actor
        (actorIndex counts the player as 0): it calls the NEXT actor's script.
        NgpCraft calls the named actor's, the event's meaning (ECARTS 25); the
        player has no interact script. The called script ends with VM_STOP, so
        the calling script stops there, as in GB Studio."""
        actors = (self.options.get("scene") or {}).get("actors") or []
        index = self.actor_index - 1
        actor = actors[index] if 0 <= index < len(actors) else None
        if actor and actor.get("script"):
            self.warn("Invoke Actor Script calls the named actor's interact script; GB Studio 4.3.2 "
                      "calls the next actor's (ECARTS 25)")
            self._add_comment("Invoke Actor Interact Script")
            self._call_far(f"{actor['symbol']}_interact", 0)

    def actor_set_bound_to_script_values(self, actor_id, value_left, value_top, value_width, value_height):
        """scriptBuilder.ts actorSetBoundToScriptValues: left, right = left +
        width - 1, top, bottom = top + height - 1, in subpixels (1/32 px)."""
        for value in (value_left, value_top, value_width, value_height):
            if not isinstance(value, dict):
                raise CompileError(f"{value!r} is not a script value")
        stack_ptr = self.stack_ptr
        actor_ref = self._declare_local("actor", 5)
        self._add_comment("Actor Set Bounds")

        def sub_one(value):
            return {"type": "sub", "valueA": value, "valueB": {"type": "number", "value": 1}}

        def add(a, b):
            return {"type": "add", "valueA": a, "valueB": b}
        sides = (("left", self._to_subpixels(value_left, "pixels")),
                 ("right", sub_one(self._to_subpixels(add(value_left, value_width), "pixels"))),
                 ("top", self._to_subpixels(value_top, "pixels")),
                 ("bottom", sub_one(self._to_subpixels(add(value_top, value_height), "pixels"))))
        compiled = [precompile_script_value(optimise_script_value(value), label) for label, value in sides]
        locals_lookup = self._perform_fetch_operations([op for _, fetch in compiled for op in fetch])
        rpn = self._rpn()
        self._add_comment("-- Calculate bounds values")
        for k, (rpn_ops, _) in enumerate(compiled, 1):
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.ref_set(self._offset_stack_addr(actor_ref, k))
        rpn.stop()
        self._add_comment("-- Set Bounds")
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_ACTOR_SET_BOUNDS", actor_ref)
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    def get_projectile_index(self, args):
        """scriptBuilderBase.ts getProjectileIndex: position in the scene's list
        (projectiles.scene_registry, built before any script is compiled)."""
        registries, scene = self.options.get("projectiles"), self.options.get("scene")
        if registries is None or scene is None or scene["id"] not in registries:
            raise CompileError(f"{self.source}: no projectile list for this scene")
        index = registries[scene["id"]].index(args, self.source)
        if index < 0:
            raise CompileError(f"{self.source}: projectile type absent from the scene's scan")
        return index

    def load_projectile(self, index, args):
        """scriptBuilder.ts loadProjectile: getGlobalProjectile, then
        VM_PROJECTILE_LOAD_TYPE dest, src, ___bank_<table>, _<table>. Tables of
        five, shared by the whole project (options.globalProjectiles), entries
        identified by their fields as toProjectileHash does. The slot is checked
        here: GBVM writes past its five definitions (ECARTS 26)."""
        from ..projectiles import FIELDS
        if type(index) is not int or not 0 <= index < 5:
            raise CompileError(f"projectile slot {index!r}: expected 0..4 (MAX_PROJECTILE_DEFS)")
        tables = self.options.setdefault("globalProjectiles", [])
        entry = {k: args[k] for k in FIELDS if k in args}
        found = next(((t["symbol"], i) for t in tables for i, p in enumerate(t["projectiles"]) if p == entry), None)
        if found is None:
            if tables and len(tables[-1]["projectiles"]) < 5:
                tables[-1]["projectiles"].append(entry)
                found = (tables[-1]["symbol"], len(tables[-1]["projectiles"]) - 1)
            else:
                symbol = self._get_available_symbol(f"global_projectiles_{len(tables)}")
                tables.append({"symbol": symbol, "projectiles": [entry]})
                found = (symbol, 0)
        symbol, src = found
        self._add_comment("Load Projectile Into Slot")
        self._add_cmd("VM_PROJECTILE_LOAD_TYPE", index, src, f"___bank_{symbol}", f"_{symbol}")
        self._add_nl()

    def _projectile_args(self, index, x, y):
        # The frozen VM macro carries a byte index and signed word offsets.
        if type(index) is not int or not 0 <= index <= 255:
            raise CompileError(f"{self.source}: projectile index {index!r} does not fit VM_PROJECTILE_LAUNCH")
        for name, value in (("x", x), ("y", y)):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise CompileError(f"{self.source}: projectile {name} must be finite")
            scaled = value * (32 if name == "x" else -32)
            if not math.isfinite(scaled) or not -32768 <= math.floor(scaled) <= 32767:
                raise CompileError(f"{self.source}: projectile {name} offset does not fit a signed word")

    def _rpn_projectile_pos_args(self, actor_ref, x=0, y=0):
        self._add_cmd("VM_ACTOR_GET_POS", actor_ref)
        rpn = self._rpn().ref(self._offset_stack_addr(actor_ref, 1))
        if x:
            rpn.int16(vm_position_px(math.floor(x * 32))).operator(".ADD")
        rpn.ref(self._offset_stack_addr(actor_ref, 2))
        if y:
            rpn.int16(vm_position_px(math.floor(-y * 32))).operator(".ADD")
        return rpn

    def _projectile_finish(self, index):
        self._add_cmd("VM_PROJECTILE_LAUNCH", index, ".ARG2")
        self._stack_pop(3)
        self._add_nl()

    def launch_projectile_in_direction(self, index, x=0, y=0, direction="right"):
        self._projectile_args(index, x, y)
        angles = {"up": 0, "right": 64, "down": 128, "left": 192}
        if direction not in angles:
            raise CompileError(f"{self.source}: projectile direction {direction!r} unknown")
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Launch Projectile In Direction")
        self._rpn_projectile_pos_args(actor_ref, x, y).int16(angles[direction]).stop()
        self._projectile_finish(index)

    def launch_projectile_in_angle(self, index, x=0, y=0, angle=0):
        self._projectile_args(index, x, y)
        if type(angle) not in (int, float) or not math.isfinite(angle):
            raise CompileError(f"{self.source}: projectile angle must be finite")
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Launch Projectile In Angle")
        # JavaScript remainder has the dividend's sign (Python % differs).
        self._rpn_projectile_pos_args(actor_ref, x, y).int16(js_round(math.fmod(angle, 256))).stop()
        self._projectile_finish(index)

    def launch_projectile_in_angle_variable(self, index, x=0, y=0, angle_variable="0"):
        self._projectile_args(index, x, y)
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Launch Projectile In Angle")
        self._rpn_projectile_pos_args(actor_ref, x, y).ref_variable(angle_variable).stop()
        self._projectile_finish(index)

    def launch_projectile_in_source_actor_direction(self, index, x=0, y=0):
        self._projectile_args(index, x, y)
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Launch Projectile In Source Actor Direction")
        self._rpn_projectile_pos_args(actor_ref, x, y).int16(0).stop()
        self._add_cmd("VM_ACTOR_GET_ANGLE", actor_ref, ".ARG0")
        self._projectile_finish(index)

    def launch_projectile_in_actor_direction(self, index, x=0, y=0, actor_id="$self$"):
        self._projectile_args(index, x, y)
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Launch Projectile In Actor Direction")
        self._rpn_projectile_pos_args(actor_ref, x, y).int16(0).stop()
        self.set_actor_id(".ARG0", actor_id)
        self._add_cmd("VM_ACTOR_GET_ANGLE", ".ARG0", ".ARG0")
        self._projectile_finish(index)

    def launch_projectile_towards_actor(self, index, x=0, y=0, other_actor_id="$self$"):
        self._projectile_args(index, x, y)
        actor_ref = self._declare_local("actor", 4)
        other_ref = self._declare_local("other_actor", 3, True)
        self._add_comment("Launch Projectile Towards Actor")
        self.set_actor_id(other_ref, other_actor_id)
        self._add_cmd("VM_ACTOR_GET_POS", other_ref)
        rpn = self._rpn_projectile_pos_args(actor_ref, x, y)
        # Aim uses source positions, before the launch offsets, as GB Studio.
        for offset in (2, 1):
            rpn.ref(self._offset_stack_addr(other_ref, offset)).ref(self._offset_stack_addr(actor_ref, offset))
            rpn.operator(".SUB").int16(vm_position_px(256)).operator(".DIV")
        rpn.operator(".ATAN2").stop()
        self._projectile_finish(index)
    def actor_emote(self, emote_id):
        emote = next((e for e in self.options.get("emotes", ()) if e["id"] == emote_id), None)
        if emote is None:
            raise CompileError(f"emote {emote_id!r} unknown (GB Studio would silently ignore it)")
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Emote")
        self._add_cmd("VM_ACTOR_EMOTE", actor_ref, "___bank_" + emote["symbol"], "_" + emote["symbol"])
        self._add_nl()

    def actor_set_sprite(self, sprite_sheet_id):
        """scriptBuilder.ts actorSetSprite; unknown sheets are localized errors."""
        sprite = next((s for s in self.options.get("sprites", ()) if s["id"] == sprite_sheet_id), None)
        if sprite is None:
            raise CompileError(f"sprite sheet {sprite_sheet_id!r} unknown (GB Studio would silently ignore it)")
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Set Spritesheet")
        self._add_cmd("VM_ACTOR_SET_SPRITESHEET", actor_ref,
                      "___bank_" + sprite["symbol"], "_" + sprite["symbol"])
        self._add_nl()

    def actor_set_state(self, state, anim_loop=_UNDEFINED):
        # scriptBuilder.ts actorSetState: statesOrder is global, while the
        # sheet's animations_lookup maps absent states to the default set.
        from .text import js_truthy
        order = self.options.get("statesOrder", [])
        if state not in order:
            raise CompileError(f"animation state {state!r} is not in the project's sprite states")
        actor_ref = self._declare_local("actor", 4)
        state_index = order.index(state)
        references = self.options["stateReferences"]
        self._add_comment("Actor Set Animation State")
        self._add_cmd("VM_ACTOR_SET_ANIM_SET", actor_ref, references[state_index])
        self._add_cmd("VM_ACTOR_SET_FLAGS", actor_ref,
                      0 if anim_loop is _UNDEFINED or js_truthy(anim_loop) else 4, 4)
        self._add_nl()

    def actor_stop_update(self):
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Stop Update Script")
        self._add_cmd("VM_ACTOR_TERMINATE_UPDATE", actor_ref)
        self._add_nl()

    def actor_get_direction(self, variable):
        """Published actorGetDirection / _actorGetDirectionToVariable.

        A custom event reference is an indirect word, so use a temporary
        destination then VM_SET_INDIRECT, exactly as the original builder.
        """
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Store Direction In Variable")
        alias = self.get_variable_alias(variable)
        if self._is_indirect_variable(variable):
            destination = self._declare_local("dir_dest_var", 1, True)
            self._add_cmd("VM_ACTOR_GET_DIR", actor_ref, destination)
            self._set_ind(alias, destination)
        else:
            self._add_cmd("VM_ACTOR_GET_DIR", actor_ref, alias)
        self._add_nl()

    def variable_add_flags(self, variable, flags):
        """Released variableAddFlags, with indirect references preserved."""
        self._add_comment("Variable Add Flags")
        self._rpn().ref_variable(variable).int16(flags).operator(".B_OR").ref_set_variable(variable).stop()
        self._add_nl()

    def variable_clear_flags(self, variable, flags):
        """Released variableClearFlags: mask complement is a 16-bit XOR."""
        self._add_comment("Variable Clear Flags")
        self._rpn().ref_variable(variable).int16(-1).int16(flags).operator(".B_XOR").operator(
            ".B_AND").ref_set_variable(variable).stop()
        self._add_nl()

    def if_variable_bitwise_value(self, variable, operator, flags, true_path=None, false_path=None):
        """Released ifVariableBitwiseValue, false branch first and one stack pop."""
        true_label,end_label=self.get_next_label(),self.get_next_label()
        self._add_comment(f"If Variable {operator} Value")
        self._rpn().ref_variable(variable).int16(flags).operator(operator).stop()
        self._if_const(".NE", ".ARG0", 0, true_label, 1)
        self._compile_path(false_path)
        self._jump(end_label)
        self._label(true_label)
        self._compile_path(true_path)
        self._label(end_label)
        self._add_nl()

    def player_set_sprite(self, sprite_sheet_id):
        """Released playerSetSprite: select actor zero, then the existing macro."""
        sprite = next((s for s in self.options.get("sprites", ()) if s["id"] == sprite_sheet_id), None)
        if sprite is None:
            raise CompileError(f"sprite sheet {sprite_sheet_id!r} unknown (GB Studio would silently ignore it)")
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Player Set Spritesheet")
        self._set_const(actor_ref, 0)
        self._add_cmd("VM_ACTOR_SET_SPRITESHEET", actor_ref,
                      "___bank_" + sprite["symbol"], "_" + sprite["symbol"])
        self._add_nl()

    def actor_get_position(self, variable_x, variable_y, units=_UNDEFINED):
        """Published actorGetPosition: unsigned position shifted before stores."""
        units = "tiles" if units is _UNDEFINED else units
        if units not in ("tiles", "pixels"):
            raise CompileError(f"actor position units {units!r} must be tiles or pixels")
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Store Position In Variables")
        self._add_cmd("VM_ACTOR_GET_POS", actor_ref)
        rpn = self._rpn()
        for offset, variable in ((1, variable_x), (2, variable_y)):
            rpn.ref(self._offset_stack_addr(actor_ref, offset)).int8(vm_position_shift(units))
            rpn.operator(".SHR").ref_set_variable(variable)
        rpn.stop()
        self._add_nl()

    def actor_set_position_relative_by_script_values(self, actor_id, value_x, value_y, units=_UNDEFINED):
        """Published helper quantizes the current position BEFORE adding offsets."""
        units = "tiles" if units is _UNDEFINED else units
        if units not in ("tiles", "pixels"):
            raise CompileError(f"actor position units {units!r} must be tiles or pixels")
        values = []
        for axis, value in (("x", value_x), ("y", value_y)):
            if not isinstance(value, dict):
                raise CompileError(f"{value!r} is not a script value")
            prop = axis + "pos" if units == "tiles" else "p" + axis + "pos"
            values.append({"type": "add", "valueA": {"type": "property", "target": actor_id,
                           "property": prop}, "valueB": value})
        self.actor_set_position_to_script_values(actor_id, values[0], values[1], units)

    def if_actor_direction_script_value(self, actor_id, direction_value, true_path=None, false_path=None):
        """Published ifActorDirectionScriptValue, including read-before-fetch order."""
        actor_ref = self._declare_local("actor", 4)
        direction_ref = self._declare_local("actor_dir", 1, True)
        false_label, end_label = self.get_next_label(), self.get_next_label()
        self._add_comment("If Actor Facing Direction")
        rpn_ops, fetch_ops = precompile_script_value(optimise_script_value(direction_value))
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_ACTOR_GET_DIR", actor_ref, direction_ref)
        lookup = self._perform_fetch_operations(fetch_ops)
        rpn = self._rpn()
        self._perform_value_rpn(rpn, rpn_ops, lookup)
        rpn.stop()
        self._if(".NE", direction_ref, ".ARG0", false_label, 1)
        self._add_nl()
        self._compile_path(true_path)
        self._jump(end_label)
        self._label(false_label)
        self._compile_path(false_path)
        self._label(end_label)
        self._add_nl()

    def actor_push(self, continue_until_collision=_UNDEFINED):
        """scriptBuilder.ts actorPush, including signed RPN MAX and word wrap."""
        from .text import js_truthy
        actor_ref = self._declare_local("actor", 4)
        direction = self._declare_local("push_dir_var", 1, True)
        stack_ptr = self.stack_ptr
        up, left, right, end = [self.get_next_label() for _ in range(4)]
        offset = (100 if continue_until_collision is not _UNDEFINED and js_truthy(continue_until_collision) else 2) << 8
        self._add_comment("Actor Push")
        self._set_const(actor_ref, 0)
        self._add_cmd("VM_ACTOR_GET_DIR", actor_ref, direction)
        self._set_const(actor_ref, self.actor_index)
        self._add_cmd("VM_ACTOR_GET_POS", actor_ref)
        for value, label in ((".DIR_UP", up), (".DIR_LEFT", left), (".DIR_RIGHT", right)):
            self._if_const(".EQ", direction, value, label, 0)
        for label, axis, op in ((None, 2, ".ADD"), (up, 2, ".SUB"), (left, 1, ".SUB"), (right, 1, ".ADD")):
            if label is not None:
                self._label(label)
            dest = self._offset_stack_addr(actor_ref, axis)
            rpn = self._rpn().ref(dest).int16(vm_position_px(offset)).operator(op)
            if op == ".SUB":
                rpn.int16(0).operator(".MAX")
            rpn.ref_set(dest).stop()
            if label != right:
                self._jump(end)
        self._label(end)
        self._set_const(self._offset_stack_addr(actor_ref, 3), ".ACTOR_ATTR_CHECK_COLL")
        self._add_cmd("VM_ACTOR_MOVE_TO", actor_ref)
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    def actor_move_cancel(self):
        actor_ref = self._declare_local("actor", 4)
        self._add_cmd("VM_ACTOR_MOVE_CANCEL", actor_ref)
        self._add_nl()

    def _actor_one(self, comment, actor_id, emit):
        actor_ref = self._declare_local("actor", 4)
        self._add_comment(comment)
        self.actor_set_by_id(actor_id)
        emit(actor_ref)
        self._add_nl()

    def actor_show(self, actor_id):
        self._actor_one("Actor Show", actor_id, lambda r: self._add_cmd("VM_ACTOR_SET_HIDDEN", r, 0))

    def actor_hide(self, actor_id):
        self._actor_one("Actor Hide", actor_id, lambda r: self._add_cmd("VM_ACTOR_SET_HIDDEN", r, 1))

    def actor_activate(self, actor_id):
        self._actor_one("Actor Activate", actor_id, lambda r: self._add_cmd("VM_ACTOR_ACTIVATE", r))

    def actor_deactivate(self, actor_id):
        self._actor_one("Actor Deactivate", actor_id, lambda r: self._add_cmd("VM_ACTOR_DEACTIVATE", r))

    def actor_set_collisions(self, enabled):
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Set Collisions")
        self._add_cmd("VM_ACTOR_SET_COLL_ENABLED", actor_ref, 1 if enabled else 0)
        self._add_nl()

    def actor_set_movement_speed(self, speed=_UNDEFINED):
        speed = 1 if speed is _UNDEFINED else speed
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Set Movement Speed")
        self._add_cmd("VM_ACTOR_SET_MOVE_SPEED", actor_ref, _js_floor(speed * 32, "movement speed"))
        self._add_nl()

    def actor_set_animation_speed(self, speed=_UNDEFINED):
        speed = 3 if speed is _UNDEFINED else speed
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Set Animation Tick")
        self._add_cmd("VM_ACTOR_SET_ANIM_TICK", actor_ref, speed)
        self._add_nl()

    def actor_set_position_to_script_values(self, actor_id, value_x, value_y, units=_UNDEFINED):
        units = "tiles" if units is _UNDEFINED or units is None else units
        actor_ref = self._declare_local("actor", 4)
        stack_ptr = self.stack_ptr
        self._add_comment("Actor Set Position")
        shift = vm_position_shift(units)
        for value in (value_x, value_y):
            if not isinstance(value, dict):
                raise CompileError(f"{value!r} is not a script value")
        rpn_x, fetch_x = precompile_script_value(
            optimise_script_value({"type": "shl", "valueA": value_x, "valueB": {"type": "number", "value": shift}}), "x")
        rpn_y, fetch_y = precompile_script_value(
            optimise_script_value({"type": "shl", "valueA": value_y, "valueB": {"type": "number", "value": shift}}), "y")
        locals_lookup = self._perform_fetch_operations(fetch_x + fetch_y)
        rpn = self._rpn()
        self._add_comment("-- Calculate coordinate values")
        self._perform_value_rpn(rpn, rpn_x, locals_lookup)
        rpn.ref_set(self._offset_stack_addr(actor_ref, 1))
        self._perform_value_rpn(rpn, rpn_y, locals_lookup)
        rpn.ref_set(self._offset_stack_addr(actor_ref, 2))
        rpn.stop()
        self._add_comment("-- Position Actor")
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_ACTOR_SET_POS", actor_ref)
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    @staticmethod
    def _to_subpixels(value, units):
        """helpers.ts scriptValueToSubpixels (shiftLeftScriptValueConst)."""
        if not isinstance(value, dict):
            raise CompileError(f"{value!r} is not a script value")
        return {"type": "shl", "valueA": value, "valueB": {"type": "number", "value": subpx_shift_for_units(units)}}

    @staticmethod
    def _to_position(value, units):
        """_to_subpixels for a position word (vm_position_shift): identical in a
        GB Studio world, pixels in a wide one."""
        if not isinstance(value, dict):
            raise CompileError(f"{value!r} is not a script value")
        return {"type": "shl", "valueA": value, "valueB": {"type": "number", "value": vm_position_shift(units)}}

    def actor_move_to_script_values(self, actor_id, value_x, value_y, collide_with, move_type=_UNDEFINED,
                                    units=_UNDEFINED, lock_direction=_UNDEFINED):
        """scriptBuilder.ts actorMoveToScriptValues."""
        move_type = "horizontal" if move_type is _UNDEFINED else move_type
        units = "tiles" if units is _UNDEFINED else units
        lock_direction = [] if lock_direction is _UNDEFINED else lock_direction
        self._check_move(move_type, units)
        actor_ref = self._declare_local("actor", 4)
        stack_ptr = self.stack_ptr
        self._add_comment("Actor Move To")
        optimised_x = optimise_script_value(self._to_position(value_x, units))
        optimised_y = optimise_script_value(self._to_position(value_y, units))
        attr = to_asm_move_flags(move_type, collide_with)
        rpn_x, fetch_x = precompile_script_value(optimised_x, "x")
        rpn_y, fetch_y = precompile_script_value(optimised_y, "y")
        locals_lookup = self._perform_fetch_operations(fetch_x + fetch_y)
        rpn = self._rpn()
        self._add_comment("-- Calculate coordinate values")
        self._perform_value_rpn(rpn, rpn_x, locals_lookup)
        rpn.ref_set(self._offset_stack_addr(actor_ref, 1))
        self._perform_value_rpn(rpn, rpn_y, locals_lookup)
        rpn.ref_set(self._offset_stack_addr(actor_ref, 2))
        rpn.stop()
        self._add_comment("-- Move Actor")
        self.actor_set_by_id(actor_id)
        self._actor_move_to_ops(actor_ref, attr, move_type, True, True, lock_direction)
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    def actor_move_relative_by_script_values(self, actor_id, value_x, value_y, collide_with,
                                             move_type=_UNDEFINED, units=_UNDEFINED, lock_direction=_UNDEFINED):
        """scriptBuilder.ts actorMoveRelativeByScriptValues."""
        move_type = "horizontal" if move_type is _UNDEFINED else move_type
        units = "tiles" if units is _UNDEFINED else units
        lock_direction = [] if lock_direction is _UNDEFINED else lock_direction
        self._check_move(move_type, units)
        stack_ptr = self.stack_ptr
        self._add_comment("Actor Move Relative")
        for value in (value_x, value_y):
            if not isinstance(value, dict):
                raise CompileError(f"{value!r} is not a script value")
        optimised_x = optimise_script_value(value_x)
        optimised_y = optimise_script_value(value_y)
        move_x = optimised_x.get("type") != "number" or optimised_x.get("value") != 0
        move_y = optimised_y.get("type") != "number" or optimised_y.get("value") != 0
        if not move_x and not move_y:
            return
        attr = to_asm_move_flags(move_type, collide_with, True, units)
        rpn_x, fetch_x = precompile_script_value(optimise_script_value(self._to_position(value_x, units)), "x")
        rpn_y, fetch_y = precompile_script_value(optimise_script_value(self._to_position(value_y, units)), "y")
        locals_lookup = self._perform_fetch_operations(fetch_x + fetch_y)
        rpn = self._rpn()
        self._add_comment("-- Calculate coordinate values")
        rpn.actor_id(actor_id)
        self._perform_value_rpn(rpn, rpn_x, locals_lookup)
        self._perform_value_rpn(rpn, rpn_y, locals_lookup)
        rpn.stop()
        self._add_comment("-- Move Actor")
        self._actor_move_to_ops(".ARG2", attr, move_type, move_x, move_y, lock_direction)
        self._stack_pop(3)
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    @staticmethod
    def _check_move(move_type, units):
        """GB Studio repairs these silently: an unknown moveType only snaps the
        actor (no branch of _actorMoveToOps), unknown units count as pixels
        (subpxShiftForUnits). Refused."""
        if move_type not in ("horizontal", "vertical", "diagonal"):
            raise CompileError(f"moveType {move_type!r} (GB Studio would not move the actor)")
        if units not in ("tiles", "pixels"):
            raise CompileError(f"units {units!r} (GB Studio would count pixels)")

    def _actor_move_to_ops(self, addr, attr, move_type, move_x, move_y, lock_direction):
        """scriptBuilderBase.ts _actorMoveToOps (lockDirection.includes: a
        missing list fails in GB Studio, refused here)."""
        if not isinstance(lock_direction, list):
            raise CompileError(f"lockDirection {lock_direction!r} is not a list")
        lock_x, lock_y = "x" in lock_direction, "y" in lock_direction
        if not move_x and not move_y:
            return

        def set_dir_x():
            if not lock_x:
                self._add_cmd("VM_ACTOR_MOVE_TO_SET_DIR_X", addr)
            else:
                self._add_cmd("VM_ACTOR_SET_ANIM_MOVING", addr)

        def set_dir_y():
            if not lock_y:
                self._add_cmd("VM_ACTOR_MOVE_TO_SET_DIR_Y", addr)
            else:
                self._add_cmd("VM_ACTOR_SET_ANIM_MOVING", addr)
        self._add_cmd("VM_ACTOR_MOVE_TO_INIT", addr, attr)
        if move_x and not move_y:
            set_dir_x()
            self._add_cmd("VM_ACTOR_MOVE_TO_X", addr, attr)
        elif move_y and not move_x:
            set_dir_y()
            self._add_cmd("VM_ACTOR_MOVE_TO_Y", addr, attr)
        elif move_type == "horizontal":
            set_dir_x()
            self._add_cmd("VM_ACTOR_MOVE_TO_X", addr, attr)
            set_dir_y()
            self._add_cmd("VM_ACTOR_MOVE_TO_Y", addr, attr)
        elif move_type == "vertical":
            set_dir_y()
            self._add_cmd("VM_ACTOR_MOVE_TO_Y", addr, attr)
            set_dir_x()
            self._add_cmd("VM_ACTOR_MOVE_TO_X", addr, attr)
        elif move_type == "diagonal":
            if not lock_y:
                self._add_cmd("VM_ACTOR_MOVE_TO_SET_DIR_Y", addr)
            elif not lock_x:
                self._add_cmd("VM_ACTOR_MOVE_TO_SET_DIR_X", addr)
            else:
                self._add_cmd("VM_ACTOR_SET_ANIM_MOVING", addr)
            self._add_cmd("VM_ACTOR_MOVE_TO_XY", addr, attr)

    def actor_set_direction_to_script_value(self, actor_id, value):
        actor_ref = self._declare_local("actor", 4)
        left, right, up, end = (self.get_next_label() for _ in range(4))
        self._add_comment("Actor Set Direction To")
        rpn_ops, fetch_ops = self._precompile(value)
        if len(rpn_ops) == 1 and rpn_ops[0].get("type") == "number":
            self.actor_set_by_id(actor_id)
            self._add_cmd("VM_ACTOR_SET_DIR", actor_ref, js_str(rpn_ops[0].get("value") or 0))
        elif len(rpn_ops) == 1 and rpn_ops[0].get("type") == "direction":
            self.actor_set_by_id(actor_id)
            self._add_cmd("VM_ACTOR_SET_DIR", actor_ref, to_asm_dir(rpn_ops[0].get("value")))
        else:
            locals_lookup = self._perform_fetch_operations(fetch_ops)
            self._add_comment("-- Calculate value")
            rpn = self._rpn()
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.ref_set(self._offset_stack_addr(actor_ref, 1)).stop()
            self.actor_set_by_id(actor_id)
            new_value = self._offset_stack_addr(actor_ref, 1)
            self._if_const(".EQ", new_value, ".DIR_LEFT", left, 0)
            self._if_const(".EQ", new_value, ".DIR_RIGHT", right, 0)
            self._if_const(".EQ", new_value, ".DIR_UP", up, 0)
            self._add_cmd("VM_ACTOR_SET_DIR", actor_ref, ".DIR_DOWN")
            self._jump(end)
            self._label(left)
            self._add_cmd("VM_ACTOR_SET_DIR", actor_ref, ".DIR_LEFT")
            self._jump(end)
            self._label(right)
            self._add_cmd("VM_ACTOR_SET_DIR", actor_ref, ".DIR_RIGHT")
            self._jump(end)
            self._label(up)
            self._add_cmd("VM_ACTOR_SET_DIR", actor_ref, ".DIR_UP")
            self._label(end)
        self._add_nl()

    def actor_set_frame_to_script_value(self, actor_id, value):
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Actor Set Animation Frame To")
        rpn_ops, fetch_ops = self._precompile(value)
        locals_lookup = self._perform_fetch_operations(fetch_ops)
        self._add_comment("-- Calculate value")
        rpn = self._rpn()
        self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
        rpn.ref_set(self._offset_stack_addr(actor_ref, 1))
        rpn.stop()
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_ACTOR_SET_ANIM_FRAME", actor_ref)
        self._add_nl()

    def idle(self):
        self._add_comment("Idle")
        self._idle()
        self._add_nl()

    def fade_in(self, speed=_UNDEFINED):
        self._add_comment("Fade In")
        self._set_const_mem_int8("fade_frames_per_step", fade_speed(speed))
        self._fade_in(True)
        self._add_nl()

    def fade_out(self, speed=_UNDEFINED):
        self._add_comment("Fade Out")
        self._set_const_mem_int8("fade_frames_per_step", fade_speed(speed))
        self._fade_out(True)
        self._add_nl()

    # -- text (scriptBuilder.ts textDialogue; scriptBuilderBase.ts text and overlay) --
    def text_dialogue(self, input_text=" ", avatar_id=None, min_height=4, max_height=7, position="bottom",
                      show_frame=True, clear_previous=True, text_x=1, text_y=1, text_height=5,
                      speed_in=_UNDEFINED, speed_out=_UNDEFINED, close_when="key", close_button="a",
                      close_delay_frames=0):
        """scriptBuilder.ts textDialogue."""
        from . import text as t
        speed_in = -1 if speed_in is _UNDEFINED else speed_in
        speed_out = -1 if speed_out is _UNDEFINED else speed_out
        scene = self.options.get("scene") or {}
        input_ = input_text if isinstance(input_text, list) else [input_text]
        overlay_in = ".OVERLAY_IN_SPEED" if speed_in == -1 else ".OVERLAY_SPEED_INSTANT" if speed_in == -3 else speed_in
        overlay_out = (".OVERLAY_OUT_SPEED" if speed_out == -1 else ".OVERLAY_SPEED_INSTANT" if speed_out == -3
                       else speed_out)
        max_num_lines = max((t.text_num_lines(x) for x in input_), default=-math.inf)   # Math.max() of []
        box_height = t.calculate_text_box_height(max_num_lines, text_y, text_height, min_height, max_height,
                                                 show_frame)
        is_modal = close_when != "notModal"
        # `!scene.parallax`: the compiled scene has `parallax` only when enabled.
        render_on_top = position == "top" and not t.js_truthy(scene.get("parallax"))
        box_y = 0 if render_on_top else 18 - box_height
        x = t.dec_oct(max(1, 1 + text_x + (2 if avatar_id else 0)))
        y = t.dec_oct(max(1, 1 + text_y))
        text_pos_sequence = f"\\003\\{x}\\{y}" if text_x != 1 or text_y != 1 else ""
        self._add_comment("Text Dialogue")
        if render_on_top:
            self._stack_push_const(0)
            self._get_mem_uint8(".ARG0", "overlay_cut_scanline")
            self._set_const_mem_uint8("overlay_cut_scanline", box_height * 8 - 1)
        for text_index, text in enumerate(input_):
            avatar_index = None
            if avatar_id:
                avatars = self.options.get("avatars") or []
                avatar_index = next((i for i, a in enumerate(avatars) if a["id"] == avatar_id), None)
            if clear_previous:
                self._overlay_clear(0, 0, 20, box_height, ".UI_COLOR_WHITE", show_frame, False)
            # Animate first dialogue window of sequence on screen
            if text_index == 0:
                self._overlay_move_to(0, box_height if render_on_top else 18, ".OVERLAY_SPEED_INSTANT")
                self._overlay_move_to(0, box_y, overlay_in)
                self._overlay_set_scroll(text_x + (2 if avatar_id else 0), text_y,
                                         (19 if show_frame else 20) - (2 if avatar_id else 0) - text_x,
                                         text_height, ".UI_COLOR_WHITE")
            decorated = (f"{self._get_avatar_code(avatar_index)}{text_pos_sequence}"
                         f"{self._inject_scroll_code(text, text_height)}")
            self._set_text_layer(".TEXT_LAYER_WIN")
            self._load_and_display_text(decorated)
            if is_modal:
                flags = [".UI_WAIT_WINDOW", ".UI_WAIT_TEXT"]
                if close_when == "key":
                    if close_button == "a":
                        flags.append(".UI_WAIT_BTN_A")
                    if close_button == "b":
                        flags.append(".UI_WAIT_BTN_B")
                    if close_button == "any":
                        flags.append(".UI_WAIT_BTN_ANY")
                self._overlay_wait(is_modal, flags)
                if close_when == "text" and close_delay_frames > 0:
                    if close_delay_frames < 5:
                        for _ in range(close_delay_frames):
                            self._idle()
                    else:
                        ref = self._declare_local("wait_args", 1, True)
                        stack_ptr = self.stack_ptr
                        self._set_const(ref, js_round(close_delay_frames))
                        self._invoke("wait_frames", 0, ref)
                        self._assert_stack_neutral(stack_ptr)
            # Animate final dialogue window of sequence off screen
            if text_index == len(input_) - 1 and is_modal:
                self._overlay_move_to(0, box_height if render_on_top else 18, overlay_out)
                self._overlay_wait(True, [".UI_WAIT_WINDOW", ".UI_WAIT_TEXT"])
        # Reset scanline when rendering on top (as long as it wasn't non-modal)
        if is_modal and render_on_top:
            self._overlay_move_to(0, 18, ".OVERLAY_SPEED_INSTANT")
            self._idle()
            self._set_mem_uint8("overlay_cut_scanline", ".ARG0")
        if render_on_top:
            self._stack_pop(1)
        self._add_nl()

    def _inject_scroll_code(self, input_text, scroll_height=None):
        """_injectScrollCode: newlines past the box become the scroll code."""
        if not scroll_height:
            return input_text
        count = [0]

        def repl(m):
            count[0] += 1
            return "\\015" if count[0] > scroll_height - 1 else m.group(0)
        return re.sub(r"(\\012|\n)", repl, input_text)

    def _get_avatar_code(self, avatar_index=None):
        """_getAvatarCode."""
        from . import text as t
        if avatar_index is None:
            return ""
        fonts = self.options.get("fonts") or []
        avatar_font_size = 16
        font_index = len(fonts) + avatar_index // avatar_font_size
        base = ((avatar_index * 4) % (avatar_font_size * 4)) + 64
        return (f"{t.text_code_set_speed(0)}{t.text_code_set_font(font_index)}{chr(base)}{chr(base + 1)}"
                f"\\n{chr(base + 2)}{chr(base + 3)}{t.text_code_set_speed(2)}{t.text_code_goto_rel(1, -1)}"
                f"{t.text_code_set_font(0)}")

    def _load_and_display_text(self, input_text):
        """_loadAndDisplayText: chunks split on !W: wait codes."""
        from . import text as t
        wait_args_ref = ""
        last_wait = -1
        chunks = t.chunk_text_on_wait_codes(input_text, self.source or "text")
        for i, chunk in enumerate(chunks):
            self._load_tokens(chunk["tokens"])
            self._display_text(i != 0)
            action = chunk.get("action")
            if action is not None and action["type"] == "wait":
                if wait_args_ref == "":
                    wait_args_ref = self._declare_local("wait_args", 1, True)
                frames = action["frames"]
                self._overlay_wait(True, [".UI_WAIT_TEXT"])
                if last_wait != frames:
                    self._set_const(wait_args_ref, js_round(frames))
                    last_wait = frames
                self._invoke("wait_frames", 0, wait_args_ref)

    def _load_tokens(self, tokens):
        """_loadTokens."""
        from . import text as t
        fonts = self.options.get("fonts") or []
        default_id = self.options.get("defaultFontId")
        font = next((f for f in fonts if f["id"] == default_id), None)
        if font is None:
            font = fonts[0] if fonts else None
        if font is None:
            # GB Studio writes "UNABLE TO LOAD FONT" into the game: refused.
            raise CompileError("text: the project has no font")
        out, indirect, aliases = "", [], []
        where = self.source or "text"
        for token in tokens:
            kind = token["type"]
            if kind == "text":
                out += t.encode_string(token["value"], font.get("mapping"), where)
            elif kind == "font":
                new = next((f for f in fonts if f["id"] == token["fontId"]), None)
                if new is not None:
                    index = next(i for i, f in enumerate(fonts) if f["id"] == token["fontId"])
                    font = new
                    out += t.text_code_set_font(index)
            elif kind in ("variable", "char", "speedVariable", "fontVariable"):
                variable = self._resolve_variable_ref(token["variableId"])
                if self._is_function_arg(variable):
                    if self._is_indirect_variable(variable):
                        local = self._declare_local(f"text_arg{len(indirect)}", 1, True)
                        indirect.insert(0, {"local": local, "arg": variable["symbol"]})
                        aliases.append(self._raw_offset_stack_addr(local))
                    else:
                        aliases.append(self._raw_offset_stack_addr(variable["symbol"]))
                else:
                    aliases.append(self.get_variable_alias(re.sub(r"^0", "", variable)))
                if kind == "variable" and token.get("fixedLength") is not None:
                    out += f"%D{token['fixedLength']}"
                elif kind == "variable":
                    out += "%d"
                elif kind == "char":
                    out += "%c"
                elif kind == "speedVariable":
                    out += "%t"
                else:
                    out += "%f"
            elif kind == "speed":
                out += t.text_code_set_speed(token["speed"])
            elif kind == "gotoxy" and token.get("relative"):
                out += t.text_code_goto_rel(token["x"], token["y"])
            elif kind == "gotoxy":
                out += t.text_code_goto(token["x"], token["y"])
            elif kind == "input":
                out += t.text_code_input(token["mask"])
        for item in indirect:
            self._get_ind(item["local"], item["arg"])
        self._load_text(len(aliases))
        if aliases:
            self._dw(*aliases)
        self._string(out)

    def _resolve_variable_ref(self, variable):
        """_resolveVariableRef."""
        if isinstance(variable, str) and re.fullmatch(r"V[0-9]", variable):
            arg = self.options["argLookup"]["variable"].get(variable)
            if arg is None:
                raise CompileError("Cant find arg")
            return arg
        return variable

    def _raw_offset_stack_addr(self, symbol, offset=0):
        """_rawOffsetStackAddr."""
        if not isinstance(symbol, str) or not (symbol.startswith(".SCRIPT_ARG_") or symbol.startswith(".LOCAL_")):
            return js_str(symbol)
        if self.stack_ptr == 0 and offset == 0:
            return symbol
        return (f"({symbol}{f' + {offset}' if offset != 0 else ''}"
                f"{f' - {self.stack_ptr}' if self.stack_ptr != 0 else ''})")

    def _load_text(self, num_inputs):
        self._add_cmd("VM_LOAD_TEXT", f"{num_inputs}")

    def _dw(self, *data):
        self._add_cmd(f".dw {', '.join(self._raw_offset_stack_addr(d) for d in data)}")

    def _string(self, text):
        self._add_cmd(f'.asciz "{text}"')

    def _display_text(self, preserve_pos=False, start_tile=None):
        if preserve_pos or start_tile is not None:
            self._add_cmd("VM_DISPLAY_TEXT_EX", ".DISPLAY_PRESERVE_POS" if preserve_pos else ".DISPLAY_DEFAULT",
                          ".TEXT_TILE_CONTINUE" if start_tile is None else start_tile)
        else:
            self._add_cmd("VM_DISPLAY_TEXT")

    def _set_text_layer(self, layer):
        self._add_cmd("VM_SWITCH_TEXT_LAYER", layer)

    def _overlay_clear(self, x, y, width, height, color, draw_frame, auto_scroll=True):
        self._add_cmd("VM_OVERLAY_CLEAR", x, y, width, height, color,
                      union_flags(([".UI_AUTO_SCROLL"] if auto_scroll else []) +
                                  ([".UI_DRAW_FRAME"] if draw_frame else [])))

    def _overlay_move_to(self, x, y, speed):
        self._add_cmd("VM_OVERLAY_MOVE_TO", x, y, speed)

    def _overlay_wait(self, modal, wait_flags):
        self._add_cmd("VM_OVERLAY_WAIT", ".UI_MODAL" if modal else ".UI_NONMODAL",
                      union_flags(wait_flags, ".UI_WAIT_NONE"))

    def _overlay_show(self, x, y, color):
        self._add_cmd("VM_OVERLAY_SHOW", x, y, color, 0)

    def overlay_show(self, color=_UNDEFINED, x=_UNDEFINED, y=_UNDEFINED):
        """scriptBuilder.ts overlayShow (color = "white", x = 0, y = 0)."""
        color = "white" if color is _UNDEFINED else color
        x = 0 if x is _UNDEFINED else x
        y = 0 if y is _UNDEFINED else y
        self._add_comment("Overlay Show")
        self._overlay_show(x, y, 1 if color == "white" else 0)
        self._add_nl()

    def set_font(self, font_id):
        """scriptBuilder.ts setFont: VM_SET_FONT <FONT SYMBOL> (_getFontSymbol: "0" for an
        unknown font, the default font)."""
        font = next((f for f in self.options.get("fonts", ()) if f.get("id") == font_id), None)
        self._add_comment("Set Font")
        self._add_cmd("VM_SET_FONT", font["symbol"].upper() if font and font.get("symbol") else "0")
        self._add_nl()

    def _replace_tile(self, addr, symbol, tile_index, num_tiles):
        self._add_cmd("VM_REPLACE_TILE", addr, f"___bank_{symbol}", f"_{symbol}", tile_index, num_tiles)

    def dialogue_frame_set_tiles(self, tileset_id):
        """scriptBuilder.ts dialogueFrameSetTiles: the nine frame tiles from a
        24 x 24 tileset; unknown tileset = the default frame. NGPC: each
        scene's UI holds the frames its scripts can select
        (docs/M4_BATCH8_EVENTS.md)."""
        tileset = next((t for t in self.options.get("tilesets") or [] if t["id"] == tileset_id), None)
        if tileset and (tileset["imageWidth"] != 24 or tileset["imageHeight"] != 24):
            raise CompileError(f"The selected tileset is {tileset['imageWidth']}x{tileset['imageHeight']}px. "
                               "Please select a 24x24 tileset.")
        symbol = tileset["symbol"] if tileset else "tileset_default_frame"
        self._add_comment("Set dialogue frame")
        self._stack_push_const(0)
        self._stack_push_const(".FRAME_TILE_ID")
        self._replace_tile(".ARG0", symbol, ".ARG1", ".FRAME_LENGTH")
        self._stack_pop(2)
        self._add_nl()

    def text_draw(self, input_text=_UNDEFINED, x=_UNDEFINED, y=_UNDEFINED, location=_UNDEFINED):
        """scriptBuilder.ts textDraw (inputText = " ", x = 0, y = 0, location =
        "background"). Background: the scene's background map (SCR2,
        GetBkgAddr), a 32 x 32 ring as on the Game Boy; overlay: the window's
        map (docs/M4_BATCH6_EVENTS.md)."""
        from . import text as t
        input_text = " " if input_text is _UNDEFINED else input_text
        x = 0 if x is _UNDEFINED else x
        y = 0 if y is _UNDEFINED else y
        location = "background" if location is _UNDEFINED else location
        is_color = (self.options.get("paletteSettings") or {}).get("colorMode") != "mono"
        draw_x, draw_y = t.dec_oct(1 + x), t.dec_oct(1 + y)
        self._add_comment("Draw Text")
        if is_color:
            self._stack_push_const(0)
            self._get_mem_uint8(".ARG0", "overlay_priority")
            self._set_const_mem_uint8("overlay_priority", 0)
        self._set_text_layer(".TEXT_LAYER_BKG" if location == "background" else ".TEXT_LAYER_WIN")
        self._load_and_display_text(f"\\003\\{draw_x}\\{draw_y}\\001\\001{input_text}")
        self._overlay_wait(False, [".UI_WAIT_TEXT"])
        if location == "background":
            self._set_text_layer(".TEXT_LAYER_WIN")
        if is_color:
            self._set_mem_uint8("overlay_priority", ".ARG0")
            self._stack_pop(1)
        self._add_nl()

    def text_close_non_modal(self, speed=_UNDEFINED):
        """scriptBuilder.ts textCloseNonModal (speed = 0; -3 = instant)."""
        from .custom_events import _js_number
        speed = 0 if speed is _UNDEFINED else speed
        self._add_comment("Close Non-Modal Dialogue")
        self._overlay_move_to(0, 18, ".OVERLAY_SPEED_INSTANT" if _js_number(speed) == -3 else speed)
        self._idle()
        self._overlay_wait(False, [".UI_WAIT_WINDOW", ".UI_WAIT_TEXT"])
        self._set_const_mem_uint8("overlay_cut_scanline", 150)    # consts.ts LYC_SYNC_VALUE
        self._add_nl()

    def overlay_move_to(self, x=_UNDEFINED, y=_UNDEFINED, speed=_UNDEFINED):
        """scriptBuilder.ts overlayMoveTo (x = 0, y = 18, speed = 0)."""
        from .custom_events import _js_number
        x = 0 if x is _UNDEFINED else x
        y = 18 if y is _UNDEFINED else y
        speed = 0 if speed is _UNDEFINED else speed
        self._add_comment("Overlay Move To")
        self._overlay_move_to(x, y, ".OVERLAY_SPEED_INSTANT" if _js_number(speed) == -3 else speed)
        self._overlay_wait(False, [".UI_WAIT_WINDOW"])
        self._add_nl()

    # -- data (save / load), docs/M5_SAVE.md step 4 ----------------------------
    def _save_slot(self, slot):
        self._add_cmd(f"    .SAVE_SLOT {slot}")

    def _poll_loaded(self, addr):
        self._add_cmd("VM_POLL_LOADED", addr)

    def _save_peek(self, success_dest, dest, source, count, slot):
        self._add_cmd("VM_SAVE_PEEK", success_dest, dest, source, count, slot)

    @staticmethod
    def _slot(slot):
        """The save slot field (togglebuttons 0, 1, 2; default 0). The NGPC
        journal has exactly these three slots (docs/M5_SAVE.md)."""
        slot = 0 if slot is _UNDEFINED else slot
        if type(slot) is not int or not 0 <= slot <= 2:
            raise CompileError(f"save slot {slot!r}: expected 0, 1 or 2")
        return slot

    def data_clear(self, slot=_UNDEFINED):
        """scriptBuilder.ts dataClear."""
        slot = self._slot(slot)
        self._add_comment(f"Clear Data in Slot {slot}")
        self._add_cmd("VM_SAVE_CLEAR", slot)
        self._add_nl()

    def data_peek(self, slot=_UNDEFINED, variable_source=None, variable_dest=None):
        """scriptBuilder.ts dataPeek."""
        slot = self._slot(slot)
        peek_value_ref = self._declare_local("peek_value", 1, True)
        dest_alias = self.get_variable_alias(variable_dest)
        source_alias = self.get_variable_alias(variable_source)
        found_label = self.get_next_label()
        self._add_comment(f"Store {source_alias} from save slot {slot} into {dest_alias}")
        self._save_peek(peek_value_ref, dest_alias, source_alias, 1, slot)
        self._if_const(".EQ", peek_value_ref, 1, found_label, 0)
        self._set_variable_const(variable_dest, 0)
        self._label(found_label)
        self._add_nl()

    def data_load(self, slot=_UNDEFINED):
        """scriptBuilder.ts dataLoad."""
        slot = self._slot(slot)
        self._add_comment(f"Load Data from Slot {slot}")
        self._raise_exception("EXCEPTION_LOAD", 1)
        self._save_slot(slot)
        self._add_nl()

    def data_save(self, slot=_UNDEFINED, on_save_path=None, on_load_path=None):
        """scriptBuilder.ts dataSave: the On Load branch gets the automatic
        fade in (EVENT_FADE_IN speed 2) where calculateAutoFadeEventId puts it."""
        from ..project import load_model
        from ..scene_init import auto_fade_event_id
        slot = self._slot(slot)
        on_save_path = [] if on_save_path is None else on_save_path
        on_load_path = [] if on_load_path is None else on_load_path
        has_loaded_ref = self._declare_local("has_loaded", 1, True)
        loaded_label = self.get_next_label()
        end_label = self.get_next_label()
        self._add_comment(f"Save Data to Slot {slot}")
        self._raise_exception("EXCEPTION_SAVE", 1)
        self._save_slot(slot)
        self._poll_loaded(has_loaded_ref)
        self._if_const(".EQ", has_loaded_ref, 1, loaded_label, 0)
        self._add_nl()
        self._compile_path(on_save_path)
        self._jump(end_label)
        self._label(loaded_label)
        if isinstance(on_load_path, list):
            plugins = self.options["pluginEvents"] if "pluginEvents" in self.options else {}
            flags = {**load_model()["events"], **{c: p.events_by_id[c] for c, p in plugins.items()}}
            custom = {ce["id"]: ce for ce in self.options["customEvents"]}
            auto_fade_id = auto_fade_event_id(
                on_load_path, [""] * len(on_load_path), custom,
                lambda command, name, where: bool((flags.get(command) or {}).get(name)),
                lambda target: "")
            index = (next((i for i, e in enumerate(on_load_path) if e.get("id") == auto_fade_id), -1)
                     if auto_fade_id else -1)
            fade_event = {"id": "autofade", "command": "EVENT_FADE_IN", "args": {"speed": 2}}
            # GB Studio splices the project's own list; a copy here, so that
            # the loaded project is unchanged and a second compilation of the
            # same script does not add a second fade.
            on_load_path = list(on_load_path)
            if index > -1:
                on_load_path.insert(index, fade_event)
            elif auto_fade_id != "MANUAL":
                on_load_path.append(fade_event)
        self._compile_path(on_load_path)
        if not isinstance(on_load_path, list):
            self._fade_in(True)
        self._label(end_label)
        self._add_nl()

    def if_data_saved(self, slot=_UNDEFINED, true_path=None, false_path=None):
        """scriptBuilder.ts ifDataSaved."""
        slot = self._slot(slot)
        true_path = [] if true_path is None else true_path
        false_path = [] if false_path is None else false_path
        save_peek_ref = self._declare_local("save_peek", 1, True)
        true_label = self.get_next_label()
        end_label = self.get_next_label()
        self._add_comment("If Variable True")
        self._save_peek(save_peek_ref, 0, 0, 0, slot)
        self._if_const(".EQ", save_peek_ref, 1, true_label, 0)
        self._add_nl()
        self._compile_path(false_path)
        self._jump(end_label)
        self._label(true_label)
        self._compile_path(true_path)
        self._label(end_label)
        self._add_nl()

    def _choice(self, variable, options, num_items):
        self._add_cmd("VM_CHOICE", variable, union_flags(options), num_items)

    def _menu_item(self, x, y, left, right, up, down):
        self._add_cmd("    .MENUITEM", x, y, left, right, up, down)

    def text_choice(self, variable, args):
        """scriptBuilder.ts textChoice."""
        from . import text as t
        variable_alias = self.get_variable_alias(variable)
        true_text = args.get("trueText") or "Choice A"
        false_text = args.get("falseText") or "Choice B"
        choice_text = (f"{t.text_code_set_speed(0)}{t.text_code_goto(3, 2)}{true_text}\n"
                       f"{t.text_code_goto(3, 3)}{false_text}")
        num_lines = len(choice_text.split("\n"))
        self._add_comment("Text Multiple Choice")
        dest = variable_alias
        if self._is_indirect_variable(variable):
            dest = self._declare_local("menu_result", 1, True)
        self._overlay_clear(0, 0, 20, num_lines + 2, ".UI_COLOR_WHITE", True, True)
        self._overlay_move_to(0, 18 - num_lines - 2, ".OVERLAY_IN_SPEED")
        self._set_text_layer(".TEXT_LAYER_WIN")
        self._load_and_display_text(choice_text)
        self._overlay_wait(True, [".UI_WAIT_WINDOW", ".UI_WAIT_TEXT"])
        self._choice(dest, [".UI_MENU_LAST_0", ".UI_MENU_CANCEL_B"], 2)
        self._menu_item(1, 1, 0, 0, 0, 2)
        self._menu_item(1, 2, 0, 0, 1, 0)
        self._overlay_move_to(0, 18, ".OVERLAY_OUT_SPEED")
        self._overlay_wait(True, [".UI_WAIT_WINDOW", ".UI_WAIT_TEXT"])
        if self._is_indirect_variable(variable):
            self._set_ind(variable_alias, dest)
        self._add_nl()

    def text_menu(self, variable, options, layout="menu", cancel_on_last_option=False, cancel_on_b=False):
        """Released textMenu: "menu" is the window at x = 10, one column of up
        to eight items (overlay at x != 0, docs/M4_OVERLAY.md); "dialogue" the
        bottom box with two columns of four."""
        from . import text as t
        if layout not in ("menu", "dialogue"):
            raise CompileError(f"EVENT_MENU: unknown layout {layout!r}")
        if not 1 <= len(options) <= 8:
            raise CompileError("EVENT_MENU: items must be between 1 and 8")
        lateral = layout == "menu"
        variable_alias = self.get_variable_alias(variable)
        texts = [t.text_code_set_font(0) + (option or f"Item {i + 1}") for i, option in enumerate(options)]
        height = len(options) if lateral else min(len(options), 4)
        menu_text = t.text_code_set_speed(0) + t.text_code_goto(3, 2) + (
            "\n".join(texts) if lateral else
            "\n".join((t.text_code_goto(12, 2) if i == 4 else "") + text for i, text in enumerate(texts)))
        x = 10 if lateral else 0
        flags = []
        if cancel_on_last_option:
            flags.append(".UI_MENU_LAST_0")
        if cancel_on_b:
            flags.append(".UI_MENU_CANCEL_B")
        self._add_comment("Text Menu")
        dest = variable_alias
        if self._is_indirect_variable(variable):
            dest = self._declare_local("menu_result", 1, True)
        self._overlay_clear(0, 0, 20 - x, height + 2, ".UI_COLOR_WHITE", True, True)
        if lateral:
            self._overlay_move_to(10, 18, ".OVERLAY_SPEED_INSTANT")
        self._overlay_move_to(x, 18 - height - 2, ".OVERLAY_IN_SPEED")
        self._set_text_layer(".TEXT_LAYER_WIN")
        self._load_and_display_text(menu_text)
        self._overlay_wait(True, [".UI_WAIT_WINDOW", ".UI_WAIT_TEXT"])
        self._choice(dest, flags, len(options))

        def index(i):
            return i + 1 if 0 <= i < len(options) else 0

        for i in range(len(options)):
            if lateral:
                self._menu_item(1, 1 + i, 1, len(options), index(i - 1), index(i + 1))
            else:
                self._menu_item(1 if i < 4 else 10, 1 + i % 4, index(i - 4) or 1,
                                index(i + 4) or len(options), index(i - 1), index(i + 1))
        self._overlay_move_to(x, 18, ".OVERLAY_OUT_SPEED")
        self._overlay_wait(True, [".UI_WAIT_WINDOW", ".UI_WAIT_TEXT"])
        if lateral:
            self._overlay_move_to(0, 18, ".OVERLAY_SPEED_INSTANT")
        if self._is_indirect_variable(variable):
            self._set_ind(variable_alias, dest)
        self._add_nl()

    def _overlay_set_scroll(self, x, y, width, height, color):
        self._add_cmd("VM_OVERLAY_SET_SCROLL", x, y, width, height, color)

    def _get_mem_uint8(self, addr, c_variable):
        self._add_cmd("VM_GET_UINT8", addr, f"_{c_variable}")

    def _set_const_mem_uint8(self, c_variable, value):
        self._add_dependency(c_variable)
        self._add_cmd("VM_SET_CONST_UINT8", f"_{c_variable}", value)

    def _set_mem_uint8(self, c_variable, addr):
        self._add_dependency(c_variable)
        self._add_cmd("VM_SET_UINT8", f"_{c_variable}", addr)

    def _set_mem_uint8_to_variable(self, c_variable, variable):
        """scriptBuilderBase.ts _setMemUInt8ToVariable."""
        alias = self.get_variable_alias(variable)
        self._add_dependency(c_variable)
        if self._is_indirect_variable(variable):
            self._stack_push_ind(alias)
            self._set_mem_uint8(c_variable, ".ARG0")
            self._stack_pop(1)
        else:
            self._set_mem_uint8(c_variable, alias)

    def overlay_set_scanline_cutoff(self, y, units="pixels"):
        """scriptBuilder.ts overlaySetScanlineCutoff (docs/M6_SCANLINE_CUTOFF.md)."""
        if not isinstance(y, dict):
            raise CompileError(f"{y!r} is not a script value")
        self._add_comment("Overlay Set Scanline Cutoff")
        rpn_ops, fetch_ops = precompile_script_value(optimise_script_value(
            {"type": "shl", "valueA": y, "valueB": {"type": "number", "value": 3 if units == "tiles" else 0}}))
        if len(rpn_ops) == 1 and rpn_ops[0].get("type") == "number":
            self._set_const_mem_uint8("overlay_cut_scanline", rpn_ops[0]["value"])
        else:
            locals_lookup = self._perform_fetch_operations(fetch_ops)
            y_ref = self._declare_local("y", 1, True)
            self._add_comment("-- Calculate value")
            rpn = self._rpn()
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.ref_set_variable(y_ref).stop()
            self._set_mem_uint8_to_variable("overlay_cut_scanline", y_ref)
        self._add_nl()

    # -- scene stack (scriptBuilder.ts scenePushState ... sceneResetState) ----
    def scene_push_state(self):
        self._add_comment("Push Scene State")
        self._add_cmd("VM_SCENE_PUSH")
        self._add_nl()

    def _scene_pop_fade(self, fade):
        fade = 2 if fade is _UNDEFINED else fade
        if _js_gt_zero(fade):
            self._set_const_mem_int8("fade_frames_per_step", fade_speed(fade))
            self._fade_out(True)
        self._set_const_mem_int8("camera_settings", ".CAMERA_LOCK")

    def scene_pop_state(self, fade=_UNDEFINED):
        self._add_comment("Pop Scene State")
        self._scene_pop_fade(fade)
        self._add_cmd("VM_SCENE_POP")
        self._add_nl()

    def scene_pop_all_state(self, fade=_UNDEFINED):
        self._add_comment("Pop All Scene State")
        self._add_comment("" + str(2 if fade is _UNDEFINED else fade))
        self._scene_pop_fade(fade)
        self._add_cmd("VM_SCENE_POP_ALL")
        self._add_nl()

    def scene_reset_state(self):
        self._add_comment("Reset Scene State Stack")
        self._add_cmd("VM_SCENE_STACK_RESET")
        self._add_nl()

    def scene_switch_using_script_values(self, scene_id, x, y, direction=_UNDEFINED, fade_speed_=_UNDEFINED):
        """scriptBuilder.ts sceneSwitchUsingScriptValues. GB Studio emits only
        the comment for a scene it cannot find (and still reserves the actor
        local): refused here, with the scene id."""
        direction = "down" if direction is _UNDEFINED else direction
        fade = 2 if fade_speed_ is _UNDEFINED else fade_speed_
        actor_ref = self._declare_local("actor", 4)
        self._add_comment("Load Scene")
        scenes = self.options.get("scenes")
        if scenes is None:
            raise CompileError("scene switch compiled without the export's scene list")
        scene = next((s for s in scenes if s["id"] == scene_id), None)
        if scene is None:
            raise CompileError(f"sceneId {scene_id!r}: no such scene in the export "
                               "(GB Studio would silently not switch)")
        if _js_gt_zero(fade):
            self._set_const_mem_int8("fade_frames_per_step", fade_speed(fade))
            self._fade_out(True)
        for value in (x, y):
            if not isinstance(value, dict):
                raise CompileError(f"{value!r} is not a script value")
        rpn_x, fetch_x = precompile_script_value(optimise_script_value(self._to_position(x, "tiles")), "x")
        rpn_y, fetch_y = precompile_script_value(optimise_script_value(self._to_position(y, "tiles")), "y")
        locals_lookup = self._perform_fetch_operations(fetch_x + fetch_y)
        rpn = self._rpn()
        self._add_comment("-- Calculate coordinate values")
        self._perform_value_rpn(rpn, rpn_x, locals_lookup)
        rpn.ref_set(self._offset_stack_addr(actor_ref, 1))
        self._perform_value_rpn(rpn, rpn_y, locals_lookup)
        rpn.ref_set(self._offset_stack_addr(actor_ref, 2))
        rpn.stop()
        self._set_const(actor_ref, 0)
        self._actor_set_position(actor_ref)
        asm_dir = to_asm_dir(direction)
        if asm_dir:
            self._actor_set_direction(actor_ref, asm_dir)
        self._set_const_mem_int8("camera_settings", ".CAMERA_LOCK")
        self._raise_exception("EXCEPTION_CHANGE_SCENE", 3)
        self._import_far_ptr_data(scene["symbol"])
        self._add_nl()

    def set_sprite_mode(self, mode):
        self._add_comment(f"Set Sprite Mode: {mode}")
        self._set_sprite_mode(to_asm_sprite_mode(mode))
        self._add_nl()

    def camera_move_to_script_values(self, value_x, value_y, speed=0, units="tiles"):
        """Released cameraMoveToScriptValues; coordinates denote GB viewport origin."""
        if units not in ("tiles", "pixels"):
            raise CompileError(f"Camera Move To: unknown units {units!r}")
        self._check_camera_speed(speed)
        args = self._declare_local("camera_move_args", 2, True)
        stack_ptr = self.stack_ptr
        self._add_comment("Camera Move To")
        pairs = [precompile_script_value(optimise_script_value({"type": "add",
                    "valueA": self._to_position(value, units),
                    "valueB": {"type": "number", "value": vm_position_px(offset)}}), label)
                 for value, offset, label in ((value_x, 2560, "x"), (value_y, 2304, "y"))]
        lookup = self._perform_fetch_operations(pairs[0][1] + pairs[1][1])
        rpn = self._rpn()
        self._add_comment("-- Calculate coordinate values")
        for i, (ops, _) in enumerate(pairs):
            self._perform_value_rpn(rpn, ops, lookup)
            rpn.ref_set(self._offset_stack_addr(args, i))
        rpn.stop()
        self._add_comment("-- Move Camera")
        if speed == 0:
            self._add_cmd("VM_CAMERA_SET_POS", args)
        else:
            self._add_cmd("VM_CAMERA_MOVE_TO", args, math.floor(speed * 32), ".CAMERA_UNLOCK")
        self._assert_stack_neutral(stack_ptr)
        self._add_nl()

    def camera_set_bounds_to_script_values(self, value_x, value_y, width, height, units=_UNDEFINED):
        """scriptBuilder.ts cameraSetBoundsToScriptValues: GBVM scroll.c
        scroll_x_min / scroll_y_min / scroll_x_max / scroll_y_max, the maxima
        computed for the 160 x 144 Game Boy screen (SCREEN_*_PX). The NGPC
        clamp takes the view height difference (docs/M4_BATCH7_EVENTS.md)."""
        units = "tiles" if units is _UNDEFINED else units
        if units not in ("tiles", "pixels"):
            raise CompileError(f"Camera Set Bounds: unknown units {units!r}")
        for value in (value_x, value_y, width, height):
            if not isinstance(value, dict):
                raise CompileError(f"{value!r} is not a script value")

        def px(value):                     # helpers.ts scriptValueToPixels
            return value if units == "pixels" else {"type": "shl", "valueA": value,
                                                    "valueB": {"type": "number", "value": 3}}

        def num(v):
            return {"type": "number", "value": v}

        from ..world import NGPC_MAX_PX, wide
        top = NGPC_MAX_PX if wide() else 2040   # GB Studio's 255 tiles, or the wide world's

        def extent(size, origin, screen):  # sub(add(clamp(size, screen, 2040), origin), screen)
            clamped = {"type": "max", "valueA": num(screen),
                       "valueB": {"type": "min", "valueA": num(top), "valueB": px(size)}}
            return {"type": "sub", "valueA": {"type": "add", "valueA": clamped, "valueB": px(origin)},
                    "valueB": num(screen)}
        self._add_comment("Camera Set Bounds")
        compiled = [precompile_script_value(optimise_script_value(value), label) for label, value in
                    (("x", px(value_x)), ("y", px(value_y)), ("width", extent(width, value_x, 160)),
                     ("height", extent(height, value_y, 144)))]
        locals_lookup = self._perform_fetch_operations([op for _, fetch in compiled for op in fetch])
        rpn = self._rpn()
        self._add_comment("-- Calculate bounds values")
        for (rpn_ops, _), memory in zip(compiled, ("scroll_x_min", "scroll_y_min", "scroll_x_max", "scroll_y_max")):
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.mem_set(".MEM_I16", memory)
        rpn.stop()
        self._add_nl()

    @staticmethod
    def _check_camera_speed(speed):
        if isinstance(speed, bool) or not isinstance(speed, (float, int)) or not math.isfinite(speed):
            raise CompileError("camera speed must be finite")
        if speed < 0 or math.floor(speed * 32) > 255 or (speed > 0 and math.floor(speed * 32) == 0):
            raise CompileError("camera speed must be 0 (instant) or encode 1..255 subpixels/frame")

    def camera_lock(self, speed, axis, prevent_scroll=None):
        """Released cameraLock, including GBVM's waitable final move."""
        self._check_camera_speed(speed)
        prevent_scroll = [] if prevent_scroll is None else prevent_scroll
        if not isinstance(axis, list) or any(a not in ("x", "y") for a in axis):
            raise CompileError("Camera Lock: axis must be a list of x/y")
        if not isinstance(prevent_scroll, list) or any(d not in ("left", "right", "up", "down") for d in prevent_scroll):
            raise CompileError("Camera Lock: invalid preventScroll direction")
        actor = self._declare_local("actor", 4)
        self._add_comment("Camera Lock")
        self._set_const(actor, 0)
        self._add_cmd("VM_ACTOR_GET_POS", actor)
        self._rpn().ref(self._offset_stack_addr(actor, 1)).int16(vm_position_px(256)).operator(".ADD").ref(
            self._offset_stack_addr(actor, 2)).int16(vm_position_px(256)).operator(".ADD").stop()
        if speed == 0:
            self._add_cmd("VM_CAMERA_SET_POS", ".ARG1")
        flags = ([".CAMERA_LOCK_X"] if "x" in axis else []) + ([".CAMERA_LOCK_Y"] if "y" in axis else [])
        for direction, flag in (("left", "X_MIN"), ("right", "X_MAX"), ("up", "Y_MIN"), ("down", "Y_MAX")):
            if direction in prevent_scroll:
                flags.append(".CAMERA_LOCK_" + flag)
        self._add_cmd("VM_CAMERA_MOVE_TO", ".ARG1", math.floor(speed * 32), union_flags(flags))
        self._stack_pop(2)

    def camera_shake_script_value(self, shake_x, shake_y, frames, magnitude):
        """scriptBuilder.ts cameraShakeScriptValue; a bounded magnitude for now:
        a number, or a project constant (its value checked here, its symbol
        kept in the bytecode as GB Studio writes it)."""
        optimised = optimise_script_value(magnitude)
        if optimised.get("type") == "constant":
            constant = self.options.get("constantsLookup", {}).get(optimised.get("value"))
            if not constant or not constant.get("symbol"):
                raise CompileError(f"camera shake magnitude: constant {optimised.get('value')!r} is not a project "
                                   "constant (GB Studio would shake by 0)")
            amount = constant.get("value")
        elif optimised.get("type") != "number":
            raise NotDelivered("camera shake magnitude must be constant (dynamic magnitudes not delivered)")
        else:
            amount = optimised["value"]
        if type(amount) not in (int, float) or int(amount) != amount or not 1 <= amount <= 255:
            raise CompileError("camera shake magnitude must be an integer in 1..255")
        rpn_ops, fetch_ops = precompile_script_value(optimised)
        lookup = self._perform_fetch_operations(fetch_ops)
        args_ref = self._declare_local("camera_shake_args", 3, True)
        self._add_comment("Camera Shake")
        self._set_const(args_ref, frames)
        self._set_const(self._offset_stack_addr(args_ref, 1),
                        union_flags(([".CAMERA_SHAKE_X"] if shake_x else []) +
                                    ([".CAMERA_SHAKE_Y"] if shake_y else [])))
        rpn = self._rpn()
        self._perform_value_rpn(rpn, rpn_ops, lookup)
        rpn.ref_set(self._offset_stack_addr(args_ref, 2)).stop()
        self._invoke("camera_shake_frames", 0, args_ref)
        self._add_nl()

    def if_actor_relative_to_actor(self, operation, other_id, true_path=None, false_path=None):
        """scriptBuilder.ts ifActorRelativeToActor, positions in subpixels."""
        actor_ref = self._declare_local("actor", 4)
        other_ref = self._declare_local("other_actor", 3, True)
        false_label, end_label = self.get_next_label(), self.get_next_label()
        self._add_comment(f"If Actor {operation} Relative To Actor")
        self._add_cmd("VM_ACTOR_GET_POS", actor_ref)
        self.set_actor_id(other_ref, other_id)
        self._add_cmd("VM_ACTOR_GET_POS", other_ref)
        if operation not in ("left", "right", "up", "down"):
            raise CompileError("Missing operation in ifActorRelativeToActor")
        offset = 1 if operation in ("left", "right") else 2
        comparison = ".LT" if operation in ("left", "up") else ".GT"
        self._rpn().ref(self._offset_stack_addr(actor_ref, offset)).ref(
            self._offset_stack_addr(other_ref, offset)).operator(comparison).stop()
        self._if_const(".EQ", ".ARG0", 0, false_label, 1)
        self._add_nl()
        self._compile_path(true_path)
        self._jump(end_label)
        self._label(false_label)
        self._compile_path(false_path)
        self._label(end_label)
        self._add_nl()

    def if_actor_at_position_by_script_values(self, actor_id, value_x, value_y,
                                              true_path=None, false_path=None, units=_UNDEFINED):
        """scriptBuilder.ts ifActorAtPositionByScriptValues; logical SHR."""
        units = "tiles" if units is _UNDEFINED else units
        if units not in ("tiles", "pixels"):
            raise CompileError(f"actor position units {units!r} must be tiles or pixels")
        actor_ref = self._declare_local("actor", 4)
        false_label, end_label = self.get_next_label(), self.get_next_label()
        self._add_comment("If Actor At Position")
        rpn_x, fetch_x = precompile_script_value(optimise_script_value(value_x), "x")
        rpn_y, fetch_y = precompile_script_value(optimise_script_value(value_y), "y")
        locals_lookup = self._perform_fetch_operations(fetch_x + fetch_y)
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_ACTOR_GET_POS", actor_ref)
        rpn = self._rpn()
        self._add_comment("-- Calculate coordinate values")
        rpn.ref(self._offset_stack_addr(actor_ref, 1)).int8(vm_position_shift(units)).operator(".SHR")
        self._perform_value_rpn(rpn, rpn_x, locals_lookup)
        rpn.operator(".EQ")
        rpn.ref(self._offset_stack_addr(actor_ref, 2)).int8(vm_position_shift(units)).operator(".SHR")
        self._perform_value_rpn(rpn, rpn_y, locals_lookup)
        rpn.operator(".EQ").operator(".AND").stop()
        self._if_const(".EQ", ".ARG0", 0, false_label, 1)
        self._add_nl()
        self._compile_path(true_path)
        self._jump(end_label)
        self._label(false_label)
        self._compile_path(false_path)
        self._label(end_label)
        self._add_nl()

    def if_actor_distance_script_value_from_actor(self, actor_id, distance_value, operator,
                                                   other_id, true_path=None, false_path=None):
        """Released distance predicate: logical tile shifts and squared 16-bit distance."""
        if operator not in (".EQ", ".NE", ".LT", ".GT", ".LTE", ".GTE"):
            raise CompileError(f"distance comparison {operator!r} is not supported")
        actor_ref = self._declare_local("actor", 4)
        other_ref = self._declare_local("other_actor", 3, True)
        distance_ref = self._declare_local("distance", 1, True)
        false_label, end_label = self.get_next_label(), self.get_next_label()
        self._add_comment("If Actor Distance from Actor")
        rpn_ops, fetch_ops = self._precompile(distance_value)
        lookup = self._perform_fetch_operations(fetch_ops)
        self._add_comment("-- Calculate value")
        rpn = self._rpn()
        self._perform_value_rpn(rpn, rpn_ops, lookup)
        rpn.ref_set(distance_ref).stop()
        self.actor_set_by_id(actor_id)
        self._add_cmd("VM_ACTOR_GET_POS", actor_ref)
        self.set_actor_id(other_ref, other_id)
        self._add_cmd("VM_ACTOR_GET_POS", other_ref)
        rpn = self._rpn()
        shift = vm_position_shift("tiles")
        for offset in (1, 2):
            for _ in range(2):
                rpn.ref(self._offset_stack_addr(other_ref, offset)).int16(shift).operator(".SHR")
                rpn.ref(self._offset_stack_addr(actor_ref, offset)).int16(shift).operator(".SHR")
                rpn.operator(".SUB")
            rpn.operator(".MUL")
        rpn.operator(".ADD").ref(distance_ref).ref(distance_ref).operator(".MUL").operator(operator).stop()
        self._if_const(".EQ", ".ARG0", 0, false_label, 1)
        self._add_nl()
        self._compile_path(true_path)
        self._jump(end_label)
        self._label(false_label)
        self._compile_path(false_path)
        self._label(end_label)
        self._add_nl()

    def while_script_value(self, value, true_path=None):
        """Released whileScriptValue; fetch and evaluate anew at every iteration."""
        loop_id, end_label = self.get_next_label(), self.get_next_label()
        self._add_comment("While")
        self._label(loop_id)
        rpn_ops, fetch_ops = self._precompile(value)
        lookup = self._perform_fetch_operations(fetch_ops)
        self._add_comment("-- Calculate value")
        rpn = self._rpn()
        self._perform_value_rpn(rpn, rpn_ops, lookup)
        rpn.stop()
        self._if_const(".EQ", ".ARG0", 0, end_label, 1)
        self._compile_path(true_path)
        self._jump(loop_id)
        self._label(end_label)
        self._add_nl()

    def variables_script_value_operation(self, variable, operation, value):
        """Released variablesScriptValueOperation, used by EVENT_LOOP_FOR."""
        if operation not in (".ADD", ".SUB", ".MUL", ".DIV", ".MOD"):
            raise CompileError(f"loop operation {operation!r} is not supported")
        self._add_comment(f"Variables {operation}")
        rpn_ops, fetch_ops = self._precompile(value)
        lookup = self._perform_fetch_operations(fetch_ops)
        rpn = self._rpn().ref_variable(variable)
        self._perform_value_rpn(rpn, rpn_ops, lookup)
        rpn.operator(operation).ref_set_variable(variable).stop()
        self._add_nl()

    def if_script_value(self, value, true_path=None, false_path=None):
        """scriptBuilder.ts ifScriptValue. Missing paths are [] (JavaScript
        default parameters)."""
        true_path = [] if true_path is None else true_path
        false_path = [] if false_path is None else false_path
        if not isinstance(value, dict):
            # GB Studio fails on `input.type` of a missing value (TypeError).
            raise CompileError(f"condition {value!r} is not a script value")
        test_if_truthy = True
        optimised = optimise_script_value(value)

        def is_zero(v):
            return isinstance(v, dict) and ((v.get("type") == "number" and v.get("value") == 0)
                                            or v.get("type") == "false")

        if optimised.get("type") == "not":
            # "!expression != 0" - optimise to "expression == 0"
            optimised = optimised["value"]
            test_if_truthy = False
        elif optimised.get("type") == "eq" and is_zero(optimised["valueA"]):
            # "(false == expression) != 0" (left side) - optimise to "expression == 0"
            optimised = optimised["valueB"]
            test_if_truthy = False
        elif optimised.get("type") == "eq" and is_zero(optimised["valueB"]):
            # "(expression == false) != 0" (right side) - optimise to "expression == 0"
            optimised = optimised["valueA"]
            test_if_truthy = False

        true_label = self.get_next_label()
        end_label = self.get_next_label()

        self._add_comment("If")

        if optimised.get("type") == "variable":
            if test_if_truthy:
                self._add_comment("-- If Truthy")
                self._if_variable_const(".NE", optimised["value"], 0, true_label, 0)
            else:
                self._add_comment("-- If Falsy")
                self._if_variable_const(".EQ", optimised["value"], 0, true_label, 0)
        else:
            rpn_ops, fetch_ops = precompile_script_value(optimised)
            locals_lookup = self._perform_fetch_operations(fetch_ops)
            self._add_comment("-- Calculate value")
            rpn = self._rpn()
            self._perform_value_rpn(rpn, rpn_ops, locals_lookup)
            rpn.stop()
            if test_if_truthy:
                self._add_comment("-- If Truthy")
                self._if_const(".NE", ".ARG0", 0, true_label, 1)
            else:
                self._add_comment("-- If Falsy")
                self._if_const(".EQ", ".ARG0", 0, true_label, 1)

        self._compile_path(false_path)
        self._jump(end_label)
        self._label(true_label)
        self._compile_path(true_path)
        self._label(end_label)
        self._add_nl()

    def compile_events(self, path):
        """compileEvents helper: options.compileEvents(this, path)."""
        self.options["compileEvents"](self, path)

    # -- labels ----------------------------------------------------------------
    def get_next_label(self):
        label = self.next_label
        self.next_label += 1
        return str(label)

    def label_define(self, name):
        if not self.label_lookup.get(name):
            self.label_lookup[name] = self.get_next_label()
        self._label(self.label_lookup[name])

    def label_goto(self, name):
        if not self.label_lookup.get(name):
            self.label_lookup[name] = self.get_next_label()
        self._jump(self.label_lookup[name])

    def script_end(self):
        self._stop()

    # -- export ----------------------------------------------------------------
    def to_script_string(self, name, lock):
        self._assert_stack_neutral()
        reserve = self._calc_locals_size()
        deps = f"\n.globl {', '.join(self.dependencies)}\n" if self.dependencies else ""
        args = self.options["argLookup"]
        arg_vars = "".join(f"\n{a['symbol']} = -{3 + reserve + i}"
                           for i, a in enumerate(reversed(list(args["variable"].values()))) if a)
        arg_actors = "".join(f"\n{a['symbol']} = -{3 + reserve + i + len(args['variable'])}"
                             for i, a in enumerate(reversed(list(args["actor"].values()))) if a)
        locals_ = arg_vars + arg_actors + "".join(f"\n{s} = -{self.locals_lookup[s]['addr']}" for s in self.locals_lookup)
        lock_line = self._pad_cmd("VM_LOCK", "", 8, 24) + "\n\n" if lock else ""
        reserve_line = self._pad_cmd("VM_RESERVE", str(reserve), 8, 24) + "\n\n" if reserve > 0 else ""
        headers = "\n".join(f'.include "{h}"' for h in self.headers)
        labels = lambda t, i: f"\t; {t['rowLabels'][i]}\n" if t["rowLabels"] and t["rowLabels"][i] else ""
        data_tables = "\n".join(
            f"\n{t['symbol']}:\n" + "\n".join(
                f"{labels(t, i)}\t{t['type']} {', '.join(t['data'][i * t['rowSize']:(i + 1) * t['rowSize']])}"
                for i in range(len(t["data"]) // t["rowSize"]))
            for t in self.data_tables.values())
        return (f".module {name}\n\n{headers}\n{deps}\n.area _CODE_255\n{locals_}\n\n"
                f"___bank_{name} = 255\n.globl ___bank_{name}\n\n_{name}::\n"
                f"{lock_line}{reserve_line}{chr(10).join(self.output)}\n{data_tables}")
