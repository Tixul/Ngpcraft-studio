"""Sprite palette recycling proof (ngpcraft/sprite_recycle.py, docs/PALETTE_RECYCLE.md).

gbs2's platformer path (161 x 18 tiles, fade in kept), actors removed, then 12
actors no script moves, 9 tiles apart, each with its own native copy of gbs2's
"cat" sheet: 4 palettes each (lab_native_sprites), 48 palettes for the 15
slots the scene leaves (the player keeps its GB Studio sheet). Three
neighbours fit in one screen: 12 palettes together at most.

Each copy has a transparent hole of its own in every 8 x 8 quarter: the shape
of a sprite's tile names the copy and the quarter. Oracle, every frame at the
game tick (what the last frame showed): for every shown hardware sprite, its
tile from character RAM (flips applied), its CP.C byte and the sprite palette
RAM give the colour of each opaque pixel; it must be the colour drawn in that
copy's picture. The player runs right, back, right again (lab_big_map).

Witnesses: --witness load (drawn frames never ask for their palettes) and
--witness fade (ngc_palette_patch drops the colours given during a fade):
wrong colours must show.

  python -B tools/lab_sprite_recycle.py --out <new dir> --toolchain C:/t900
         --emulator <Ngpcraft_emulator> --bios <bios> [--witness load|fade]
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.png import read_png  # noqa: E402
from ngpcraft.project import SCHEMA, SIDECAR  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_actor_moves import GBS2  # noqa: E402
from tools.demo_gbs_scene import derive, write_json  # noqa: E402
from tools.lab_big_map import SCHEDULE  # noqa: E402
from tools.lab_native_sprites import _write_rgba, frame16  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick, write_frame  # noqa: E402

START = (20, 10)
COPIES = 12
SPACING = 9                      # tiles between two actors
FIRST_X, ROW = 16, 4             # actor k at tile (FIRST_X + SPACING * (COPIES + 1 - k), ROW):
# the last copies are on screen during the fade in, and a shared slot starts
# with the palette of the first copy using it (sprites.place_native_palettes)
FROM = 60                        # the fade in is over
WITNESSES = {
    "load": ("src/scene_sprites.c", "            ngc_spal_dirty |= (unsigned short)(1u << ps);", ""),
    "fade": ("src/gbvm.c", "    for (i = 0u; i < count; ++i) ngc_palette_shadow[(UWORD)first + i] = src[i];", ""),
}


def picture(k):
    """Copy k's 16 x 16 picture: rows of RGB444 colours or None (transparent);
    a hole at its own place in each quarter."""
    from ngpcraft.sprites import TRANSPARENT, sprite_index
    def opaque(c):               # off GB Studio's transparent keys (blue without green...)
        return c if sprite_index(*c, 255) != TRANSPARENT else (c[0], 0x33, c[2])
    rows = [[None if c is None else (lambda o: (o[0] >> 4) | (o[1] >> 4) << 4 | (o[2] >> 4) << 8)(opaque(c))
             for c in row] for row in frame16(k)]
    for qy in (0, 1):
        for qx in (0, 1):
            rows[qy * 8 + 1 + (k // 6) % 6][qx * 8 + 1 + k % 6] = None
    return rows


def design():
    """{opaque mask of an 8 x 8 tile: (copy, quarter, 64 colours)}."""
    out = {}
    for k in range(1, COPIES + 1):
        rows = picture(k)
        for q in range(4):
            qx, qy = q % 2, q // 2
            colours = tuple(rows[qy * 8 + y][qx * 8 + x] for y in range(8) for x in range(8))
            mask = tuple(c is not None for c in colours)
            if mask in out:
                raise SystemExit("two quarters share a shape")
            out[mask] = (k, q, colours)
    return out


def project_case(root, scenes=False):
    """scenes: a first scene (the same background, no actor) switches to the
    level at once, with a fade: the multi-scene engine (palette shadow)."""
    project = root / "project"
    derive(GBS2, project, "path_to_sample_town", START, fade=True)
    folder = next((project / "project/scenes").rglob("scene.gbsres")).parent
    template = json.loads(sorted((folder / "actors").glob("*.gbsres"))[0].read_text(encoding="utf-8"))
    for sub in ("triggers", "actors"):
        if (folder / sub).is_dir():
            shutil.rmtree(folder / sub)
    (folder / "actors").mkdir()
    sheet = json.loads((project / "assets/sprites/cat.png.gbsres").read_text(encoding="utf-8"))
    width, height, _ = read_png(project / "assets/sprites/cat.png")
    if (width, height) != (16, 16):
        raise SystemExit("gbs2 cat sheet is not one 16 x 16 frame")
    ids = []
    for k in range(1, COPIES + 1):
        name, sid = f"cat_recycle_{k}", f"ngpc-recycle-sheet-{k}"
        rows = picture(k)
        _write_rgba(project / f"assets/sprites/{name}.png",
                    [[(0, 0, 0, 0) if c is None else ((c & 15) * 17, (c >> 4 & 15) * 17, (c >> 8 & 15) * 17, 255)
                      for c in row] for row in rows])
        write_json(project / f"assets/sprites/{name}.png.gbsres",
                   {**sheet, "id": sid, "name": name, "symbol": f"sprite_{name}", "filename": f"{name}.png"})
        actor = dict(template)
        actor.update(id=f"ngpc-recycle-actor-{k}", _index=k - 1, symbol=f"actor_recycle_{k}", prefabId="",
                     name=f"Recycle {k}", coordinateType="tiles", x=FIRST_X + SPACING * (COPIES + 1 - k), y=ROW, frame=0,
                     animate=False, spriteSheetId=sid, paletteId="", direction="down", moveSpeed=1, animSpeed=15,
                     isPinned=False, persistent=False, collisionGroup="", collisionExtraFlags=[],
                     prefabScriptOverrides={}, script=[], startScript=[], updateScript=[], hit1Script=[],
                     hit2Script=[], hit3Script=[])
        write_json(folder / "actors" / f"recycle_{k}.gbsres", actor)
        ids.append(sid)
    write_json(project / SIDECAR, {"schema": SCHEMA, "resources": {i: {"ngpc_color": "native"} for i in ids}})
    if scenes:
        from tools.demo_actor_moves import ev, num
        level = json.loads((folder / "scene.gbsres").read_text(encoding="utf-8"))
        lead = folder.parent / "lab_lead_in"
        lead.mkdir()
        write_json(lead / "scene.gbsres", {
            **level, "id": "lab-lead-in", "name": "Lead in", "symbol": "scene_lab_lead_in",
            "script": [ev("lab-switch", "EVENT_SWITCH_SCENE", sceneId=level["id"], x=num(START[0]),
                          y=num(START[1]), direction="right", fadeSpeed="2")]})
        settings_path = project / "project/settings.gbsres"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings["startSceneId"] = "lab-lead-in"
        write_json(settings_path, settings)
    return project


def _tile(data, hflip, vflip):
    out = []
    for y in range(8):
        word = int.from_bytes(data[2 * y:2 * y + 2], "little")
        out.append([(word >> (14 - 2 * x)) & 3 for x in range(8)])
    if hflip:
        out = [row[::-1] for row in out]
    if vflip:
        out = out[::-1]
    return [v for row in out for v in row]


def check(machine, shapes):
    """(sprites checked per copy, wrong pixels, sprites of another sheet) of what is shown."""
    oam, cpc = machine.read(0x8800, 256), machine.read(0x8C00, 64)
    pal = [int.from_bytes(machine.read(0x8200 + 2 * n, 2), "little") & 0xFFF for n in range(64)]
    seen, wrong, other = {}, 0, 0
    for i in range(64):
        attr = oam[4 * i + 1]
        if not (attr >> 3) & 3:
            continue                                   # PR.C 0: not shown
        tile = oam[4 * i] | (attr & 1) << 8
        idx = _tile(machine.read(0xA000 + 16 * tile, 16), attr & 0x80, attr & 0x40)
        mask = tuple(v != 0 for v in idx)
        if mask not in shapes:
            other += 1
            continue
        k, _, colours = shapes[mask]
        seen[k] = seen.get(k, 0) + 1
        slot = cpc[i] & 15
        wrong += sum(1 for n, v in enumerate(idx) if v and pal[slot * 4 + v] != colours[n])
    return seen, wrong, other


def recycled(path):
    """{palettes, slots} from the generated sprite renderer's recycling comment."""
    import re
    m = re.search(r"sprite_recycle\.py: (\d+) sprite palettes in (\d+) slots", path.read_text(encoding="utf-8"))
    return {"palettes": int(m[1]), "slots": int(m[2])} if m else {"palettes": None, "slots": None}


