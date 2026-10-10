"""ngpc_backdrop: the backdrop colour of a native background, chosen (docs/M7_NATIVE_COLOR.md).

gbs2 "Cave" alone, actors removed, player hidden, in native colours; its PNG
is rewritten with one transparent 8x8 cell (tile 2, 2). A transparent pixel
shows the backdrop: with ngpc_backdrop "ff0080" the cell must show RGB444
0x80F on screen; without it (--witness) the automatic backdrop (the most
frequent colour), never 0x80F.
"""
import argparse
import json
from pathlib import Path
import shutil
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.background_stream import view_of  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.png import read_png  # noqa: E402
from ngpcraft.project import SCHEMA, SIDECAR, load_project  # noqa: E402
from ngpcraft.scene_assets import background_source  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_actor_moves import GBS2, ev  # noqa: E402
from tools.demo_gbs_scene import derive, write_json  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick, write_frame  # noqa: E402

CHOSEN, CHOSEN_444 = "ff0080", 0x80F
CELL = (2, 2)


def write_rgba_png(path, width, height, rgba):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(c for px in rgba[y * width:(y + 1) * width] for c in px) for y in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def project_case(root, chosen=True):
    project = root / "project"
    derive(GBS2, project, "cave", (9, 12), fade=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    for sub in ("actors", "triggers"):
        if (path.parent / sub).is_dir():
            shutil.rmtree(path.parent / sub)
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["script"] = [ev("hide-player", "EVENT_ACTOR_HIDE", actorId="player")]
    write_json(path, scene)
    bg = scene["backgroundId"]
    for res in (project / "assets/backgrounds").glob("*.gbsres"):
        data = json.loads(res.read_text(encoding="utf-8"))
        if data["id"] != bg:
            continue
        data["tileColors"] = ""            # native colours ignore them (and refuse GB priority)
        write_json(res, data)
        w, h, rgba = read_png(res.with_suffix(""))
        rgba = [tuple(p) for p in rgba]
        for y in range(CELL[1] * 8, CELL[1] * 8 + 8):
            for x in range(CELL[0] * 8, CELL[0] * 8 + 8):
                r, g, b, _ = rgba[y * w + x]
                rgba[y * w + x] = (r, g, b, 0)
        write_rgba_png(res.with_suffix(""), w, h, rgba)
    extension = {"ngpc_color": "native", **({"ngpc_backdrop": CHOSEN} if chosen else {})}
    write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": {}, "resources": {bg: extension}})
    return project


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    if out.exists():
        raise SystemExit("output must be new")
    out.mkdir(parents=True)
    project = project_case(out, chosen=not witness)
    export_project(project, out / "export")
    build = build_rom(out / "export", Path(toolchain))
    loaded = load_project(project)
    source = background_source(loaded, loaded.scenes[0])
    _, top = view_of(source)
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    m.set_breakpoints([verified_tick(rom, build)])
    for _ in range(8):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise SystemExit("tick not reached")
    picture = [v & 4095 for v in m.framebuffer()]
    write_frame(out / "frame.ppm", picture)
    cell = [picture[(top + CELL[1] * 8 + y) * 160 + CELL[0] * 8 + x] for y in range(8) for x in range(8)]
    result = {"witness": witness, "backdrop_source": source.backdrop, "cell_colours": sorted(set(cell)),
              "chosen": CHOSEN_444}
    result["ok"] = (set(cell) == {CHOSEN_444}) if not witness else (CHOSEN_444 not in cell
                                                                    and set(cell) == {source.backdrop})
    write_json(out / "backdrop.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + n, required=True)
    p.add_argument("--witness", action="store_true")
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness)
    print(json.dumps(r, indent=2))
    raise SystemExit(0 if r["ok"] else 1)
