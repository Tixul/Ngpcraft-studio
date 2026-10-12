"""Scenes over GB Studio's 255 tiles (docs/PLAN_GRANDES_CARTES.md G9).

Oracle by translation: gbs2's platformer scene (path_to_sample_town, 161 x 18
tiles) is repeated rx times across and ry times down. A player placed in the
last copy, given the same buttons, must follow exactly the trajectory of the
player of the original scene placed at the same spot of its copy: x and y
offset by whole copies (in 1/32 px), same platform.c state every tick. Once
the player is past 2048 px (256 tiles), the 16-bit GB Studio position cannot
hold it: only the wide world's 32-bit positions can pass.

Across (rx > 1) the camera sees the same picture in both scenes: every
sampled screen must be identical to the pixel. Down (ry > 1), the original
scene is one screen tall and its camera does not move vertically: positions
and states are compared, screens are saved to look at.

Witness (--witness): the wide export with GB Studio's 8-bit SUBPX_TO_TILE
macro restored in platform.c must fail.

  python -B tools/lab_big_map.py --out <new dir> --toolchain C:/t900
         --emulator <Ngpcraft_emulator> --bios <bios> [--axis x|y] [--frames 600] [--witness]
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.demo_actor_moves import GBS2  # noqa: E402
from tools.demo_gbs_scene import derive, write_json  # noqa: E402
from tools.platform_oracle import A, B, LEFT, RIGHT, RIDE, RIDE_PLATFORM, encode_collisions, project_case  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick, write_frame  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.scene_assets import decompress_tile_colors  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402

BASE = (161, 18)                 # gbs2 path_to_sample_town, tiles
START = (20, 10)                 # camera free on both sides of the original scene
# Run right (with B), jump regularly, a short walk back left, run right again.
SCHEDULE = ([[20, 330, RIGHT | B], [330, 380, LEFT], [380, 600, RIGHT | B]]
            + [[t, t + 12, A] for t in range(40, 600, 55)])
SAMPLE = 20                      # one screen every SAMPLE ticks
WITNESS = ("#define SUBPX_TO_TILE(a) ((UWORD)((a) >> 8))   /* wide world: 16-bit tiles */",
           "#define SUBPX_TO_TILE(a) ((UWORD)(unsigned char)((a) >> 8))")
# TOPDOWN: gbs2 sample_town (56 x 56), 5 copies across; row 13 is free from
# x 8 to 53: walk right from x 20 (camera free) past tile 256 of the wide scene.
TOPDOWN = ("sample_town", (56, 56), (20, 13))
UP, DOWN = 1, 2
TOPDOWN_SCHEDULE = [[20, 240, RIGHT], [250, 262, DOWN], [270, 360, LEFT], [370, 382, UP], [390, 470, RIGHT]]
TOPDOWN_WITNESS = ("tile = (unsigned short)((unsigned long)(act_x[0] + (unsigned long)act_bounds[1]) >> 8);",
                   "tile = (unsigned char)((unsigned long)(act_x[0] + (unsigned long)act_bounds[1]) >> 8);")


# A ladder (GBVM TILE_PROP_LADDER) under X's middle at the start, climbed and left: platform.c's
# ladder state adds VEL_TO_SUBPX to the position directly (signed in a wide world). Witness: the
# GB Studio (unsigned) macro, which adds 65536 a climbing step on 32 bits.
LADDER_CASE = ((20, 10), [[30, 150, UP], [160, 220, RIGHT], [230, 300, LEFT], [310, 400, UP]],
               {(20, y): 0x10 for y in range(5, 13)},
               ("#define VEL_TO_SUBPX(v) ((WORD)((((v) & 0x8000) ? (((v) >> 8) | 0xFF00) : ((v) >> 8)) << 1))"
                "   /* wide world: signed */",
                "#define VEL_TO_SUBPX(v) ((((v) & 0x8000) ? (((v) >> 8) | 0xFF00) : ((v) >> 8)) << 1)"))


# Other gbs2 scenes (G6): (folder, size in tiles, start, buttons, scene type forced or None,
# copies across, witness (exported C, GB Studio's 16-bit form) or None).
GENRES = {
    "topdown": (TOPDOWN[0], TOPDOWN[1], TOPDOWN[2], TOPDOWN_SCHEDULE, None, 5, TOPDOWN_WITNESS),
    # adventure.c on the same town: its walk moves act_x by VEL_TO_SUBPX directly when blocked.
    # adventure.c on the same town: its walk computes new_x (32-bit here) each step.
    "adventure": (TOPDOWN[0], TOPDOWN[1], TOPDOWN[2], TOPDOWN_SCHEDULE, "ADVENTURE", 5,
                  ("    unsigned long new_x;", "    UWORD new_x;")),
    # shmup.c (perpendicular lock: the ship moves up and down) steps by NGC_TRANSLATE_ANGLE, signed
    # here: going down subtracts a negative step.
    "shmup": ("space_battle", (255, 18), (12, 8), [[20, 120, 1], [130, 230, 2], [240, 300, 8], [310, 400, 4]],
              None, 2, ("act_y[i] -= (long)((short)", "act_y[i] -= (UWORD)((short)")),
    # pointnclick.c: the cursor kept in the image (image_width_subpx).
    "pointnclick": ("players_house", (20, 18), (8, 9), [[20, 200, 8], [210, 260, 2], [270, 400, 4]], None, 13,
                    ("#define image_width_subpx ((unsigned long)NGC_COLL_W << 8)",
                     "#define image_width_subpx ((UWORD)(NGC_COLL_W << 8))")),
    # The town with its GB priority tiles kept (cells 12-13 of rows 42..49): the player walks
    # row 47 back and forth under them (camera kept off the scene's left edge, free in both
    # scenes), behind the background through the dynamic mask
    # (sprite_priority.py). 2240 px wide: rows past 234 px are over 64 KiB into the mask.
    # Witness: the mask row offset cut to 16 bits.
    "priority": (TOPDOWN[0], TOPDOWN[1], (20, 47), [[20, 88, 4], [100, 168, 8], [180, 248, 4], [260, 328, 8]],
                 None, 5, ("src/scene_sprites.c", "#define NGC_MASK_ROW(y) (ngc_mask_row8[",
                           "#define NGC_MASK_ROW(y) (unsigned short)(ngc_mask_row8[")),
}
PRIORITY_KEPT = ("priority",)


# Cases where both scenes show the same picture (camera free on both): screens compared.
SCREENS = ("x", "topdown", "adventure", "shmup", "priority")


def scene_case(root, genre, start):
    """project_case for another gbs2 scene: that scene alone, its actors and triggers removed,
    its type forced when the case says so."""
    import shutil
    folder_name, _, _, _, kind, _, _ = GENRES[genre]
    derive(GBS2, root / "project", folder_name, start, fade=False)
    folder = next((root / "project/project/scenes").rglob("scene.gbsres")).parent
    for sub in ("triggers", "actors"):
        if (folder / sub).is_dir():
            shutil.rmtree(folder / sub)
    if kind:
        scene = json.loads((folder / "scene.gbsres").read_text(encoding="utf-8"))
        scene["type"] = kind
        write_json(folder / "scene.gbsres", scene)
    return root / "project"


def big_case(root, rx, ry, start, platform=None, topdown=None, tiles=None):
    """gbs2's scene repeated rx x ry times (background and collisions).
    platform: project_case's moving platform (Move To in pixels), or None."""
    from PIL import Image
    # topdown: a GENRES key (another gbs2 scene), else gbs2's platformer path
    project = (scene_case(root, topdown, start) if topdown else
               project_case(root, start=start, platform=platform, tiles=tiles))
    folder = next((project / "project/scenes").rglob("scene.gbsres")).parent
    scene = json.loads((folder / "scene.gbsres").read_text(encoding="utf-8"))
    w, h = scene["width"], scene["height"]
    if (w, h) != (GENRES[topdown][1] if topdown else BASE):
        raise SystemExit(f"gbs2 scene is {w}x{h}, not the expected one")
    resource = next(p for p in (project / "assets/backgrounds").glob("*.gbsres")
                    if json.loads(p.read_text(encoding="utf-8")).get("id") == scene["backgroundId"])
    background = json.loads(resource.read_text(encoding="utf-8"))
    if topdown and topdown not in PRIORITY_KEPT:
        # sample_town's GB priority tiles take the dynamic sprite mask: cleared
        # here, kept by the "priority" case.
        cells = decompress_tile_colors(background["tileColors"], "lab_big_map", "tileColors")
        background["tileColors"] = encode_collisions([c & 0x7F for c in cells])
        write_json(resource, background)
    if rx == ry == 1:
        return project
    png = resource.with_suffix("")
    image = Image.open(png)
    big = Image.new(image.mode, (image.width * rx, image.height * ry))
    if image.mode == "P":
        big.putpalette(image.getpalette())
    for i in range(rx):
        for j in range(ry):
            big.paste(image, (i * image.width, j * image.height))
    big.save(png)
    def repeat(text, what):        # tileColors and collisions share GB Studio's encoding
        cells = decompress_tile_colors(text, "lab_big_map", what)
        cells = (cells + [0] * (w * h))[:w * h]
        return encode_collisions([cells[(y % h) * w + x % w] for y in range(h * ry) for x in range(w * rx)])
    background.update(width=w * rx, height=h * ry, imageWidth=image.width * rx,
                      imageHeight=image.height * ry, tileColors=repeat(background["tileColors"], "tileColors"))
    write_json(resource, background)
    scene.update(width=w * rx, height=h * ry, collisions=repeat(scene["collisions"], "collisions"))
    write_json(folder / "scene.gbsres", scene)
    return project


