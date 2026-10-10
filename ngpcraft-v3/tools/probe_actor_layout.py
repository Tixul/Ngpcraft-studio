"""S1 actor ABI sizing from external GBVM declarations, never a runtime port."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from ngpcraft.compiler import CompileError
from tools.probe_gbvm_layout import measure_layout
from tools.reproducible_build import write_report


def declaration(text, name, kind="struct"):
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    pattern = r"typedef\s+" + kind + r"\s*(?:" + re.escape(name) + r"\s*)?\{[^{}]*\}\s*" + re.escape(name) + r"\s*;"
    matches = re.findall(pattern, clean)
    if len(matches) != 1:
        raise CompileError("missing, duplicate or unsupported declaration: " + name)
    return matches[0]


def capacity(text, name):
    matches = re.findall(r"^#define\s+" + name + r"[ \t]+([0-9]+)[ \t\r]*$", text, re.M)
    if len(matches) != 1 or int(matches[0]) <= 0:
        raise CompileError("missing or unsupported actor capacity: " + name)
    return int(matches[0])


METRICS = {
    "pointer": "sizeof(void *)",
    "direction": "sizeof(direction_e)",
    "position": "sizeof(upoint16_t)",
    "bounds": "sizeof(rect16_t)",
    "animation": "sizeof(animation_t)",
    "far_pointer": "sizeof(far_ptr_t)",
    "actor": "sizeof(actor_t)",
    "actor_pool": "sizeof(actor_t) * MAX_ACTORS",
    "active_capacity_equivalent": "sizeof(actor_t) * MAX_ACTORS_ACTIVE",
    "list_heads": "sizeof(actor_t *) * 3",
}


def probe(reference, toolchain):
    reference = Path(reference)
    names = ("include/gbs_types.h", "include/math.h", "include/bankdata.h", "include/actor.h", "LICENSE")
    raw = {name: (reference / name).read_bytes() for name in names}
    texts = {name: data.decode("utf-8-sig") for name, data in raw.items()}
    declarations = [
        declaration(texts["include/math.h"], "upoint16_t"),
        declaration(texts["include/math.h"], "rect16_t"),
        declaration(texts["include/math.h"], "direction_e", "enum"),
        declaration(texts["include/bankdata.h"], "far_ptr_t"),
        declaration(texts["include/gbs_types.h"], "animation_t"),
        declaration(texts["include/gbs_types.h"], "actor_t"),
    ]
    constants = {name: capacity(texts["include/actor.h"], name) for name in ("MAX_ACTORS", "MAX_ACTORS_ACTIVE")}
    if constants["MAX_ACTORS_ACTIVE"] > constants["MAX_ACTORS"]:
        raise CompileError("active actor capacity exceeds actor pool")
    prefix = "/* Extracted GBVM types. MIT, Copyright (c) 2020 Toxa. */\n"
    prefix += "typedef unsigned char uint8_t;\ntypedef unsigned char UBYTE;\n"
    prefix += "typedef unsigned short uint16_t;\ntypedef short int16_t;\n"
    prefix += "\n".join(declarations) + "\n"
    prefix += "".join("#define %s %d\n" % pair for pair in constants.items())
    result = measure_layout(prefix, METRICS, toolchain)
    sizes = result["sizes_bytes"]
    return {
        "check": "gbvm-actor-cc900-layout",
        "constants": constants,
        "reference_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()},
        "license": texts["LICENSE"],
        "actor_pool_and_list_heads_bytes": sizes["actor_pool"] + sizes["list_heads"],
        **result,
        "scope": "Native cc900 enum/pointer ABI; original fields and bank bytes preserved. "
                 "21-slot pool includes player. Active capacity equivalent is NOT a second allocation. "
                 "Excludes other actor globals, VM, rendering buffers, physics state and C stack. "
                 "No actor execution, cycles, GB binary compatibility or product architecture decision.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gbvm", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise CompileError("report already exists; choose a new output")
        result = probe(args.gbvm, args.toolchain)
        result["command"] = [sys.executable, "-B", "-m", "tools.probe_actor_layout", *sys.argv[1:]]
        write_report(args.out, result)
        print(json.dumps(result["sizes_bytes"], indent=2))
        return 0 if result["toolchain_unchanged"] else 1
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
