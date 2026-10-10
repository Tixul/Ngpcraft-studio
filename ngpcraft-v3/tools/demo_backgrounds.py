"""Build a static 160x152 background ROM with independent source pixels.

Diagnostic viewport only: no gameplay, priorities, implicit ninth-row policy,
or GB Studio export support is claimed. Assets are read by ngpcraft.scene_assets (no image library).
"""
import argparse
import json
from pathlib import Path

from ngpcraft.background import compile_background
from ngpcraft.background_rom import export_background_rom
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels
from ngpcraft.toolchain import build_rom
from tools.reference import REFERENCE


def synthetic_pixels():
    palettes = ((0xFFF, 0x00F, 0x0F0, 0xF00), (0xABC, 0x123, 0x456, 0x789))
    return tuple(palettes[(x//8 + y//8) & 1][((x & 7) + (y & 7)*2) & 3]
                 for y in range(152) for x in range(160))


def scene_pixels(project, scene_name):
    """Static source colors of the scene whose folder is `scene_name`."""
    project = load_project(project)
    matches = [p for p in (project.root / "project/scenes").rglob("scene.gbsres")
               if p.parent.name == scene_name]
    if len(matches) != 1:
        raise ValueError(f"scene name must identify exactly one scene: {scene_name}")
    rel = matches[0].relative_to(project.root).as_posix()
    scene_id = project.files[rel]["id"]
    scene = next(s for s in project.scenes if s["id"] == scene_id)
    return background_pixels(project, scene)


def write_new(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("xb") as stream:
        stream.write(data)
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="synthetic")
    parser.add_argument("--project", type=Path, default=REFERENCE / "appData/templates/gbs2")
    parser.add_argument("--x", type=int, default=0)
    parser.add_argument("--y", type=int, default=0)
    parser.add_argument("--front", type=int, choices=(1,2), default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    if args.scene == "synthetic":
        if args.x or args.y:
            parser.error("synthetic viewport has no offset")
        pixels = synthetic_pixels()
    else:
        w, h, source = scene_pixels(args.project, args.scene)
        if args.x < 0 or args.y < 0 or args.x+160 > w or args.y+152 > h:
            parser.error("explicit 160x152 viewport must fit entirely inside source; no padding")
        pixels = tuple(source[(args.y+y)*w+args.x+x] for y in range(152) for x in range(160))
    asset = compile_background(160, 152, pixels, planes=(args.front, 3-args.front),
                               where=f"proof:{args.scene}[{args.x},{args.y}]")
    export_background_rom(asset, args.out / "export")
    build = build_rom(args.out / "export", args.toolchain)
    write_new(args.out / "expected.rgb444", b"".join(v.to_bytes(2,"little") for v in pixels))
    rom_name = f"ngpcraft_m2_{args.scene}_{args.x}_{args.y}_scr{args.front}.ngc"
    write_new(args.out / rom_name, (args.out / "export/rom/game.ngc").read_bytes())
    write_new(args.out / "source.json", json.dumps({"scene": args.scene,
        "viewport_px": [args.x,args.y,160,152], "diagnostic_only": True,
        "excluded": ["scripts", "actors", "priority semantics", "dynamic palettes", "streaming"]}, indent=2).encode("utf-8"))
    print(json.dumps({"rom": str(args.out / rom_name), "bytes": build["header"]["size_bytes"],
                      "assets": {k:v for k,v in asset.report().items() if k != "complement_cells"},
                      "complement_cell_count": len(asset.complement_cells)}, indent=2))


if __name__ == "__main__":
    main()
