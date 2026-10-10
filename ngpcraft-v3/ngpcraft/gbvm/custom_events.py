"""EVENT_CALL_CUSTOM_EVENT: scriptBuilder.ts callScript and
compileCustomEventScript (GB Studio 4.3.2), translated line by line.

A custom script is compiled once per scene (and reused when the compiled text
is identical) as a function: its events have their V0..V9 variables and 0..9
actors replaced by stack arguments according to the TYPE of each field
(scriptDefHelpers.ts isVariableField, isActorField, isPropertyField,
isScriptValueField, isDataTableField; types frozen by
tools/extract_event_fields.py). The caller pushes the arguments and does
VM_CALL_FAR; the function ends with VM_RET_FAR_N (it pops them).

Not delivered yet, refused with the place: data table fields. GB Studio's silent repairs are
refused: unknown script (it only warns), unknown argument. An argument left unset is
compiled as GB Studio does, with a located warning (REPRISE A9).
"""
import copy
import hashlib
import json
from pathlib import Path
import re

from ..compiler import CompileError
from .builder import default_variable_for_context

FIELDS = Path(__file__).resolve().parents[1] / "gbstudio_event_fields.json"
MAX_NESTED_SCRIPT_DEPTH = 5           # consts.ts
SECTION_TAB_KEY = "__section"         # scriptDefHelpers.ts
_FIELDS = None


def event_fields():
    global _FIELDS
    if _FIELDS is None:
        _FIELDS = json.loads(FIELDS.read_text(encoding="utf-8"))["events"]
    return _FIELDS


