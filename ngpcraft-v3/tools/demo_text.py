"""EVENT_TEXT proof (docs/M2_TEXT.md): Cave case A, or --case-b Sample Town.

--mask-witness pins town actors near the box to test R2 hiding/restoration.

gbs2 with only Cave (case A: the front plane is free), whose On Init is
  loop: Display Dialogue (two pages, gbs2's own words), Wait W frames
The probe presses A to close each page (tools/probe_gbvm.py --inputs) and
captures the screen; the oracle is tools/gbvm_ui.py.

  python -B -m tools.demo_text --out <new dir> --toolchain C:\\t900
"""
import argparse
import hashlib
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new
from tools.demo_scene_switch import CAVE, TOWN, TOWN_START, GBS2, derive_scenes, ev, num, set_script

CAVE_START = (9, 12)
PAGES = ["It's dangerous to\ngo without docs.", "Check out\ngbstudio.dev/docs"]


def project_case(root, wait=60, pages=PAGES, extra=None, *, case_b=False, mask_witness=False):
    scene = TOWN if case_b else CAVE
    keep, ids = derive_scenes(GBS2, root / "project", [scene], scene, TOWN_START if case_b else CAVE_START)
    if mask_witness:
        if not case_b:
            raise ValueError("sprite mask witness requires case B")
        from tools.demo_gbs_scene import write_json
        for k, actor in enumerate(sorted(keep[scene].rglob("actors/*.gbsres"))):
            data = json.loads(actor.read_text(encoding="utf-8"))
            data.update(isPinned=True, coordinateType="tiles", x=3 + k * 3, y=16)
            write_json(actor, data)
    body = [ev("c-text", "EVENT_TEXT", text=list(pages), **(extra or {})),
            ev("c-wait", "EVENT_WAIT", units="frames", frames=num(wait))]
    set_script(keep[scene], [ev("c-loop", "EVENT_LOOP", __collapse=False)])
    path = keep[scene] / "scene.gbsres"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["script"][0]["children"] = {"true": body}
    from tools.demo_gbs_scene import write_json
    write_json(path, data)
    return root / "project", ids


def build_case(root, toolchain, wait=60, pages=PAGES, extra=None, *, case_b=False, mask_witness=False):
    project, ids = project_case(root, wait, pages, extra, case_b=case_b, mask_witness=mask_witness)
    manifest = export_project(project, root / "export")
    build = build_rom(root / "export", toolchain)
    return manifest, build


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--wait", type=int, default=60)
    parser.add_argument("--case-b", action="store_true", help="Sample Town: shared front plane")
    parser.add_argument("--mask-witness", action="store_true", help="place pinned actors across the window line")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    args.out.mkdir(parents=True)
    manifest, build = build_case(args.out, args.toolchain, args.wait, case_b=args.case_b, mask_witness=args.mask_witness)
    rom = args.out / "ngpcraft_m2_text_dialogue.ngc"
    write_new(rom, (args.out / "export/rom/game.ngc").read_bytes())
    print(json.dumps({"rom": str(rom), "bytes": build["header"]["size_bytes"],
                      "sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
                      "features": manifest["features"], "warnings": manifest["warnings"]}, indent=1))


if __name__ == "__main__":
    main()
