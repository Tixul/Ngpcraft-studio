"""Emit only selected code. No all-feature template and no feature #ifdefs."""
import hashlib
import json
from pathlib import Path

from .compiler import CompileError
from .runtime_units import select_units, hardware_source, emit_unit_files, describe_unit


def generate(plan, target="module"):
    if target not in ("module", "rom"):
        raise CompileError("target must be module or rom")
    scenes = {name: i for i, name in enumerate(plan.scenes)}
    transition = "scene" in plan.features
    header = ["/* Generated C89. Prototype module API; not a complete ROM. */",
              "#ifndef NGC_GAME_H", "#define NGC_GAME_H", "void ngc_game_init(void);",
              "void ngc_game_tick(void);"]
    runtime_h = ["/* Only the selected hardware operations are declared. */"]
    c = ['#include "game.h"']
    hardware = []
    units = select_units(plan.features, target)
    for unit in units:
        if unit.source:
            runtime_h.append(unit.declaration)
            hardware.extend(hardware_source(unit).splitlines())
    if hardware:
        c.append('#include "hardware.h"')
    if transition:
        c.extend(["static unsigned char ngc_scene;", "static unsigned char ngc_next_scene;"])
    for name in plan.globals:
        c.append(f"static short ngc_global_{name};")
    for name in plan.palettes:
        colors = ", ".join(f"{v}u" for v in plan.project["palettes"][name])
        c.append(f"static const unsigned short ngc_asset_{name}[4] = {{ {colors} }};")
    contexts = []
    for index, ctx in enumerate(plan.contexts):
        symbol = f"ngc_ctx_{index}"
        waits = any(e["op"] == "wait" for e in ctx["events"])
        c.append(f"static unsigned short {symbol}_pc;")
        if waits:
            c.append(f"static unsigned short {symbol}_wait;")
        for owner, name in ctx["locals"]:
            c.append(f"static short {symbol}_{owner}_local_{name};")
        contexts.append((symbol, ctx, waits))

    def expr(value, symbol, owner=None):
        if isinstance(value, int):
            return "(-32767 - 1)" if value == -32768 else str(value)
        return ("ngc_global_" if value["scope"] == "global" else f"{symbol}_{owner}_local_") + value["name"]

    for symbol, ctx, waits in contexts:
        c.extend([f"static void {symbol}_step(void)", "{"])
        if waits:
            c.extend([f"    if ({symbol}_wait != 0u) {{", f"        --{symbol}_wait;",
                      f"        if ({symbol}_wait != 0u) return;", "    }"])
        c.append(f"    switch ({symbol}_pc) {{")
        for i, event in enumerate(ctx["events"]):
            if i == 0 or ctx["events"][i - 1]["op"] == "wait":
                c.append(f"    case {i}u:")
            c.append(f"        /* {event['source']} */")
            op = event["op"]
            if op == "set":
                c.append(f"        {expr(event['target'], symbol, event['owner'])} = "
                         f"{expr(event['value'], symbol, event['owner'])};")
            elif op == "backdrop":
                c.append(f"        ngc_backdrop({expr(event['value'], symbol, event['owner'])});")
            elif op == "palette":
                c.append(f"        ngc_palette({event['slot']}u, ngc_asset_{event['asset']});")
            elif op == "wait":
                c.extend([f"        {symbol}_wait = {event['frames']}u;", f"        {symbol}_pc = {i + 1}u;", "        return;"])
                continue
            elif op == "scene":
                c.extend([f"        ngc_next_scene = {scenes[event['target']]}u;",
                          f"        {symbol}_pc = 65535u;", "        return;"])
                continue
            # Straight-line operations fall through, no artificial frame delay.
        if ctx["events"][-1]["op"] != "scene":
            c.extend([f"        {symbol}_pc = 65535u;", "        return;"])
        c.extend(["    default: return;", "    }", "}"])
    if transition:
        c.extend(["static void ngc_enter(unsigned char scene)", "{", "    ngc_scene = scene;",
                  "    ngc_next_scene = 255u;", "    switch (scene) {"])
        for sid, number in scenes.items():
            c.append(f"    case {number}u:")
            for symbol, ctx, waits in contexts:
                if ctx["scene"] == sid:
                    c.append(f"        {symbol}_pc = 0u;")
                    if waits:
                        c.append(f"        {symbol}_wait = 0u;")
                    for (owner, name), initial in ctx["locals"].items():
                        c.append(f"        {symbol}_{owner}_local_{name} = {expr(initial, symbol)};")
            c.append("        break;")
        c.extend(["    }", "}"])
    c.extend(["void ngc_game_init(void)", "{"])
    for name in plan.globals:
        c.append(f"    ngc_global_{name} = {expr(plan.project['globals'][name], '')};")
    if transition:
        c.append("    ngc_enter(0u);")
    else:
        for symbol, ctx, waits in contexts:
            c.append(f"    {symbol}_pc = 0u;")
            if waits:
                c.append(f"    {symbol}_wait = 0u;")
            for (owner, name), initial in ctx["locals"].items():
                c.append(f"    {symbol}_{owner}_local_{name} = {expr(initial, symbol)};")
    c.extend(["}", "void ngc_game_tick(void)", "{"])
    for symbol, ctx, waits in contexts:
        if transition:
            c.append(f"    if (ngc_scene == {scenes[ctx['scene']]}u) {{")
        c.append(f"    {symbol}_step();")
        if transition:
            c.extend(["        if (ngc_next_scene != 255u) { ngc_enter(ngc_next_scene); return; }", "    }"])
    c.append("}")
    header.append("#endif")
    files = {"src/game.h": "\n".join(header) + "\n", "src/game.c": "\n".join(c) + "\n"}
    if hardware:
        files["src/hardware.h"] = "\n".join(runtime_h) + "\n"
        files["src/hardware.c"] = '#include "hardware.h"\n' + "\n".join(hardware) + "\n"
    files.update(emit_unit_files(units))
    manifest = {
        "format": "ngpcraft-export/0.1", "target": "ngpc-c-module", "complete_rom": False,
        "sources": sorted(x for x in files if x.endswith(".c")),
        "runtime_units": [describe_unit(unit) for unit in units],
        "features": plan.features, "scenes": plan.scenes, "palettes": plan.palettes,
        "variables": {"globals": plan.globals, "locals": [
            {"scene": c["scene"], "owner": owner, "names": sorted(n for o, n in c["locals"] if o == owner)}
            for c in plan.contexts for owner in sorted({o for o, _ in c["locals"]})]},
        "retained": plan.retained, "removed": plan.removed,
        "sha256": {path: hashlib.sha256(data.encode()).hexdigest() for path, data in sorted(files.items())},
        "guarantee": "Only statically reachable selected code/assets. Conservative dynamic liveness; no universal semantic dead-code proof.",
    }
    if target == "rom":
        manifest["target"] = "ngpc-rom"
        manifest["platform"] = {"profile": "retail-ngpc", "viewport": [160, 152],
                                "initialized_ram_supported": False,
                                "required": ["cartridge-header", "vblank", "watchdog", "shutdown", "video-reset"]}
    files["export.json"] = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return files


def export(plan, destination, target="module"):
    files = generate(plan, target)
    dest = Path(destination)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("export destination must be new or empty (prevents stale code and overwrites)")
    dest.mkdir(parents=True, exist_ok=True)
    for name, contents in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")
    return json.loads(files["export.json"])