def run(out, toolchain, emulator, bios, witness=None, frames=600, scenes=False):
    out = Path(out)
    if out.exists():
        raise SystemExit("output must be new")
    out.mkdir(parents=True)
    project = project_case(out, scenes)
    manifest = export_project(project, out / "export")
    if witness:
        name, old, new = WITNESSES[witness]
        path = out / "export" / name
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"witness: {old!r} not found once in {name}")
        path.write_text(text.replace(old, new), encoding="utf-8")
        export = json.loads((out / "export/export.json").read_text(encoding="utf-8"))
        export["sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(out / "export/export.json", export)
    build_rom(out / "export", Path(toolchain))
    rom = (out / "export/rom/game.ngc").read_bytes()
    build = json.loads((out / "export/rom/build-report.json").read_text(encoding="utf-8"))
    tick = verified_tick(rom, build)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.set_breakpoints([tick])
    shapes = design()
    copies, bad, others, compared = {}, [], 0, 0
    for frame in range(frames + 1):
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40 or stop.stop_pc != tick:
            raise SystemExit(f"tick {frame} not reached")
        if frame >= FROM:
            seen, wrong, other = check(machine, shapes)
            compared += 1
            others = max(others, other)
            for k, n in seen.items():
                copies[k] = copies.get(k, 0) + 1
            if wrong:
                bad.append(frame)
                if len(bad) == 1:
                    write_frame(out / "first_wrong.ppm", machine.framebuffer())
        if frame % 100 == 0:
            write_frame(out / f"frame_{frame:03d}.ppm", machine.framebuffer())
        held = 0
        for start, end, mask in SCHEDULE:
            if start <= frame < end:
                held |= mask
        machine.write(0x00B0, bytes([held]))
    result = {"witness": witness, "scenes": scenes, "frames_compared": compared, "frames_wrong": len(bad), "first_wrong": bad[:5],
              "copies_seen": dict(sorted(copies.items())), "most_other_sprites": others,
              **recycled(out / "export/src/scene_sprites.c"), "features": manifest["features"],
              "passed": compared > 0 and not bad and len(copies) == COPIES and "palette_recycle" in manifest["features"]}
    write_json(out / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--witness", choices=sorted(WITNESSES))
    parser.add_argument("--scenes", action="store_true", help="enter the level through a scene change")
    a = parser.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness, scenes=a.scenes)
    print(json.dumps(r, indent=2))
    return int(r["passed"] == bool(a.witness))


if __name__ == "__main__":
    raise SystemExit(main())
