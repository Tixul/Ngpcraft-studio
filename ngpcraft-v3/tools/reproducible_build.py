"""M0: compare two independently exported and linked ROMs, byte for byte.

Run from the project root with python -B -m tools.reproducible_build.
No emulator is used: this proves build reproducibility, not execution.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from ngpcraft.compiler import analyze, CompileError
from ngpcraft.emitter import export
from ngpcraft.toolchain import build_rom, locate, TOOLS


def tool_fingerprints(toolchain):
    info = locate(toolchain)
    if not info["ready"]:
        raise CompileError("Toshiba toolchain incomplete: " + ", ".join(info["missing"]))
    return {name: hashlib.sha256((Path(info["bin"]) / name).read_bytes()).hexdigest()
            for name in TOOLS}


def compare(project, toolchain):
    fingerprints = tool_fingerprints(toolchain)
    runs, binaries = [], []
    with tempfile.TemporaryDirectory(prefix="ngpc-v3-repro-") as temp:
        for name in ("first", "second path with spaces"):
            dest = Path(temp) / name
            export(analyze(project), dest, "rom")
            build = build_rom(dest, toolchain)
            binary = (dest / "rom/game.ngc").read_bytes()
            binaries.append(binary)
            runs.append({"size_bytes": len(binary),
                         "sha256": hashlib.sha256(binary).hexdigest(),
                         "export_sha256": hashlib.sha256((dest / "export.json").read_bytes()).hexdigest(),
                         "link_audit": build["link_audit"]})
    before, after = binaries
    first_difference = next((i for i, (a, b) in enumerate(zip(before, after)) if a != b), None)
    if first_difference is None and len(before) != len(after):
        first_difference = min(len(before), len(after))
    return {"check": "two-independent-builds", "tool_sha256": fingerprints,
            "toolchain_unchanged": fingerprints == tool_fingerprints(toolchain),
            "rom_equal": before == after, "first_difference_offset": first_difference,
            "export_equal": runs[0]["export_sha256"] == runs[1]["export_sha256"],
            "runs": runs,
            "scope": "Same input and tools, two clean directories; no execution or console proof."}


def write_report(path, report):
    """Atomic report publication; refuse any existing destination."""
    path = Path(path)
    if path.exists():
        raise CompileError("report already exists; choose a new output")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        json.dump(report, f, indent=2)
        f.write("\n")
    try:
        # Exclusive reservation prevents replacing an existing user file.
        with path.open("xb"):
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--toolchain", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise CompileError("report already exists; choose a new output")
        raw = args.project.read_bytes()
        report = compare(json.loads(raw.decode("utf-8-sig")), args.toolchain)
        report["project_sha256"] = hashlib.sha256(raw).hexdigest()
        report["command"] = [sys.executable, "-B", "-m", "tools.reproducible_build", *sys.argv[1:]]
        write_report(args.out, report)
        ok = report["rom_equal"] and report["export_equal"] and report["toolchain_unchanged"]
        print("reproducible" if ok else "NOT reproducible")
        return 0 if ok else 1
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