def trace(export, emulator, bios, frames, schedule, size, actors=1):
    rom = (export / "rom/game.ngc").read_bytes()
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    symbols = build["link_audit"]["public_symbols"]
    tick = verified_tick(rom, build)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.set_breakpoints([tick])

    def read(name, n, signed=False):
        return int.from_bytes(machine.read(symbols[name]["address"], n), "little", signed=signed)
    rows, screens = [], {}
    for frame in range(frames + 1):
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40 or stop.stop_pc != tick:
            raise SystemExit(f"tick {frame} not reached")
        rows.append({"x": read("_act_x", size), "y": read("_act_y", size),
                     "actors": [[int.from_bytes(machine.read(symbols[name]["address"] + size * k, size), "little")
                                 for name in ("_act_x", "_act_y")] for k in range(1, actors)],
                     **({"state": read("_plat_state", 1), "vel_x": read("_plat_vel_x", 2, True),
                         "vel_y": read("_plat_vel_y", 2, True)} if "_plat_state" in symbols else
                        {"state": 0, "vel_x": 0, "vel_y": 0}),
                     "scroll": read("_ngc_scroll", 4)})
        if frame % SAMPLE == 0:
            screens[frame] = machine.framebuffer()
        held = 0
        for start, end, mask in schedule:
            if start <= frame < end:
                held |= mask
        machine.write(0x00B0, bytes([held]))
    return rows, screens


