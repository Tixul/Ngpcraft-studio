"""Closed prototype IR -> validated, reachable and specialized build plan.

This is intentionally NOT a GB Studio project importer. Unknown constructs fail.
Analysis is conservative: reachable operations are retained unless proven dead.
"""
from copy import deepcopy
from dataclasses import dataclass
import re


class CompileError(ValueError):
    pass


def require(condition, where, message):
    if not condition:
        raise CompileError(f"{where}: {message}")


def fields(value, allowed, required, where):
    require(isinstance(value, dict), where, "expected an object")
    require(not (value.keys() - set(allowed)), where,
            f"unknown fields: {sorted(value.keys() - set(allowed))}")
    require(set(required) <= value.keys(), where, f"required fields: {required}")


def identifier(value, where):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value),
            where, "expected identifier [A-Za-z][A-Za-z0-9_]{0,63}")
    return value


def integer(value, low, high, where):
    require(type(value) is int and low <= value <= high, where,
            f"expected integer in [{low}, {high}]")
    return value


@dataclass
class Plan:
    project: dict
    scenes: list
    contexts: list
    globals: list
    palettes: list
    features: list
    retained: list
    removed: list


def analyze(source):
    p = deepcopy(source)
    fields(p, ["schema", "start", "flags", "globals", "palettes", "scripts", "scenes"],
           ["schema", "start", "scenes"], "project")
    require(p["schema"] == "ngpcraft-ir/0.1", "project.schema", "unsupported schema")
    for name in ("flags", "globals", "palettes", "scripts"):
        p.setdefault(name, {})
        require(isinstance(p[name], dict), name, "expected an object")
        for key in p[name]:
            identifier(key, name)
    for key, value in p["flags"].items():
        require(type(value) is bool, f"flags.{key}", "expected boolean")
    for key, value in p["globals"].items():
        integer(value, -32768, 32767, f"globals.{key}")
    for key, colors in p["palettes"].items():
        require(isinstance(colors, list) and len(colors) == 4, f"palettes.{key}", "four RGB444 words required")
        for color in colors:
            integer(color, 0, 4095, f"palettes.{key}")
    require(isinstance(p["scenes"], list) and p["scenes"], "scenes", "nonempty list required")
    scenes = {}
    for scene in p["scenes"]:
        fields(scene, ["id", "on_init", "actors"], ["id"], "scene")
        sid = identifier(scene["id"], "scene.id")
        require(sid not in scenes, sid, "duplicate scene")
        scenes[sid] = scene
        require(isinstance(scene.get("actors", []), list), sid, "actors must be a list")
        seen = set()
        for actor in scene.get("actors", []):
            fields(actor, ["id", "locals", "on_init"], ["id"], f"{sid}.actor")
            aid = identifier(actor["id"], f"{sid}.actor.id")
            require(aid not in seen, sid, "duplicate actor")
            seen.add(aid)
            require(isinstance(actor.get("locals", {}), dict), aid, "locals must be an object")
            for key, value in actor.get("locals", {}).items():
                identifier(key, aid)
                integer(value, -32768, 32767, f"{sid}.{aid}.{key}")
    require(p["start"] in scenes, "start", "unknown scene")
    removed, retained = [], []

    def variable(ref, locals_, path):
        fields(ref, ["scope", "name"], ["scope", "name"], path)
        require(ref["scope"] in ("global", "local"), path, "scope must be global or local")
        store = p["globals"] if ref["scope"] == "global" else locals_
        require(isinstance(ref["name"], str) and ref["name"] in store, path, "undeclared variable")

    def value(v, locals_, path, color=False):
        if isinstance(v, dict):
            variable(v, locals_, path)
        else:
            integer(v, 0 if color else -32768, 4095 if color else 32767, path)

    def expand(nodes, locals_, path, stack=()):
        require(isinstance(nodes, list), path, "script must be a list")
        result = []
        for i, node in enumerate(nodes):
            loc = f"{path}[{i}]"
            require(isinstance(node, dict), loc, "event must be an object")
            op = node.get("op")
            if op == "if_flag":
                fields(node, ["op", "flag", "then", "else"], ["op", "flag", "then"], loc)
                require(isinstance(node["flag"], str) and node["flag"] in p["flags"], loc, "unknown build flag")
                selected = "then" if p["flags"][node["flag"]] else "else"
                removed.append({"path": loc, "reason": f"constant branch: keep {selected}"})
                result.extend(expand(node.get(selected, []), locals_, loc + "." + selected, stack))
            elif op == "call":
                fields(node, ["op", "script"], ["op", "script"], loc)
                target = node["script"]
                require(isinstance(target, str) and target in p["scripts"], loc, "unknown script")
                require(target not in stack and len(stack) < 32, loc, "recursive/deep calls unsupported")
                retained.append({"kind": "script", "id": target, "because": loc})
                result.extend(expand(p["scripts"][target], locals_, f"scripts.{target}", stack + (target,)))
            else:
                specs = {
                    "set": ("target", "value"), "wait": ("frames",),
                    "backdrop": ("value",), "palette": ("asset", "slot"), "scene": ("target",),
                }
                require(op in specs, loc, f"unsupported event {op!r}")
                fields(node, ["op", *specs[op]], ["op", *specs[op]], loc)
                if op == "set":
                    variable(node["target"], locals_, loc + ".target")
                    value(node["value"], locals_, loc + ".value")
                elif op == "wait":
                    integer(node["frames"], 1, 65535, loc + ".frames")
                elif op == "backdrop":
                    value(node["value"], locals_, loc + ".value", color=True)
                elif op == "palette":
                    require(isinstance(node["asset"], str) and node["asset"] in p["palettes"], loc, "unknown palette")
                    integer(node["slot"], 0, 15, loc + ".slot")
                elif op == "scene":
                    require(isinstance(node["target"], str) and node["target"] in scenes, loc, "unknown scene")
                result.append({**node, "source": loc})
            # An inlined scene transition also terminates its caller.
            if result and result[-1]["op"] == "scene":
                if i + 1 < len(nodes):
                    removed.append({"path": path, "reason": "events after unconditional scene transition"})
                break
            require(len(result) <= 4096, path, "expanded script exceeds prototype limit (4096)")
        return result

    queue, included, contexts = [p["start"]], [], []
    while queue:
        sid = queue.pop(0)
        if sid in included:
            continue
        included.append(sid)
        require(len(included) <= 255, "scenes", "prototype limit: 255 reachable scenes")
        scene = scenes[sid]
        retained.append({"kind": "scene", "id": sid, "because": "entry point" if sid == p["start"] else "scene transition"})
        # Contract C10 (docs/CONTRAT_GBSTUDIO.md): ONE init thread per scene,
        # every actor's On Init in list order, then the scene's. A wait in an
        # actor's init delays the next actors and the scene.
        owners = [("actor_" + a["id"], a) for a in scene.get("actors", [])] + [("scene", scene)]
        events, locals_ = [], {}
        for n, (owner, obj) in enumerate(owners):
            for name, initial in obj.get("locals", {}).items():
                locals_[(owner, name)] = initial
            path = f"scenes.{sid}.{owner}.on_init"
            part = [{**e, "owner": owner} for e in expand(obj.get("on_init", []), obj.get("locals", {}), path)]
            events.extend(part)
            if part and part[-1]["op"] == "scene":
                # The transition ends the whole thread: later owners never run.
                rest = [o for o, x in owners[n + 1:] if x.get("on_init")]
                if rest:
                    removed.append({"path": f"scenes.{sid}", "reason": f"init of {rest} after unconditional scene transition"})
                break
        if events:
            contexts.append({"scene": sid, "locals": locals_, "events": events})
            queue.extend(e["target"] for e in events if e["op"] == "scene")

    def is_live(e, ref, live_l):
        return ref["name"] in live_g if ref["scope"] == "global" else (e["owner"], ref["name"]) in live_l

    def mark(e, ref, live_l):
        if ref["scope"] == "global":
            live_g.add(ref["name"])
        else:
            live_l.add((e["owner"], ref["name"]))

    # Propagate dependencies only from observable backdrop reads and live writes.
    live_g, live_l = set(), {i: set() for i in range(len(contexts))}
    for i, ctx in enumerate(contexts):
        for e in ctx["events"]:
            if e["op"] == "backdrop" and isinstance(e["value"], dict):
                mark(e, e["value"], live_l[i])
    changed = True
    while changed:
        before = (len(live_g), sum(map(len, live_l.values())))
        for i, ctx in enumerate(contexts):
            for e in ctx["events"]:
                if e["op"] == "set" and is_live(e, e["target"], live_l[i]) and isinstance(e["value"], dict):
                    mark(e, e["value"], live_l[i])
        changed = before != (len(live_g), sum(map(len, live_l.values())))
    final = []
    for i, ctx in enumerate(contexts):
        kept = []
        for e in ctx["events"]:
            if e["op"] == "set":
                if not is_live(e, e["target"], live_l[i]):
                    removed.append({"path": e["source"], "reason": "write to unobserved variable"})
                    continue
            kept.append(e)
        while kept and kept[-1]["op"] == "wait":
            removed.append({"path": kept.pop()["source"], "reason": "terminal wait without observable continuation"})
        if kept:
            final.append({**ctx, "locals": {k: ctx["locals"][k] for k in sorted(live_l[i])}, "events": kept})
    used_assets = sorted({e["asset"] for c in final for e in c["events"] if e["op"] == "palette"})
    features = sorted({e["op"] for c in final for e in c["events"]})
    for kind, available, used in [("scene", scenes, included), ("palette", p["palettes"], used_assets),
                                 ("global", p["globals"], live_g),
                                 ("script", p["scripts"], {x["id"] for x in retained if x["kind"] == "script"})]:
        for key in sorted(set(available) - set(used)):
            removed.append({"kind": kind, "id": key, "reason": "not reachable / not observed"})
    for c in final:
        for e in c["events"]:
            retained.append({"kind": "event", "id": e["op"], "because": e["source"]})
    return Plan(p, included, final, sorted(live_g), used_assets, features, retained, removed)
