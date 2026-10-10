"""Build a ROM with a streamed gbs2 background and scripted gbs2 actors.

Diagnostic only: actors follow a scripted table (no GBVM actor logic yet),
the camera never stops so every step is identified by its scroll position.
Covers: animated and flipped (left-facing) frames, partial clipping on all
four edges, fully off-screen actors, an actor that disappears (hidden slots),
overlapping actors (first listed on top), 8x16 sheets of 16x16, 16x32, 80x48.
"""
import argparse
import json
import math
from pathlib import Path

from ngpcraft.background import compile_background
from ngpcraft.background_stream import SCREEN_H, SCREEN_W, clamp_path
from ngpcraft.scene_sprites import export_scene_rom, frame_ids
from ngpcraft.project import load_project
from ngpcraft.scene_assets import sprite_palettes as scene_sprite_palettes
from ngpcraft.sprites import ngpc_sheet
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import scene_pixels, write_new
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets

ACTORS = ("player", "npc001", "cat", "npc_gardener", "elephant")


def sprite_palettes(project, scene_name):
    """ngpcraft.scene_assets.sprite_palettes for the scene whose folder is `scene_name`."""
    project = load_project(project)
    matches = [p for p in (project.root / "project/scenes").rglob("scene.gbsres")
               if p.parent.name == scene_name]
    scene_id = project.files[matches[0].relative_to(project.root).as_posix()]["id"]
    return scene_sprite_palettes(project, next(s for s in project.scenes if s["id"] == scene_id))


def script(width, height, frames_of, steps=240):
    """[(scroll x, scroll y, [(name, world x, world y, anim, k) | None, ...]), ...]"""
    xm, ym = width - SCREEN_W, height - SCREEN_H
    out = []
    for t in range(steps):
        a = 2 * math.pi * t / steps
        cx = int(xm / 2 + (xm / 2 - 8) * math.cos(a))
        cy = int(ym / 2 + (ym / 2 - 8) * math.sin(a))
        if t in (100, 170):                       # jumps: re-blit frames with actors
            cx, cy = xm - cx, ym - cy
        cx = min(max(cx, 0), xm) + (t & 1)        # never stops, even on a clamp
        cy = min(max(cy, 0), ym)
        walk = (t // 30) % 4                      # engine anims: 4 down 5 right 6 up 7 left (moving)
        px = cx + 72 + (20 if walk == 1 else -20 if walk == 3 else 0) * ((t % 30) / 30)
        py = cy + 80 + (20 if walk == 0 else -20 if walk == 2 else 0) * ((t % 30) / 30)
        actors = [("player", int(px), int(py), 4 + walk, (t // 6) % frames_of("player", 4 + walk)),
                  ("npc001", 200, 120, 0, 0),                        # fixed in the world: leaves the screen
                  ("cat", int(cx + 76), int(py) + 4, 1, 0),          # overlaps the player (drawn under it)
                  ("npc_gardener", 300, int(150 + 100 * math.sin(3 * a)), 3, 0),
                  None if 60 <= t < 80 else                          # disappears: hidden slots
                  ("elephant", int(cx - 60 + (t * 3) % 300), int(cy + 60 + (t % 90)), 0, 0)]
        out.append((cx, cy, actors))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="sample_town")
    parser.add_argument("--project", type=Path, default=REFERENCE / "appData/templates/gbs2")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    w, h, pixels = scene_pixels(args.project, args.scene)
    background = compile_background(w, h, pixels, where=f"scene:{args.scene}")
    settings = json.loads((args.project / "project/settings.gbsres").read_text(encoding="utf-8"))
    mode = settings["spriteMode"]
    loaded = load_sheets(args.project)
    sheets = [ngpc_sheet(*loaded[name], mode) for name in ACTORS]
    ids = frame_ids(sheets)
    count = lambda name, anim: (lambda s: s[1] - s[0] + 1)(sheets[ACTORS.index(name)].animation_offsets[anim])
    steps = script(w, h, count)
    rom_steps = [(x, y, [(0xFFFF, 0, 0) if a is None else
                         (ids[(ACTORS.index(a[0]), a[3], a[4])], a[1], a[2]) for a in actors])
                 for x, y, actors in steps]
    palettes = sprite_palettes(args.project, args.scene)
    export_scene_rom(background, sheets, palettes, rom_steps, args.out / "export", loop=True)
    build = build_rom(args.out / "export", args.toolchain)
    args.out.mkdir(exist_ok=True)
    write_new(args.out / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
    rom_name = f"ngpcraft_m2_actors_{args.scene}.ngc"
    write_new(args.out / rom_name, (args.out / "export/rom/game.ngc").read_bytes())
    write_new(args.out / "source.json", json.dumps({
        "scene": args.scene, "size_px": [w, h], "loop": True,
        "expected_scroll": clamp_path(background, [(x, y) for x, y, _ in steps]),
        "project": str(args.project), "default_sprite_mode": mode,
        "sprite_palettes": [list(p) for p in palettes],
        "actors": [[list(a) for a in actors if a] for _, _, actors in steps],
        "diagnostic_only": True,
        "excluded": ["GBVM actor logic", "collisions", "priority tiles", "144-px scenes"]}).encode("utf-8"))
    print(json.dumps({"rom": str(args.out / rom_name), "bytes": build["header"]["size_bytes"],
                      "steps": len(steps), "sprite_tiles": sum(len(s.tiles) for s in sheets),
                      "hardware_sprites_max": sum(s.max_sprites() for s in sheets)}, indent=2))


if __name__ == "__main__":
    main()
