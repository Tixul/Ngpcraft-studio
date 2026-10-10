"""Build a ROM running GBVM actor bookkeeping (activation, animation, render order)
over a streamed gbs2 scene, and the expected frames from the line-by-line oracle.

Diagnostic only: positions and directions come from a scripted table (stand-in
for movement scripts). The camera circles the map without stopping, so actors
leave the screen (deactivated, or only disabled when persistent) and come back
(activated by the incoming column/row, GBVM scroll.c). One actor is pinned.
"""
import argparse
import json
import math
from pathlib import Path

from ngpcraft.background import compile_background
from ngpcraft.scene_actors import FLAGS, export_actors_rom
from ngpcraft.sprites import ngpc_sheet
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import scene_pixels, write_new
from tools.demo_scene import sprite_palettes
from tools.gbvm_actors import Actor, Scene
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets

SHEETS = ("player", "npc001", "npc003", "cat", "npc_gardener", "dog", "duck")
# (sheet, world x px, world y px, dir, flags, anim tick): player first.
ACTORS = [("player", 0, 0, 1, 0, 15),
          ("npc001", 64, 96, 0, 0, 15),
          ("npc003", 360, 80, 3, 0, 7),             # walks in place (moving)
          ("cat", 200, 380, 1, 0, 3),
          ("npc_gardener", 120, 300, 0, FLAGS["persistent"], 15),   # keep running offscreen
          ("dog", 400, 400, 2, 0, 7),
          ("duck", 16, 32, 0, FLAGS["pinned"], 31),  # pinned: fixed on screen
          ("npc003", 236, 236, 1, 0, 15)]            # only seen when the camera jumps to the centre


def script(width, height, steps):
    xm, ym = width - 160, height - 152
    out = []
    for t in range(steps):
        a = 2 * math.pi * t / steps
        cx = int(80 + xm / 2 + (xm / 2 + 20) * math.cos(a)) + (t & 1)   # never still, overshoots: clamps
        cy = int(76 + ym / 2 + (ym / 2 + 20) * math.sin(a))
        if steps // 3 <= t < steps // 3 + 12:           # jump to the centre and back: re-blit
            cx, cy = 80 + xm // 2 + (t & 1), 76 + ym // 2 + (t - steps // 3)   # activation
        px, py = cx - 8, cy + 4                              # the player walks with the camera
        direction = 1 if math.sin(a) < 0 else 3 if math.sin(a) > 0 else 0   # right/left by movement
        entries = [(px, py, 2 if t % 60 < 10 else direction, t % 60 >= 10)]
        for n, (_, x, y, d, _, _) in enumerate(ACTORS[1:], 1):
            moving = n == 2 or (n == 7 and t % 40 < 20)
            entries.append((x, y + (t % 30 if n == 5 else 0), d, moving))
        out.append((cx, cy, entries))
    return out


def simulate(sheets, names, steps, width, height, ticks):
    """Oracle run, tick by tick, the ROM's loop included (step = tick % len(steps))."""
    actors = [Actor(i, x << 5, y << 5, d, f, tick, list(sheets[SHEETS.index(name)].animation_offsets[:8]))
              for i, ((x, y, d, _), (name, _, _, _, f, tick)) in enumerate(zip(steps[0][2], ACTORS))]
    scene = Scene(actors, width, height)
    frames = []

    def record(position):
        draws = []
        for actor, _, _ in scene.render_list():
            sheet = sheets[SHEETS.index(names[actor.index])]
            anim = next(a for a, (s, e) in enumerate(sheet.animation_offsets[:8]) if s <= actor.frame <= e)
            world = ((actor.x >> 5), (actor.y >> 5)) if not actor.flags & 1 else \
                ((actor.x >> 5) + position[0], (actor.y >> 5) + position[1])
            draws.append([names[actor.index], world[0], world[1], anim,
                          actor.frame - sheet.animation_offsets[anim][0]])
        frames.append((position, draws))

    record(scene.start(steps[0][0], steps[0][1]))
    for t in range(1, ticks):
        cx, cy, entries = steps[t % len(steps)]
        for actor, (x, y, d, moving) in zip(actors, entries):
            actor.x, actor.y = x << 5, y << 5
            scene.set_dir(actor, d, moving)
        position = scene.scroll_update(cx, cy)
        scene.actors_update()
        record(position)
        scene.game_time = (scene.game_time + 1) & 0xFFFF
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="sample_town")
    parser.add_argument("--project", type=Path, default=REFERENCE / "appData/templates/gbs2")
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    w, h, pixels = scene_pixels(args.project, args.scene)
    background = compile_background(w, h, pixels, where=f"actors:{args.scene}")
    settings = json.loads((args.project / "project/settings.gbsres").read_text(encoding="utf-8"))
    mode = settings["spriteMode"]
    loaded = load_sheets(args.project)
    sheets = [ngpc_sheet(*loaded[name], mode) for name in SHEETS]
    steps = script(w, h, args.steps)
    defs = [{"sheet": SHEETS.index(name), "flags": f, "anim_tick": tick, "move_speed": 0} for name, _, _, _, f, tick in ACTORS]
    palettes = sprite_palettes(args.project, args.scene)
    export_actors_rom(background, sheets, palettes, defs, steps, args.out / "export", loop=True)
    build = build_rom(args.out / "export", args.toolchain)
    frames = simulate(sheets, [a[0] for a in ACTORS], steps, w, h, 2 * len(steps) + 10)
    args.out.mkdir(exist_ok=True)
    write_new(args.out / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
    rom_name = f"ngpcraft_m2_gbvm_actors_{args.scene}.ngc"
    write_new(args.out / rom_name, (args.out / "export/rom/game.ngc").read_bytes())
    write_new(args.out / "source.json", json.dumps({
        "scene": args.scene, "size_px": [w, h], "loop": False, "probe_frames": 2 * len(steps),
        "expected_scroll": [list(p) for p, _ in frames], "actors": [d for _, d in frames],
        "project": str(args.project), "default_sprite_mode": mode,
        "sprite_palettes": [list(p) for p in palettes],
        "oracle": "tools/gbvm_actors.py (GBVM actor.c/scroll.c transcription), ticks from the start",
        "diagnostic_only": True,
        "excluded": ["movement scripts", "On Update scripts", "collisions", "emotes", "window"]}).encode("utf-8"))
    active = [len(d) for _, d in frames]
    print(json.dumps({"rom": str(args.out / rom_name), "bytes": build["header"]["size_bytes"],
                      "steps": len(steps), "drawn_actors_min_max": [min(active), max(active)]}, indent=2))


if __name__ == "__main__":
    main()
