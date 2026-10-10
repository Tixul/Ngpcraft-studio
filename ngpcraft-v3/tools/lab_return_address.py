"""Build an isolated S1 ABI laboratory; never added to the event catalogue.

Use --negative-control to deliberately discard the high address word.
Observe the ROM with tools/probe_rom.py: green=pass, red=fail.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from ngpcraft.compiler import analyze, CompileError
from ngpcraft.emitter import generate
from ngpcraft.toolchain import build_rom

ROOT = Path(__file__).resolve().parents[1]


def build(destination, toolchain, negative=False):
    dest = Path(destination)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("laboratory destination must be new or empty")
    files = generate(analyze({"schema": "ngpcraft-ir/0.1", "start": "lab",
                              "scenes": [{"id": "lab"}]}), "rom")
    source = (ROOT / "labs/s1_return_address.c").read_text(encoding="utf-8")
    if negative:
        needle = "(unsigned short)(value >> 16)"
        if source.count(needle) != 1:
            raise CompileError("negative control no longer matches laboratory source")
        source = source.replace(needle, "0u /* deliberate address truncation */")
    # Assemble the lab in memory, then fingerprint and publish once. No post-export patch.
    files["src/game.c"] = source
    manifest = json.loads(files.pop("export.json"))
    manifest["laboratory"] = {"name": "s1-return-address", "negative_control": negative,
                               "expected_palette": 15 if negative else 240}
    manifest["guarantee"] = "Disposable hand-authored ABI laboratory; not an author-project export or GBVM implementation."
    manifest["sha256"] = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in files.items()}
    files["export.json"] = json.dumps(manifest, indent=2) + "\n"
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
            f.write(text)
            tmp = f.name
        os.replace(tmp, path)
    return build_rom(dest, toolchain)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--toolchain", type=Path)
    p.add_argument("--negative-control", action="store_true")
    args = p.parse_args()
    try:
        result = build(args.out, args.toolchain, args.negative_control)
        print(json.dumps(result["header"], indent=2))
        return 0
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