def _js_number(value):
    """JavaScript Number() for the condition comparisons."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    if isinstance(value, str):
        try:
            return float(value) if value.strip() else 0.0
        except ValueError:
            return float("nan")
    return float("nan")


def is_field_visible(field, args, ignore=()):
    """scriptDefHelpers.ts isFieldVisible (its lt/lte compare like GB Studio's)."""
    if "conditions" not in field:
        return True
    ok = True
    for c in field["conditions"]:
        if c["key"] in ignore:
            continue
        missing = c["key"] not in args
        value = None if missing else args[c["key"]]
        ok = (ok
              and (not c.get("eq") or value == c["eq"])
              and (not c.get("ne") or value != c["ne"])
              and (not c.get("gt") or _js_number(value) > _js_number(c["gt"]))
              and (not c.get("gte") or _js_number(value) >= _js_number(c["gte"]))
              and (not c.get("lt") or _js_number(value) > _js_number(c["lt"]))
              and (not c.get("lte") or _js_number(value) >= _js_number(c["lte"]))
              and (not c.get("in") or value in c["in"] or (missing and None in c["in"]))
              and ("set" not in c or (c["set"] and not missing) or (not c["set"] and missing)))
    return ok


def _field(command, name):
    event = event_fields().get(command)
    return None if event is None else event["fields"].get(name)


def _is_union(value, kind):
    return isinstance(value, dict) and "type" in value and value["type"] == kind


def is_variable_field(command, name, args):
    field = _field(command, name)
    return (field is not None and (field["type"] == "variable" or _is_union(args.get(name), "variable"))
            and is_field_visible(field, args, (SECTION_TAB_KEY,)))


def is_actor_field(command, name, args):
    if name.startswith("$actor["):
        return True
    field = _field(command, name)
    return field is not None and field["type"] == "actor" and is_field_visible(field, args, (SECTION_TAB_KEY,))


def is_property_field(command, name, args):
    field = _field(command, name)
    return (field is not None and (field["type"] == "property" or _is_union(args.get(name), "property"))
            and is_field_visible(field, args, (SECTION_TAB_KEY,)))


def is_script_value_field(command, name, args):
    if name.startswith("$variable["):
        return True
    field = _field(command, name)
    return field is not None and field["type"] == "value" and is_field_visible(field, args, (SECTION_TAB_KEY,))


def is_data_table_field(command, name, args):
    field = _field(command, name)
    return field is not None and field["type"] == "dataTable" and is_field_visible(field, args, (SECTION_TAB_KEY,))


def is_variable_custom_event(value):
    return isinstance(value, str) and re.fullmatch(r"V[0-9]", value) is not None


def map_uncommented_script(script, callback):
    """walk.ts mapUncommentedScript: a commented event with children is dropped,
    one without children is still given to the callback (which keeps it)."""
    out = []
    for event in script or []:
        if event and event.get("children"):
            if (event.get("args") or {}).get("__comment"):
                continue
            new = callback(event)
            children = new.get("children") or event["children"]
            out.append({**new, "children": {k: map_uncommented_script(v, callback) for k, v in children.items()}})
        elif event:
            out.append(callback(event))
    return out


def map_script_value_leaf_nodes(value, fn):
    from .builder import VALUE_OPERATOR_TYPES, VALUE_UNARY_OPERATOR_TYPES
    kind = value.get("type") if isinstance(value, dict) else None
    if kind in VALUE_OPERATOR_TYPES:
        return {**value, "valueA": value.get("valueA") and map_script_value_leaf_nodes(value["valueA"], fn),
                "valueB": value.get("valueB") and map_script_value_leaf_nodes(value["valueB"], fn)}
    if kind in VALUE_UNARY_OPERATOR_TYPES:
        return {**value, "value": value.get("value") and map_script_value_leaf_nodes(value["value"], fn)}
    return fn(value)


def is_script_value(value):
    """scriptValue/types.ts isScriptValue, for the atoms the rewrite meets."""
    return isinstance(value, dict) and isinstance(value.get("type"), str)


def compile_custom_event_script(b, custom_event_id, bindings=None):
    """scriptBuilder.ts compileCustomEventScript -> {"scriptRef", "argsLen"}.

    bindings: {"variable": {id: variable}, "actor": {id: actor}} of the
    arguments bound at the call site (docs/M2_CUSTOM_EVENTS.md, sub-scripts):
    they are written into the script instead of being passed."""
    o = b.options
    custom = next((ce for ce in o["customEvents"] if ce["id"] == custom_event_id), None)
    if custom is None:
        raise CompileError(f"customEventId {custom_event_id!r}: no such script (GB Studio would only warn)")
    bindings = bindings or {"variable": {}, "actor": {}}
    # cacheKey `${customEventId}-${scene.hash}`: the scene id stands for the
    # scene hash (identical compiled scripts are still shared, see below).
    cache_key = f"{custom_event_id}-{o['scene']['id']}"
    if bindings["variable"] or bindings["actor"]:
        cache_key += "-bound-" + json.dumps(bindings, sort_keys=True)
    cache = o["compiledCustomEventScriptCache"]
    if cache_key in cache:
        return cache[cache_key]
    arg_lookup = {"actor": {}, "variable": {}}
    actor_args = [a for a in custom["actors"].values() if not a or a["id"] not in bindings["actor"]]
    variable_args = [a for a in custom["variables"].values() if not a or a["id"] not in bindings["variable"]]
    args_len = len(actor_args) + len(variable_args)
    num = [args_len - 1]

    def register(kind, indirect, value):
        if value not in arg_lookup[kind]:
            symbol = f".SCRIPT_ARG_{'INDIRECT_' if indirect else ''}{num[0]}_{kind}".upper()
            arg_lookup[kind][value] = {"type": "argument", "indirect": indirect, "symbol": symbol}
            num[0] -= 1
        return arg_lookup[kind][value]

    def get_arg(kind, value):
        if kind == "actor" and value == "player":
            return value
        if kind == "actor" and value == "$self$":
            return "player"
        if value in bindings[kind]:
            return bindings[kind][value]
        if value not in arg_lookup[kind]:
            raise CompileError(f"Unknown arg {kind} {value} within script \"{custom['name']}\"")
        return arg_lookup[kind][value]

    for arg in reversed(actor_args):
        if arg:
            register("actor", False, arg["id"])
    for arg in reversed(variable_args):
        if arg:
            register("variable", bool(arg["passByReference"]), arg["id"])

    def rewrite(event):
        if not event.get("args") or event["args"].get("__comment"):
            return event
        e = {**event, "args": dict(event["args"])}
        command = e["command"]
        for name in list(e["args"]):
            value = e["args"][name]
            if is_variable_field(command, name, e["args"]):
                if _is_union(value, "variable") and value.get("value") and is_variable_custom_event(value["value"]):
                    e["args"][name] = {**value, "value": get_arg("variable", value["value"])}
                elif isinstance(value, str) and is_variable_custom_event(value):
                    e["args"][name] = get_arg("variable", value)
            if is_property_field(command, name, e["args"]):
                def replace_property(p):
                    actor = re.sub(r":.*", "", p)
                    if actor == "player":
                        return p
                    arg = get_arg("actor", actor)
                    if isinstance(arg, str):
                        return f"{arg}:{re.sub(r'.*:', '', p)}"
                    return {"value": arg, "property": re.sub(r".*:", "", p)}
                if _is_union(value, "property") and value.get("value"):
                    e["args"][name] = {**value, "value": replace_property(value["value"])}
                elif isinstance(value, str):
                    e["args"][name] = replace_property(value)
            if is_actor_field(command, name, e["args"]) and isinstance(value, str):
                e["args"][name] = get_arg("actor", value)
            if is_script_value_field(command, name, e["args"]) and is_script_value(value):
                def leaf(val):
                    if val.get("type") == "variable":
                        if is_variable_custom_event(val.get("value")):
                            return {**val, "value": get_arg("variable", val["value"])}
                    elif val.get("type") == "property" and val.get("target") != "camera":
                        arg = get_arg("actor", val["target"])
                        if isinstance(arg, str) and val["target"] in bindings["actor"]:
                            return {**val, "target": arg}
                        if arg and isinstance(arg, str):
                            return {**val, "value": arg}
                        if arg:
                            return {**val, "target": arg["symbol"], "value": arg}
                    return val
                e["args"][name] = map_script_value_leaf_nodes(value, leaf)
            if is_data_table_field(command, name, e["args"]):
                raise CompileError(f"{command}.{name}: data table fields are not delivered yet")
        return e

    script = map_uncommented_script(copy.deepcopy(custom["script"]), rewrite)
    where = custom["_path"] if "_path" in custom else f"script {custom['name']}"
    input_symbol = custom["symbol"] if custom.get("symbol") else "script_custom_0"
    if bindings["variable"] or bindings["actor"]:
        input_symbol += "_bound"
    placeholder = "__PLACEHOLDER|" + input_symbol + b._context_hash() + "|PLACEHOLDER__"
    cache[cache_key] = {"scriptRef": placeholder, "argsLen": args_len}
    symbol = compile_sub_script(b, "custom", script, input_symbol, {"argLookup": arg_lookup},
                                [f"{where}:script[{i}]" for i in range(len(script))])
    result = {"scriptRef": symbol, "argsLen": args_len}
    o["recursiveSymbolMap"][placeholder] = symbol
    cache[cache_key] = result
    return result


def anonymize_gbvm_script(text):
    """gbvm/buildHelpers.ts anonymizeGBVMScript: the script's own name, debugger
    lines and recursion placeholders do not count when comparing scripts."""
    m = re.search(r"_([^:\s]*)::", text)
    if not m:
        return text
    name = m[1]
    text = re.sub(re.escape(name), "SCRIPT", text)
    text = re.sub(r"^(.globl |)(GBVM\$|GBVM_END\$).*$", "", text, flags=re.M)
    return re.sub(r"__PLACEHOLDER\|" + re.escape(name) + r"[^|]*\|PLACEHOLDER__", "SCRIPT", text)


def gbvm_script_checksum(text):
    """buildHelpers.ts gbvmScriptChecksum (MD5 there; any digest of the same text does)."""
    return hashlib.md5(anonymize_gbvm_script(text).encode()).hexdigest()


def compile_sub_script(b, kind, script, input_symbol, extra, sources):
    """scriptBuilderBase.ts _compileSubScript (type "custom")."""
    from .events import compile_entity_events
    o = b.options
    context = "script" if kind == "custom" else ("global" if o["context"] == "script" else o["context"])
    pre_hash = (hashlib.sha256(json.dumps(script, sort_keys=True).encode()).hexdigest()
                + f"_{b._context_hash()}_{input_symbol if kind == 'custom' else ''}")
    cache = o["additionalScriptsCache"]
    if pre_hash in cache:
        return cache[pre_hash]
    symbol = b._get_available_symbol(input_symbol if input_symbol else f"script_{kind}")
    options = {**o, **extra, "context": context, "maxDepth": o["maxDepth"] - 1}
    if kind != "custom":
        # Input, timer and state scripts start in a context of their own
        # (script_execute): the arguments of the custom script that attached
        # them are not on its stack. GB Studio 4.3.2 still addresses them
        # there and reads or writes whatever is below (docs/M2_CUSTOM_EVENTS.md).
        options["argLookup"] = {"actor": {}, "variable": {}}
    text = compile_entity_events(symbol, script if o["maxDepth"] >= 0 else [], sources, options,
                                 o["entities"], lock=False, loop=False, is_function=kind == "custom")
    if kind != "custom" and ".SCRIPT_ARG_" in text:
        where = sources[0].rsplit("[", 1)[0] if sources else f"{kind} script"
        raise CompileError(f"{where}: this {kind} script runs in its own context and uses an argument of "
                           "the custom script that sets it; only arguments the call binds (a variable "
                           "passed by reference, an actor) can be used there: a value argument does "
                           "not exist any more when the script runs (docs/M2_CUSTOM_EVENTS.md)")
    checksum = gbvm_script_checksum(text.replace(input_symbol, "SCRIPT") if input_symbol else text)
    script_hash = checksum + f"_{input_symbol if kind == 'custom' else ''}"
    if script_hash in cache:
        return cache[script_hash]
    o["additionalScripts"][symbol] = {"symbol": symbol, "compiledScript": text}
    cache[script_hash] = symbol
    cache[pre_hash] = symbol
    return symbol


# Events whose child script runs in a new context: the key of that script.
SUB_SCRIPTS = {"EVENT_SET_INPUT_SCRIPT": "true", "EVENT_SET_TIMER_SCRIPT": "script",
               "EVENT_SET_PLATFORMER_CALLBACK_SCRIPT": "script", "EVENT_THREAD_START": "true"}


def _sub_script_uses(script, names):
    """Argument ids (V0.., actor ids) mentioned inside new-context child scripts."""
    found = set()

    def mentions(value):
        if isinstance(value, str):
            return {n for n in names if value == n or value.startswith(n + ":")}
        if isinstance(value, dict):
            return set().union(*(mentions(v) for v in value.values())) if value else set()
        if isinstance(value, list):
            return set().union(*(mentions(v) for v in value)) if value else set()
        return set()

    def walk(events, inside):
        for e in events or ():
            if not isinstance(e, dict) or (e.get("args") or {}).get("__comment"):
                continue
            if inside:
                found.update(mentions(e.get("args") or {}))
            for key, child in (e.get("children") or {}).items():
                walk(child, inside or SUB_SCRIPTS.get(e.get("command")) == key)
    walk(script, False)
    return found


def sub_script_bindings(custom, input_):
    """Call-site values for the arguments a new-context child script uses.

    Bound: a variable passed by reference to a variable of this scene or the
    project (not to an argument of the caller), an actor given by id. Anything
    else stays an argument, and compile_sub_script refuses its use."""
    names = {a["id"] for a in custom["variables"].values() if a} | {a["id"] for a in custom["actors"].values() if a}
    used = _sub_script_uses(custom["script"], names)
    bindings = {"variable": {}, "actor": {}}
    for arg in custom["variables"].values():
        if not arg or arg["id"] not in used or not arg["passByReference"]:
            continue
        value = (input_ or {}).get(f"$variable[{arg['id']}]$")
        if isinstance(value, dict) and value.get("type") == "variable" and value.get("value") \
                and not is_variable_custom_event(value["value"]):
            bindings["variable"][arg["id"]] = value["value"]
        elif isinstance(value, str) and value and not is_variable_custom_event(value):
            bindings["variable"][arg["id"]] = value
    for arg in custom["actors"].values():
        if not arg or arg["id"] not in used:
            continue
        value = (input_ or {}).get(f"$actor[{arg['id']}]$")
        if isinstance(value, str) and value:
            bindings["actor"][arg["id"]] = value
    return bindings


def call_script(b, script_id, input_):
    """scriptBuilder.ts callScript."""
    custom = next((ce for ce in b.options["customEvents"] if ce["id"] == script_id), None)
    if custom is None:
        raise CompileError(f"customEventId {script_id!r}: no such script (GB Studio would only warn)")
    bindings = sub_script_bindings(custom, input_)
    compiled = compile_custom_event_script(b, custom["id"], bindings)
    script_ref, args_len = compiled["scriptRef"], compiled["argsLen"]
    b._add_comment(f"Call Script: {custom['name']}")
    actor_args = [a for a in custom["actors"].values() if not a or a["id"] not in bindings["actor"]]
    variable_args = [a for a in custom["variables"].values() if not a or a["id"] not in bindings["variable"]]

    def arg_value(kind, arg):
        # `input?.[key] || ""`: an argument never set in the editor is "".
        # Decision A9 (2026-10-01): compiled as GB Studio does, with
        # a located warning (unset_warning), because the editor SHOWS player /
        # variable 0 for it while the compiler uses something else.
        key = f"${kind}[{arg['id']}]$"
        value = input_.get(key) if input_ else None
        return value if value else ""

    def unset_warning(kind, arg, compiled):
        b.warn(f"${kind}[{arg['id']}]$ of \"{custom['name']}\" is not set: compiled as GB Studio does, "
               f"with {compiled}; the GB Studio editor shows {'player' if kind == 'actor' else 'variable 0'} "
               "for it. Set the argument explicitly to remove this warning (M2_CUSTOM_EVENTS.md)")

    const_args = {}
    for arg in variable_args:
        if not arg:
            continue
        value = arg_value("variable", arg)
        if not isinstance(value, str) and value.get("type") not in ("variable", "number", "argument"):
            from .builder import optimise_script_value, precompile_script_value
            rpn_ops, fetch_ops = precompile_script_value(optimise_script_value(value))
            ref = b._declare_local("arg", 1, True)
            if len(rpn_ops) == 1 and rpn_ops[0].get("type") == "number":
                b._set_const(ref, rpn_ops[0]["value"])
            else:
                lookup = b._perform_fetch_operations(fetch_ops)
                b._add_comment("-- Calculate value")
                rpn = b._rpn()
                b._perform_value_rpn(rpn, rpn_ops, lookup)
                rpn.ref_set(ref).stop()
            const_args[json.dumps(value, sort_keys=True)] = ref
        elif arg["passByReference"]:
            if not isinstance(value, str) and value.get("type") == "number":
                ref = b._declare_local("arg", 1, True)
                b._set_const(ref, value["value"])
                const_args[json.dumps(value, sort_keys=True)] = ref
    for arg in reversed(actor_args):
        if not arg:
            continue
        value = arg_value("actor", arg)
        if value == "":
            index, compiled = b.unset_actor_index()
            unset_warning("actor", arg, compiled)
            b._stack_push_const(index, f"Actor {arg['id']}")
        elif isinstance(value, str):
            b._stack_push_const(b.get_actor_index(value), f"Actor {arg['id']}")
        elif value.get("type") == "argument":
            b._stack_push(value["symbol"])
    for arg in reversed(variable_args):
        if not arg:
            continue
        value = arg_value("variable", arg)
        if value == "":
            # getVariableAlias(""): defaultVariableForContext (L0 of the entity,
            # V0 in a custom script, else variable 0); resolved again just below.
            unset_warning("variable", arg, f"{default_variable_for_context(b.options['context'])} "
                                           f"({b.get_variable_alias('')})")
        if arg["passByReference"]:
            if isinstance(value, str):
                b._stack_push_const(b.get_variable_alias(value), f"Variable {arg['id']}")
            elif value and value.get("type") == "variable":
                alias = b.get_variable_alias(value["value"])
                if b._is_indirect_variable(value["value"]):
                    b._stack_push(alias)
                else:
                    b._stack_push_reference(alias, f"Variable {arg['id']}")
            else:
                ref = const_args[json.dumps(value, sort_keys=True)]
                b._stack_push_reference(ref, f"Variable {arg['id']}")
                b._mark_local_use(ref)
        else:
            if isinstance(value, str):
                b._stack_push(b.get_variable_alias(value))
            elif value and value.get("type") == "number":
                b._stack_push_const(value["value"], f"Variable {arg['id']}")
            elif value and value.get("type") == "variable":
                alias = b.get_variable_alias(value["value"])
                if b._is_indirect_variable(value["value"]):
                    b._stack_push_ind(alias)
                else:
                    b._stack_push(alias)
            else:
                ref = const_args[json.dumps(value, sort_keys=True)]
                b._stack_push(ref)
                b._mark_local_use(ref)
    b._call_far(script_ref, args_len)
    b._add_nl()
