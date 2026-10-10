"""ngpc.frame_rate 30 against 60: the same project shows the same pictures.

Two exports of tools/lab_combined_load.py's project, one per frame rate, no
buttons, compared before the narrator's first dialogue: N actors walk (On
Update Move To + Wait) and animate. At 30 per second the whole GBVM frame runs
for each picture and one picture in two is shown, so tick k must equal, pixel
for pixel, entry 2k + offset at 60 (offset: the boot alignment, found once and
then required everywhere). Independent of every oracle.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.lab_combined_load import build_case, WAIT  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def shots(root, toolchain, emulator, bios, workers, rate, entries):
    root.mkdir(parents=True)
    build_case(root, toolchain, workers, frame_rate=rate)
    write_json(root / "inputs.json", [])
    subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(root / "export"),
                    "--emulator", str(emulator), "--bios", str(bios), "--out", str(root / "probe"),
                    "--frames", str(entries), "--inputs", str(root / "inputs.json"),
                    "--shots", ",".join(map(str, range(1, entries + 1))), "--duty-cycle", "0.25"],
                   cwd=ROOT, check=True, capture_output=True)
    return {k: (root / "probe" / f"shot-{k:04d}.ppm").read_bytes() for k in range(1, entries + 1)}


def compare(out, toolchain, emulator, bios, workers=7, mutate_30=None):
    """mutate_30(shots_at_30): test hook, applied before the comparison."""
    at60 = shots(out / "r60", toolchain, emulator, bios, workers, 60, WAIT - 10)
    at30 = shots(out / "r30", toolchain, emulator, bios, workers, 30, (WAIT - 10) // 2)
    if mutate_30:
        mutate_30(at30)

    def diff(a, b):
        return sum(x != y for x, y in zip(a, b)) // 3

    last = max(at30)
    offset = min(range(-3, 4), key=lambda o: sum(diff(at30[k], at60[2 * k + o]) for k in range(10, 20)))
    rows = [(k, diff(at30[k], at60[2 * k + offset])) for k in range(3, last + 1) if 2 * k + offset in at60]
    bad = [(k, d) for k, d in rows if d]
    return {"workers": workers, "offset": offset, "compared_ticks": len(rows),
            "distinct_pictures_at_30": len({at30[k] for k in range(3, last + 1)}),
            "ticks_with_differences": bad[:20], "identical": not bad}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--workers", type=int, default=7)
    args = p.parse_args()
    if args.out.exists():
        p.error("output must be new")
    report = compare(args.out, args.toolchain, args.emulator, args.bios, args.workers)
    temp = args.out / "frame_rate.json.tmp"
    temp.write_text(json.dumps(report, indent=1), encoding="utf-8")
    temp.replace(args.out / "frame_rate.json")
    print(json.dumps(report))
    if not report["identical"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
