"""Every variable with static storage of an export's C sources (docs/M5_SAVE.md step 3).

Clang reads each generated source (cc900 dialect neutralised: `__interrupt`,
`__asm(...)`) and its AST lists the variables that live across frames:
globals, file statics and function statics, `const` ones excluded (ROM).
This is the mechanical list the save classification (ngpcraft/save_state.py)
must cover entirely: nothing mutable is left out by forgetting it.

  python -B -m tools.state_inventory <export_dir> --clang <clang.exe>
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def _walk(node, function, out, source, aliases=None):
    if aliases is None:
        aliases = {}
    kind = node.get("kind")
    if kind == "TypedefDecl":
        aliases[node["name"]] = node.get("type", {}).get("qualType", "")
    if kind == "FunctionDecl":
        function = node.get("name")
    if kind == "VarDecl" and node.get("loc", {}).get("includedFrom") is None:
        static = node.get("storageClass") == "static"
        file_scope = function is None
        types = node.get("type", {})
        qual = types.get("desugaredQualType", types.get("qualType", ""))
        # Clang omits desugaredQualType on arrays of a typedef, including
        # typedefs of const elements. Resolve the names before testing const.
        for _ in range(len(aliases) + 1):
            expanded = re.sub(r"\b[A-Za-z_]\w*\b", lambda m: aliases.get(m[0], m[0]), qual)
            if expanded == qual:
                break
            qual = expanded
        if (file_scope or static) and node.get("storageClass") != "extern":
            out.append({"source": source, "function": function, "name": node["name"], "type": qual,
                        "static": static or not file_scope, "const": _is_const(qual)})
    for child in node.get("inner", []) or []:
        _walk(child, function, out, source, aliases)


def _is_const(qual):
    """Top-level const: `const T x`, `T * const p`, arrays of const."""
    base = re.sub(r"\[[^\]]*\]", "", qual).strip()
    if base.endswith("const") or re.search(r"\(\s*\*\s*const\s*\)", base):
        return True
    return "*" not in base and base.startswith("const ")


def inventory(export_dir, clang):
    src = Path(export_dir) / "src"
    clang = Path(clang)
    if not src.is_dir():
        raise RuntimeError(f"export sources not found: {src}")
    out = []
    paths = sorted(src.glob("*.c"))
    if not paths:
        raise RuntimeError(f"no C sources found: {src}")
    for path in paths:
        proc = subprocess.run([str(clang), "--target=wasm32", "-fsyntax-only", "-w", "-Xclang", "-ast-dump=json",
                               "-D__interrupt=", "-D__asm(x)=", "-I" + str(src), str(path)],
                              capture_output=True, timeout=300)
        # A partial AST after a parse error cannot prove complete coverage.
        if proc.returncode:
            raise RuntimeError(f"clang could not read {path.name}: {proc.stderr.decode(errors='replace')[-1200:]}")
        try:
            tree = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(f"clang could not read {path.name}: {proc.stderr.decode(errors='replace')[-800:]}")
        _walk(tree, None, out, path.name)
    return [v for v in out if not v["const"]]


def target_sizes(export_dir, variables, toolchain):
    """Measure saved globals with cc900 itself, without modifying the export.

    Each owning C unit gets a temporary ROM table of sizeof expressions.
    -S evaluates it for TLCS-900 (int=2, pointer=4); no host ABI estimates.
    This is an audit artifact, never code linked into the user's game.
    """
    from ngpcraft.toolchain import _compiler_lock, locate
    info = locate(toolchain)
    if not info["ready"]:
        raise RuntimeError("Toshiba toolchain incomplete: " + ", ".join(info["missing"]))
    if os.name != "nt":
        raise RuntimeError("target size measurement currently requires Windows")
    compiler = Path(info["bin"]) / "cc900.exe"
    src = Path(export_dir).resolve() / "src"
    selected = [v for v in variables if v["decision"] == "save"]
    sizes = {}
    env = os.environ.copy()
    env["THOME"] = info["root"]
    env["PATH"] = info["bin"] + os.pathsep + env.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="ngc-save-size-") as tmp:
        folder = Path(tmp)
        for source in sorted({v["source"] for v in selected}):
            entries = [v for v in selected if v["source"] == source]
            if any(v["function"] is not None for v in entries):
                raise RuntimeError(f"saved function static cannot be addressed: {source}")
            path = folder / source
            code = (src / source).read_text(encoding="utf-8")
            code += "\nconst unsigned short ngc_state_sizes[] = {\n"
            code += ",\n".join(v["sizeof_expression"] for v in entries) + "\n};\n"
            path.write_text(code, encoding="utf-8")
            with _compiler_lock():
                proc = subprocess.run([str(compiler), "-S", "-O3", "-Nb2", "-I" + str(src), str(path)],
                                      cwd=folder, env=env, capture_output=True, timeout=120)
            asm = path.with_suffix(".asm")
            if proc.returncode or not asm.exists():
                raise RuntimeError(f"cc900 sizeof failed for {source}: "
                                   + (proc.stdout + proc.stderr).decode(errors="replace")[-1200:])
            values = _size_words(asm.read_text(encoding="utf-8", errors="replace"))
            if len(values) != len(entries):
                raise RuntimeError(f"cc900 sizeof table length mismatch: {source}, "
                                   f"{len(values)} words for {len(entries)} variables; "
                                   + asm.read_text(errors="replace").split("_ngc_state_sizes:", 1)[-1][:1200])
            for variable, size in zip(entries, values):
                sizes[(source, variable["name"])] = size
    return {"compiler_sha256": hashlib.sha256(compiler.read_bytes()).hexdigest(),
            "variables": [{**v, "target_bytes": sizes[(v["source"], v["name"])]}
                          if v["decision"] == "save" else v for v in variables],
            "raw_state_max_bytes": sum(sizes.values()),
            "size_basis": "cc900 sizeof; includes all allocated stacks, excludes serialization metadata"}


def _size_words(assembly):
    """The public cc900 constant table; refuse an unexpected directive."""
    if assembly.count("_ngc_state_sizes:") != 1:
        raise RuntimeError("cc900 sizeof table missing or duplicated")
    lines = assembly.split("_ngc_state_sizes:", 1)[1].splitlines()
    values = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if re.fullmatch(r"align\s+\d+", line):
            continue
        words = re.fullmatch(r"dw\s+(.+)", line)
        if words is None:
            break
        for token in words[1].split(","):
            if not re.fullmatch(r"\d+", token.strip()):
                raise RuntimeError(f"unexpected cc900 sizeof word: {token}")
            values.append(int(token))
    return values


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("export_dir")
    p.add_argument("--clang", required=True)
    p.add_argument("--check", action="store_true", help="reject any unclassified mutable variable")
    p.add_argument("--json", action="store_true", help="print decisions, reasons and target sizeof expressions")
    p.add_argument("--toolchain", help="measure saved globals using the user-supplied cc900")
    p.add_argument("--vm-packed", action="store_true", help="check the NV1 packed VM budget (requires --toolchain)")
    a = p.parse_args()
    if a.vm_packed and not a.toolchain:
        p.error("--vm-packed requires --toolchain")
    variables = inventory(a.export_dir, a.clang)
    if a.check or a.json or a.toolchain:
        from ngpcraft.save_state import describe
        variables = describe(variables, a.export_dir)
    if a.toolchain:
        report = target_sizes(a.export_dir, variables, a.toolchain)
        if a.vm_packed:
            from tools.save_vm import budget
            manifest = json.loads((Path(a.export_dir) / "export.json").read_text(encoding="utf-8"))
            report["packed_vm_budget"] = budget(report, manifest)
        print(json.dumps(report, indent=2))
        return
    if a.json:
        print(json.dumps(variables, indent=2))
        return
    for v in variables:
        where = f"{v['source']}:{v['function'] + '()' if v['function'] else ''}"
        print(f"{where:40} {v['name']:32} {v['type']}")


if __name__ == "__main__":
    main()
