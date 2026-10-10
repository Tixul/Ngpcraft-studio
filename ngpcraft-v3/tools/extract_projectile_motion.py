"""Freeze/check the released sine table and provenance, without rewriting GBVM.

python -B -m tools.extract_projectile_motion check | write
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from tools.extract_gbvm_model import write_json
from tools.reference import REFERENCE, GBVM_COMMIT, LABEL

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ngpcraft/runtime/projectile_motion.json"
SOURCES = ("appData/engine/gbvm/include/sincos.h",
           "appData/engine/gbvm/src/core/projectiles.c",
           "appData/engine/gbvm/LICENSE")


def extract():
    contents = [(REFERENCE / name).read_bytes() for name in SOURCES]
    match = re.search(r"static const int8_t sine_wave\[256\] = \{(.*?)\};", contents[0].decode("utf-8"), re.S)
    if not match:
        raise ValueError("released sincos.h: sine table missing")
    sine = [int(value) for value in re.findall(r"-?\d+", match[1])]
    if len(sine) != 256 or any(not -128 <= value <= 127 for value in sine):
        raise ValueError("released sine table no longer has 256 signed bytes")
    return {"reference": LABEL, "gbvm_commit": GBVM_COMMIT,
            "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in zip(SOURCES, contents)},
            "license": contents[2].decode("utf-8"), "sine_wave": sine}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "write"))
    args = parser.parse_args()
    expected = extract()
    if args.command == "write":
        write_json(OUT, expected)
    elif json.loads(OUT.read_text(encoding="utf-8")) != expected:
        raise SystemExit("projectile motion reference drift")
    print("projectile motion reference: OK")


if __name__ == "__main__":
    main()
