"""Console ROM for the GBVM fades (P5): a real GB Studio project, gbs2's logo
scene with this On Init, exported and linked by the V3.

Expected on screen (no decor yet, M2): white at power-on, fade to black
(auto fade, speed 2, ~20 frames), 1 s black, fade to white (speed 1, fast),
1 s, fade to black (speed 4, slow, ~80 frames), 1 s, fade to white (speed 3),
then it stays white. Variable 3 counts the steps done (1..4) for the probe.

  python -B -m tools.demo_fades --out <new dir> --toolchain C:\\t900
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from gbs_fixture import event, make_project, number  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402

SCRIPT = [
    event("d1", "EVENT_SET_VALUE", variable="3", value=number(1)),
    event("d2", "EVENT_WAIT", time=number(1)),
    event("d3", "EVENT_FADE_OUT", speed=1),
    event("d4", "EVENT_SET_VALUE", variable="3", value=number(2)),
    event("d5", "EVENT_WAIT", time=number(1)),
    event("d6", "EVENT_FADE_IN", speed=4),
    event("d7", "EVENT_SET_VALUE", variable="3", value=number(3)),
    event("d8", "EVENT_WAIT", time=number(1)),
    event("d9", "EVENT_FADE_OUT", speed=3),
    event("d10", "EVENT_SET_VALUE", variable="3", value=number(4)),
]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path)
    a = p.parse_args()
    if a.out.exists() and any(a.out.iterdir()):
        p.error("output directory must be new or empty")
    project = make_project(a.out / "project", SCRIPT, auto_fade=2)
    export_project(project, a.out / "export")
    result = build_rom(a.out / "export", a.toolchain)
    rom = a.out / "ngpcraft_v3_fondus_v2.ngc"   # v2: GBVM statics cleared at start (v1: random colours on console)
    shutil.copyfile(a.out / "export/rom/game.ngc", rom)
    print(rom, result["header"]["size_bytes"], result["header"]["sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
