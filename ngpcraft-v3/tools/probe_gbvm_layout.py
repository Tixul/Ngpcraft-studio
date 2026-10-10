"""S1: measure GBVM context/heap layout using cc900 and the Toshiba link map.

This is a disposable ABI experiment, never an exported runtime or a working VM.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from ngpcraft.compiler import CompileError
from ngpcraft.toolchain import locate
from tools.reproducible_build import tool_fingerprints, write_report


def extract(header):
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", header, flags=re.S)
    match = re.search(r"typedef struct SCRIPT_CTX\s*\{[^}]+\} SCRIPT_CTX;", clean)
    if not match:
        raise CompileError("GBVM SCRIPT_CTX declaration missing or unsupported")
    names = ("VM_MAX_CONTEXTS", "VM_CONTEXT_STACK_SIZE", "VM_HEAP_SIZE")
    constants = {}
    for name in names:
        found = re.findall(r"^#define " + name + r"[ \t]+([0-9]+)[ \t]*$", clean, re.M)
        if len(found) != 1 or int(found[0]) <= 0:
            raise CompileError("unsupported or missing GBVM constant: " + name)
        constants[name] = int(found[0])
    return match[0], constants


METRICS = {
    "pointer": "sizeof(void *)",
    "word": "sizeof(UWORD)",
    "context": "sizeof(SCRIPT_CTX)",
    "contexts": "sizeof(SCRIPT_CTX) * VM_MAX_CONTEXTS",
    "heap": "sizeof(UWORD) * VM_HEAP_SIZE",
    "thread_stacks": "sizeof(UWORD) * VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE",
    "script_memory": "sizeof(UWORD) * (VM_HEAP_SIZE + VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE)",
}


def parse_map(text, metrics=None):
    metrics = METRICS if metrics is None else metrics
    rows = re.findall(r"^\s*ram\s+far_area\s+DATA\s+[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+f_area\s+\(([^)]+)\)", text, re.M)
    result = {}
    for length, filename in rows:
        name = filename.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".rel")
        if name not in metrics or name in result:
            raise CompileError("unexpected or duplicate layout object: " + name)
        result[name] = int(length, 16)
    if set(result) != set(metrics) or any(n <= 0 for n in result.values()):
        raise CompileError("incomplete layout map")
    return result


def probe(reference, toolchain):
    reference = Path(reference)
    header = (reference / "include/vm.h").read_bytes()
    license_text = (reference / "LICENSE").read_text(encoding="utf-8")
    declaration, constants = extract(header.decode("utf-8-sig"))
    prefix = "/* Derived from GBVM include/vm.h. MIT, Copyright (c) 2020 Toxa. */\n"
    prefix += "typedef unsigned char UBYTE;\ntypedef unsigned short UWORD;\n"
    prefix += declaration + "\n"
    prefix += "".join("#define %s %d\n" % pair for pair in constants.items())
    measurement = measure_layout(prefix, METRICS, toolchain)
    sizes = measurement["sizes_bytes"]
    lower_bound = sizes["contexts"] + sizes["script_memory"]
    return {"check": "gbvm-cc900-layout", "vm_header_sha256": hashlib.sha256(header).hexdigest(),
            "license": license_text, "constants": constants, "sizes_bytes": sizes,
            "contexts_and_script_memory_bytes": lower_bound,
            "work_ram_bytes": 0x2C00, "remaining_before_other_state_and_c_stack_bytes": 0x2C00 - lower_bound,
            **measurement,
            "scope": "Preserved GBVM fields including banks; UBYTE=u8, UWORD=u16, native pointers. "
                     "Excludes VM globals, actors, engine, video buffers and C stack. No performance or execution proof."}



def measure_layout(prefix, metrics, toolchain):
    """Compile sizeof allocations with Toshiba; no runtime or host ABI inference."""
    fingerprints = tool_fingerprints(toolchain)
    info = locate(toolchain)
    env = os.environ.copy()
    env["THOME"] = info["root"]
    env["PATH"] = info["bin"] + os.pathsep + env.get("PATH", "")
    sources, logs = {}, []
    with tempfile.TemporaryDirectory(prefix="ngpc-v3-layout-") as tmp:
        dest = Path(tmp)
        objects = []
        for name, expression in metrics.items():
            source = prefix + "unsigned char measurement_" + name + "[" + expression + "];\n"
            sources[name] = source
            path = dest / (name + ".c")
            path.write_text(source, encoding="utf-8")
            obj = path.with_suffix(".rel")
            command = [str(Path(info["bin"]) / "cc900.exe"), "-c", "-O3", "-Nb2", str(path), "-o", str(obj)]
            run = subprocess.run(command, cwd=info["bin"], env=env, capture_output=True,
                                 text=True, errors="replace", timeout=120)
            logs.append({"case": name, "returncode": run.returncode, "output": run.stdout + run.stderr})
            if run.returncode or not obj.exists():
                raise CompileError("layout compile failed: " + json.dumps(logs[-1]))
            objects.append(str(obj))
        # Laboratory allocations overlap in meaning; they are not a game RAM layout.
        lcf = dest / "layout.lcf"
        lcf.write_text("memory { ram(RW) : org=0x004000, len=0x002C00 }\n"
                       "sections { far_area org=0x004000 : {*(f_area)} }\n", encoding="ascii")
        run = subprocess.run([str(Path(info["bin"]) / "tulink.exe"), "-la", "-o", "layout.abs", str(lcf), *objects],
                             cwd=dest, capture_output=True, text=True, errors="replace", timeout=120)
        if run.returncode:
            raise CompileError("layout link failed: " + run.stdout + run.stderr)
        map_text = (dest / "layout.map").read_text(errors="replace")
        sizes = parse_map(map_text, metrics)
    return {"sizes_bytes": sizes, "sources": sources, "compile_logs": logs,
            "link_map": map_text, "tool_sha256": fingerprints,
            "toolchain_unchanged": fingerprints == tool_fingerprints(toolchain)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gbvm", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise CompileError("report already exists; choose a new output")
        report = probe(args.gbvm, args.toolchain)
        report["command"] = [sys.executable, "-B", "-m", "tools.probe_gbvm_layout", *sys.argv[1:]]
        write_report(args.out, report)
        print(json.dumps({"sizes_bytes": report["sizes_bytes"], "minimum_bytes": report["contexts_and_script_memory_bytes"]}, indent=2))
        return 0 if report["toolchain_unchanged"] else 1
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
