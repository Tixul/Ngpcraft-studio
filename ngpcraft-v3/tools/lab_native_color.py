"""`ngpc_color: "native"` backgrounds in a native ROM, compared pixel for pixel
(docs/M7_NATIVE_COLOR.md).

gbs2 "Cave" alone, its actors removed and the player hidden; its background
image replaced by a 160x144 picture of 16 groups of 3 colours each (48
colours + the backdrop: impossible with GB Studio's 8 palettes of 4), each
tile drawn with one group. The screen must show the PNG rounded to 4 bits per
channel, between the 4 px bands of a 144 px scene (ECARTS 1).
Witness (--witness): the same project without the key (GB Studio colours,
green thresholds and the scene palettes): the picture must differ.

  python -B -m tools.lab_native_color --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> [--witness]
"""
from array import array
import argparse
import colorsys
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import zlib

from ngpcraft.background_stream import view_of
from ngpcraft.native_color import palette_plan
from ngpcraft.pipeline import export_project
from ngpcraft.project import SCHEMA, SIDECAR, load_project
from ngpcraft.scene_assets import background_source
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev
from tools.demo_gbs_scene import derive, write_json
from tools.probe_rom import power_on_ram, verified_tick, write_frame

BACKDROP = (0x22, 0x11, 0x44)
GROUPS = 16


def group_colours(g):
    """Three 4-bit colours of group g (distinct over every group)."""
    out = []
    for k in range(3):
        r, gr, b = colorsys.hsv_to_rgb((g + k / 3) / GROUPS % 1.0, 1.0 - 0.25 * k, 1.0 - 0.2 * (g % 2))
        out.append(tuple(round(c * 15) * 17 for c in (r, gr, b)))
    return out


def picture(width=160, height=144):
    """RGB rows: tile (tx, ty) uses group (tx + 3 ty) % 16, diagonal stripes;
    half of each tile is the backdrop (the most frequent colour)."""
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            g = (x // 8 + 3 * (y // 8)) % GROUPS
            k = (x + y) % 6
            row.append(BACKDROP if k >= 3 else group_colours(g)[k])
        rows.append(row)
    return rows


def write_png(path, rows):
    height, width = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def project_case(root, native=True):
    derive(GBS2, root / "project", "cave", (9, 12), fade=False)
    path = next((root / "project/project/scenes").rglob("scene.gbsres"))
    for sub in ("actors", "triggers"):
        if (path.parent / sub).is_dir():
            shutil.rmtree(path.parent / sub)
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["script"] = [ev("hide-player", "EVENT_ACTOR_HIDE", actorId="player")]
    write_json(path, scene)
    bg_id = scene["backgroundId"]
    for res in (root / "project/assets/backgrounds").glob("*.gbsres"):
        if json.loads(res.read_text(encoding="utf-8"))["id"] == bg_id:
            write_png(res.with_suffix(""), picture())
    if native:
        write_json(root / "project" / SIDECAR, {"schema": SCHEMA, "resources": {bg_id: {"ngpc_color": "native"}}})
    return root / "project", bg_id


def run(out, toolchain, emulator, bios, frames=12, witness=False):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    project_path, bg_id = project_case(out, native=not witness)
    plan = palette_plan(project_path, bg_id)
    manifest = export_project(project_path, out / "export")
    build = build_rom(out / "export", Path(toolchain))
    # The expectation: the PNG itself, rounded (independent of the build).
    rows = picture()
    expected_bg = [(r >> 4) | (g >> 4) << 4 | (b >> 4) << 8 for row in rows for r, g, b in row]
    project = load_project(project_path)
    source = background_source(project, project.scenes[0])
    view_h, top = view_of(source)
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.set_breakpoints([verified_tick(rom, build)])
    wrong_frames, first = 0, None
    for k in range(frames + 1):
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError(f"tick {k} not reached")
        if k >= 3:
            shown = array("H", (v & 4095 for v in machine.framebuffer()))
            diff = [(j % 160, j // 160) for j in range(160 * 152)
                    if top <= j // 160 < top + view_h and shown[j] != expected_bg[(j // 160 - top) * 160 + j % 160]]
            if diff:
                wrong_frames += 1
                first = first or {"tick": k, "pixels": len(diff), "first": diff[:4]}
        machine.set_breakpoints([])
        machine.run(1, record=False)
        machine.set_breakpoints([verified_tick(rom, build)])
    write_frame(out / "frame.ppm", machine.framebuffer())
    background = manifest["scene_engine"]["background"]
    result = {"witness": witness, "frames_compared": frames - 2, "wrong_frames": wrong_frames, "first": first,
              "plan": {"backdrop": plan["backdrop"], "palettes": len(plan["palettes"]), "errors": len(plan["errors"])},
              "colours_in_png": len(set(expected_bg)), "palette_counts": background.get("palette_counts"),
              "rom_sha256": hashlib.sha256(rom).hexdigest()}
    write_json(out / "comparison.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--frames", type=int, default=12)
    p.add_argument("--witness", action="store_true")
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.frames, a.witness)
    print(json.dumps(r, indent=2))
    return int(bool(r["wrong_frames"]) != a.witness)


if __name__ == "__main__":
    raise SystemExit(main())