def build(root, rx, ry, start, toolchain, witness=False, platform=None, topdown=False, tiles=None, witness_pair=None):
    project = big_case(root, rx, ry, start, platform, topdown, tiles)
    export_project(project, root / "export")
    if witness:
        pair = witness_pair or WITNESS
        name, old, new = pair if len(pair) == 3 else ("src/scene_actors.c", *pair)
        path = root / "export" / name
        text = path.read_text(encoding="utf-8")
        if text.count(old) < 1:
            raise SystemExit(f"witness: {old} not found")
        path.write_text(text.replace(old, new), encoding="utf-8")
        manifest = json.loads((root / "export/export.json").read_text(encoding="utf-8"))
        import hashlib
        manifest["sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(root / "export/export.json", manifest)
    build_rom(root / "export", Path(toolchain))
    return root / "export"


def run(out, toolchain, emulator, bios, axis="x", frames=600, witness=False):
    """axis: "x" (2 copies across), "y" (15 down), "ride" (3 copies across, a
    moving platform driven by Move To in pixels that the player rides)."""
    if out.exists():
        raise SystemExit("output must be new")
    out.mkdir(parents=True)
    topdown = axis if axis in GENRES else None
    rx, ry = (GENRES[axis][5], 1) if topdown else {"x": (2, 1), "y": (1, 15), "ride": (3, 1), "ladder": (2, 1)}[axis]
    base = GENRES[axis][1] if topdown else BASE
    copy_x, copy_y = (rx - 1) * base[0], (ry - 1) * base[1]
    start, schedule, platform, wide_platform, actors = START, SCHEDULE, None, None, 1
    tiles = witness_pair = None
    if topdown:
        start, schedule, witness_pair = GENRES[axis][2], GENRES[axis][3], GENRES[axis][6]
    if axis == "ladder":
        start, schedule, tiles, witness_pair = LADDER_CASE
    if axis == "ride":
        flag, x0, x1, y = RIDE_PLATFORM
        start, schedule, actors = (4, 10), RIDE, 2
        platform, wide_platform = RIDE_PLATFORM, (flag, x0 + copy_x * 8, x1 + copy_x * 8, y)
    narrow = build(out / "narrow", 1, 1, start, toolchain, platform=platform, topdown=topdown, tiles=tiles)
    wide = build(out / "wide", rx, ry, (start[0] + copy_x, start[1] + copy_y), toolchain, witness,
                 wide_platform, topdown, tiles, witness_pair)
    ref, ref_screens = trace(narrow, emulator, bios, frames, schedule, 2, actors)
    got, got_screens = trace(wide, emulator, bios, frames, schedule, 4, actors)
    dx, dy = copy_x * 256, copy_y * 256
    shifted = lambda r: (r["x"] + dx, r["y"] + dy, r["state"], r["vel_x"], r["vel_y"],
                         [[ax + dx, ay + dy] for ax, ay in r["actors"]])
    mismatches = [{"tick": k, "expected": shifted(r), "actual": g}
                  for k, (r, g) in enumerate(zip(ref, got))
                  if (g["x"], g["y"], g["state"], g["vel_x"], g["vel_y"], g["actors"]) != shifted(r)]
    screen_diffs = []
    shots = out / "screens"
    shots.mkdir()
    for frame in sorted(got_screens):
        write_frame(shots / f"wide_{frame:04d}.ppm", got_screens[frame])
        write_frame(shots / f"narrow_{frame:04d}.ppm", ref_screens[frame])
        # Tick 0 shows the frame drawn while the scene loads (half-drawn player in
        # both): it depends on the load time, longer with 32-bit positions.
        if axis in SCREENS and frame > 0 and got_screens[frame] != ref_screens[frame]:
            screen_diffs.append(frame)
    beyond = [k for k, g in enumerate(got) if (g["y"] if axis == "y" else g["x"]) >= 2048 * 32]
    result = {"axis": axis, "scene_tiles": [base[0] * rx, base[1] * ry], "ticks": len(got),
              "mismatches": len(mismatches), "first": mismatches[:3],
              "ticks_beyond_2048_px": len(beyond), "first_beyond": beyond[0] if beyond else None,
              "actors_compared": actors - 1,
              "actor_x_px_range": [[min(g["actors"][k][0] for g in got) >> 5, max(g["actors"][k][0] for g in got) >> 5]
                                   for k in range(actors - 1)],
              "max_px": max((g["y"] if axis == "y" else g["x"]) for g in got) >> 5,
              "states_seen": sorted({g["state"] for g in got}),
              "screens_compared": len(got_screens) - 1 if axis in SCREENS else 0,
              "screen_mismatches": screen_diffs, "witness": witness}
    write_json(out / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--bios", required=True)
    parser.add_argument("--axis", choices=["x", "y", "ride", "ladder", *GENRES], default="x")
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--witness", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.out, args.toolchain, args.emulator, args.bios, args.axis,
                         args.frames, args.witness), indent=2))


if __name__ == "__main__":
    main()
