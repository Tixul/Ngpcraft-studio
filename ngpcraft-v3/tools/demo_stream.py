"""Build a streamed-background ROM that walks a scripted scroll path.

Diagnostic only: the whole scene is compiled, the ROM advances one path entry
per frame. No actors, scripts, priorities or 144-px policy. Pillow stays in
the source reader (tools/demo_backgrounds.py).
"""
import argparse
import json
from pathlib import Path

from ngpcraft.background import compile_background
from ngpcraft.background_stream import (DEFAULT_MAX_DELTA, SCREEN_H, SCREEN_W,
                                        clamp_path, export_stream_rom)
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import scene_pixels, write_new
from tools.reference import REFERENCE


def walk(path, target, speed):
    """Straight moves at `speed` px per frame on each axis until `target`."""
    x, y = path[-1]
    while (x, y) != target:
        x += max(-speed, min(speed, target[0] - x))
        y += max(-speed, min(speed, target[1] - y))
        path.append((x, y))


def hold(path, frames):
    path.extend([path[-1]] * frames)


def default_path(width, height, max_delta):
    """Covers sub-tile moves, the 256-px ring boundary, clamps, speeds up to the
    streaming budget, and jumps beyond it (re-blit)."""
    xm, ym = width - SCREEN_W, height - SCREEN_H
    path = [(0, 0)]
    hold(path, 4)
    walk(path, (min(20, xm), 0), 1)                 # sub-tile steps
    walk(path, (xm, 0), 3)                          # right edge, ring wrap at 256
    path += [(xm + 40, -20)] * 3                    # requests past the edges: clamped
    walk(path, (xm, ym), 5)                         # down to the bottom edge
    walk(path, (xm // 2, ym // 2), 4)               # diagonal up-left
    walk(path, (0, ym // 2), 8)                     # exactly one tile per frame
    walk(path, (xm, ym), 8 * max_delta)             # the streaming budget, diagonal
    hold(path, 2)
    path.append((0, 0))                             # jump beyond the budget: re-blit
    hold(path, 2)
    path.append((xm, ym))                           # and back
    path.append((xm // 3, ym // 3))
    walk(path, (0, 0), 8 * max_delta + 8)           # one tile over the budget per frame
    path += [(-50, -50)] * 3                        # negative requests: clamped
    walk(path, (xm // 2 + 3, ym // 2 + 5), 7)
    hold(path, 4)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="sample_town")
    parser.add_argument("--project", type=Path, default=REFERENCE / "appData/templates/gbs2")
    parser.add_argument("--front", type=int, choices=(1, 2), default=1)
    parser.add_argument("--max-delta", type=int, default=DEFAULT_MAX_DELTA)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    w, h, pixels = scene_pixels(args.project, args.scene)
    asset = compile_background(w, h, pixels, planes=(args.front, 3 - args.front),
                               where=f"stream:{args.scene}")
    path = default_path(w, h, args.max_delta)
    export_stream_rom(asset, path, args.out / "export", args.max_delta, loop=True)
    build = build_rom(args.out / "export", args.toolchain)
    args.out.mkdir(exist_ok=True)
    write_new(args.out / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
    rom_name = f"ngpcraft_m2_stream_{args.scene}_scr{args.front}.ngc"
    write_new(args.out / rom_name, (args.out / "export/rom/game.ngc").read_bytes())
    write_new(args.out / "source.json", json.dumps({
        "scene": args.scene, "size_px": [w, h], "max_delta_tiles": args.max_delta,
        "path": path, "expected_scroll": clamp_path(asset, path), "loop": True,
        "diagnostic_only": True,
        "excluded": ["scripts", "actors", "priority semantics", "dynamic palettes",
                     "144-px scenes"]}).encode("utf-8"))
    print(json.dumps({"rom": str(args.out / rom_name), "bytes": build["header"]["size_bytes"],
                      "frames": len(path), "tiles": len(asset.tiles), "planes": list(asset.planes),
                      "palettes": [len(p) for p in asset.palettes]}, indent=2))


if __name__ == "__main__":
    main()
