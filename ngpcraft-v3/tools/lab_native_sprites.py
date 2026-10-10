"""`ngpc_color: "native"` sprite sheets in a native ROM (docs/M7_NATIVE_COLOR.md,
phase 2).

gbs2 "Cave" alone, actors removed; the player is drawn with gbs2's "cat"
sheet (16x16, two 8x16 tiles) whose image is redrawn: each 8x8 quarter has
3 colours of its own (12 colours, 4 sprite palettes; impossible with the 4
colours of one GB Studio sprite palette) and a transparent border. The
screen must show that 16x16 picture exactly (transparent pixels show the
background, not compared) somewhere for every compared frame.
Witness (--witness): the same project without the key: no exact match.

  python -B -m tools.lab_native_sprites --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> [--witness]
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.png import read_png
from ngpcraft.project import SCHEMA, SIDECAR
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev
from tools.demo_gbs_scene import derive, write_json
from tools.lab_native_color import write_png
from tools.probe_rom import power_on_ram, verified_tick, write_frame

QUARTERS = (((0xF0, 0x20, 0x20), (0x20, 0xF0, 0x20), (0x20, 0x20, 0xF0)),
            ((0xF0, 0xF0, 0x20), (0x20, 0xF0, 0xF0), (0xF0, 0x20, 0xF0)),
            ((0xF0, 0x90, 0x20), (0x90, 0x20, 0xF0), (0xF0, 0xF0, 0xF0)),
            ((0x80, 0x80, 0x80), (0xF0, 0x90, 0xC0), (0x90, 0xF0, 0x20)))


def recolour(colour, variant):
    """Variant v of a colour: each 4-bit channel XOR (5v + 3); variant 0 is the
    colour itself. Distinct per channel, so a variant's 4 palettes differ from
    every other variant's (no sharing: each sheet adds 4 palettes)."""
    if not variant:
        return colour
    return tuple((((c >> 4) ^ (5 * variant + 3)) & 15) * 17 for c in colour)


def frame16(variant=0):
    """16x16 RGBA rows: quarter q = (x >= 8) + 2 (y >= 8); colour by (x + y) % 3;
    the outer ring transparent."""
    rows = []
    for y in range(16):
        row = []
        for x in range(16):
            if x in (0, 15) or y in (0, 15):
                row.append(None)
            else:
                row.append(recolour(QUARTERS[(x >= 8) + 2 * (y >= 8)][(x + y) % 3], variant))
        rows.append(row)
    return rows


def project_case(root, native=True, extra=0):
    derive(GBS2, root / "project", "cave", (9, 12), fade=False)
    project = root / "project"
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    for sub in ("actors", "triggers"):
        if (path.parent / sub).is_dir():
            shutil.rmtree(path.parent / sub)
    sheet_res = project / "assets/sprites/cat.png.gbsres"
    sheet = json.loads(sheet_res.read_text(encoding="utf-8"))
    width, height, _ = read_png(project / "assets/sprites/cat.png")
    block = frame16()
    rows = [[(0, 0, 0, 0) if block[y % 16][x % 16] is None else block[y % 16][x % 16] + (255,)
             for x in range(width)] for y in range(height)]
    _write_rgba(project / "assets/sprites/cat.png", rows)
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["playerSpriteSheetId"] = sheet["id"]
    scene["script"] = []
    write_json(path, scene)
    ids = [sheet["id"]]
    # Extra actors, each with its own recoloured copy of the sheet: 4 more
    # palettes each (2 extras = 12 native palettes, past K2GE 8..15).
    for i in range(1, extra + 1):
        name = f"cat_native_{i}"
        variant = frame16(i)
        _write_rgba(project / f"assets/sprites/{name}.png",
                    [[(0, 0, 0, 0) if variant[y % 16][x % 16] is None else variant[y % 16][x % 16] + (255,)
                      for x in range(width)] for y in range(height)])
        sid = f"ngpc-native-sheet-{i}"
        write_json(project / f"assets/sprites/{name}.png.gbsres",
                   {**sheet, "id": sid, "name": name, "symbol": f"sprite_{name}", "filename": f"{name}.png"})
        (path.parent / "actors").mkdir(exist_ok=True)
        write_json(path.parent / "actors" / f"native_{i}.gbsres", {
            "_resourceType": "actor", "id": f"ngpc-native-actor-{i}", "_index": i - 1,
            "symbol": f"actor_native_{i}", "prefabId": "", "name": f"Native {i}",
            "coordinateType": "tiles", "x": 2 + 5 * i, "y": 5, "frame": 0, "animate": False,
            "spriteSheetId": sid, "paletteId": "", "direction": "down", "moveSpeed": 1,
            "animSpeed": 15, "isPinned": False, "persistent": False, "collisionGroup": "",
            "collisionExtraFlags": [], "prefabScriptOverrides": {}, "script": [], "startScript": [],
            "updateScript": [], "hit1Script": [], "hit2Script": [], "hit3Script": []})
        ids.append(sid)
    if native:
        write_json(project / SIDECAR, {"schema": SCHEMA,
                                       "resources": {i: {"ngpc_color": "native"} for i in ids}})
    return project, sheet["id"]


def _write_rgba(path, rows):
    import struct
    import zlib
    height, width = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def find_block(shown, variant=0):
    """(x, y) of the top-left of the 16x16 picture on the 160x152 screen, or None."""
    block = frame16(variant)
    want = [(x, y, (c[0] >> 4) | (c[1] >> 4) << 4 | (c[2] >> 4) << 8)
            for y, row in enumerate(block) for x, c in enumerate(row) if c is not None]
    for oy in range(152 - 15):
        for ox in range(160 - 15):
            if all(shown[(oy + y) * 160 + ox + x] == v for x, y, v in want):
                return ox, oy
    return None


def run(out, toolchain, emulator, bios, frames=20, witness=False, extra=0):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    project, sheet_id = project_case(out, native=not witness, extra=extra)
    manifest = export_project(project, out / "export")
    build = build_rom(out / "export", Path(toolchain))
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    tick = verified_tick(rom, build)
    m.set_breakpoints([tick])
    found = []
    for k in range(frames):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError(f"tick {k} not reached")
        if k >= 5:
            shown = [v & 4095 for v in m.framebuffer()]
            found.append(tuple(find_block(shown, variant) for variant in range(extra + 1)))
        m.set_breakpoints([])
        m.run(1, record=False)
        m.set_breakpoints([tick])
    write_frame(out / "frame.ppm", m.framebuffer())
    report = {"witness": witness, "frames_compared": len(found), "pictures": extra + 1,
              "frames_matched": sum(all(p is not None for p in f) for f in found),
              "positions": sorted({p for f in found for p in f if p}),
              "passed": bool(found) and all(all(p is not None for p in f) for f in found),
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "features": manifest["features"]}
    write_json(out / "comparison.json", report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--witness", action="store_true")
    p.add_argument("--extra", type=int, default=0, help="extra actors with their own native sheet (4 palettes each)")
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, witness=a.witness, extra=a.extra)
    print(json.dumps(r, indent=2))
    return int(r["passed"] == a.witness)


if __name__ == "__main__":
    raise SystemExit(main())
