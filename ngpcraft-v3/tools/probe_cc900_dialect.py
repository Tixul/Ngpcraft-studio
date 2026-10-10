"""S1 prerequisite: compile isolated GBVM dialect features with real cc900.

This does not port GBVM, link a ROM or prove generated machine-code correctness.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from ngpcraft.compiler import CompileError
from ngpcraft.toolchain import locate
from tools.reproducible_build import tool_fingerprints, write_report


CASES = {
    "c89_control": "unsigned short probe(unsigned short x) { unsigned short y; y = x; return y + 1; }\n",
    "line_comment": "// GBVM uses C++ style comments\nunsigned short probe(void) { return 1; }\n",
    "mixed_declarations": "unsigned short probe(unsigned short x) { x++; unsigned short y; y = x; return y; }\n",
    "for_declaration": "unsigned short probe(void) { unsigned short sum; sum = 0; for (unsigned short i = 0; i < 4; ++i) sum += i; return sum; }\n",
    "stdbool_header": "#include <stdbool.h>\nbool probe(bool value) { return !value; }\n",
    "variadic_macro": "#define APPLY(fn, ...) fn(__VA_ARGS__)\nunsigned short plus(unsigned short x) { return x + 1; }\nunsigned short probe(void) { return APPLY(plus, 2); }\n",
    "sdcc_attributes": "unsigned short probe(unsigned short x) __banked { return x; }\n",
    "c89_widen_before_shift": "unsigned short probe(unsigned char x) { return ((unsigned short)x) << 9; }\n",
}


def probe(toolchain):
    fingerprints = tool_fingerprints(toolchain)
    info = locate(toolchain)
    env = os.environ.copy()
    env["THOME"] = info["root"]
    env["PATH"] = info["bin"] + os.pathsep + env.get("PATH", "")
    rows = []
    with tempfile.TemporaryDirectory(prefix="ngpc-v3-dialect-") as temp:
        for name, source in CASES.items():
            path = Path(temp) / (name + ".c")
            # New temporary fixtures only; no user files or runtime sources edited.
            path.write_text(source, encoding="ascii")
            obj = path.with_suffix(".rel")
            args = [str(Path(info["bin"]) / "cc900.exe"), "-c", "-O3", "-Nb2", str(path), "-o", str(obj)]
            result = subprocess.run(args, cwd=info["bin"], env=env, capture_output=True,
                                    text=True, errors="replace", timeout=120)
            rows.append({"case": name, "source": source,
                         "source_sha256": hashlib.sha256(source.encode("ascii")).hexdigest(),
                         "accepted": result.returncode == 0 and obj.is_file(),
                         "returncode": result.returncode, "diagnostic": result.stdout + result.stderr})
    return {"check": "cc900-dialect", "flags": ["-c", "-O3", "-Nb2"],
            "tool_sha256": fingerprints, "cases": rows,
            "toolchain_unchanged": fingerprints == tool_fingerprints(toolchain),
            "scope": "Compilation only. No ROM execution, ABI, performance or GBVM compatibility claim."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise CompileError("report already exists; choose a new output")
        report = probe(args.toolchain)
        report["command"] = [sys.executable, "-B", "-m", "tools.probe_cc900_dialect", *sys.argv[1:]]
        write_report(args.out, report)
        for row in report["cases"]:
            print(row["case"], "accepted" if row["accepted"] else "rejected")
        control = next(row for row in report["cases"] if row["case"] == "c89_control")
        return 0 if control["accepted"] and report["toolchain_unchanged"] else 1
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
