"""Modal EVENT_TEXT in shared Sample Town and free-front Cave, then switch back.

The ROM keeps the real GBVM UI lifecycle and the scenes' original assets.
Inputs close two pages in each scene. Probe with duty-cycle 0.25 and check
each scene independently using tools.check_text (town: committed-map).
"""
import argparse
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import write_json
from tools.demo_scene_switch import (GBS2, TOWN, CAVE, TOWN_START, TOWN_ARRIVAL,
                                     CAVE_ARRIVAL, derive_scenes, ev, num, set_script, switch)
from tools.demo_text import PAGES


def project_case(root):
    keep, ids = derive_scenes(GBS2, root / "project", [TOWN, CAVE], TOWN, TOWN_START)
    for name, target, arrival in ((TOWN, CAVE, CAVE_ARRIVAL), (CAVE, TOWN, TOWN_ARRIVAL)):
        set_script(keep[name], [ev(name + "-text", "EVENT_TEXT", text=PAGES),
                                ev(name + "-pause", "EVENT_WAIT", units="frames", frames=num(20)),
                                switch(name + "-switch", ids[target], arrival, "0")])
    return root / "project", ids


def build_case(root, toolchain):
    project, _ = project_case(root)
    manifest = export_project(project, root / "export")
    report = build_rom(root / "export", toolchain)
    write_json(root / "inputs.json", [[k, k + 8, 16] for k in range(80, 901, 100)])
    return manifest, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be new")
    args.out.mkdir(parents=True)
    manifest, report = build_case(args.out, args.toolchain)
    print({"bytes": report["header"]["size_bytes"], "features": manifest["features"]})


if __name__ == "__main__":
    main()
