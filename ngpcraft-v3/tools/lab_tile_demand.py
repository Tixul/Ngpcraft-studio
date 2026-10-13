"""What a running game really shows, frame by frame (docs/ETUDE_RECYCLAGE_TUILES.md).

A project (copied, never modified) is exported and played on the native core
(silicon timing, noisy power-on RAM) with held buttons. At every game tick:
- every ngc_sprite_draw(frame, x, y) call is read at its entry (arguments on
  the stack), and the same on-screen test as the renderer keeps the frames
  actually drawn; their tiles come from the scene's own tables
  (s<k>_spr_start / s<k>_spr_words / s<k>_spr_rec of scene_sprites.c);
- per tick: distinct frames drawn, distinct sprite tiles they use (what must
  be resident at that moment), and tiles of frames not drawn the tick before
  (what a frame cache would copy, a cache holding exactly the last tick);
- the raster line (K2GE 0x8009 as the core reports it) when the tick starts
  and when each commit step starts: how much of the vertical blank
  (lines 152..198) the commits already take.

  python -B -m tools.lab_tile_demand --project <dir> --out <new dir> --frames 1200
         [--start <scene id> [--x X --y Y]] [--inputs run.json]
         --toolchain C:/t900 --emulator <Ngpcraft_emulator> --bios <bios>

Writes result.json (per-tick series and summary) in --out.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import statistics
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402  (before the emulator's tools)

COMMITS = ("ngc_scroll_commit", "ngc_sprites_commit", "ngc_stream_update", "ngc_ui_commit",
           "ngc_audio_service", "script_runner_update")
ARRAY = re.compile(r"static const (?:unsigned short|unsigned char|signed char) (s\d+)_spr_(words|start|rec)"
                   r"\[\d+\] = \{([^}]*)\};", re.S)


def scene_tables(source):
    """{scene: {"words", "start", "rec"}} from scene_sprites.c."""
    tables = {}
    for prefix, name, body in ARRAY.findall(source):
        values = [int(v, 0) for v in re.findall(r"-?(?:0x[0-9A-Fa-f]+|\d+)", body)]
        tables.setdefault(int(prefix[1:]), {})[name] = values
    return tables


def frame_tiles(t, frame):
    """Character numbers of a frame (low 9 bits of each sprite's first word)."""
    count = t["rec"][frame * 8] & 0xFF
    start = t["start"][frame]
    return {t["words"][start + 2 * i] & 0x1FF for i in range(count)}


def drawn(t, frame, x, y):
    """ngc_sprite_draw's own test: count, then the frame's box on the 160 x 152 screen."""
    if frame >= len(t["start"]):
        return False
    r = t["rec"][frame * 8:frame * 8 + 8]
    if r[0] == 0:
        return False
    return not (x + r[4] < 0 or x + r[3] > 159 or y + r[6] < 0 or y + r[5] > 151)


def signed(v):
    return v - 0x10000 if v & 0x8000 else v


def run(export_dir, frames, inputs, emulator, bios):
    rom = (export_dir / "rom/game.ngc").read_bytes()
    build = json.loads((export_dir / "rom/build-report.json").read_text(encoding="utf-8"))
    sym = {k[1:]: v["address"] for k, v in build["link_audit"]["public_symbols"].items()}
    tables = scene_tables((export_dir / "src/scene_sprites.c").read_text(encoding="utf-8"))
    if str(emulator) not in sys.path:
        sys.path.insert(0, str(emulator))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    tick = verified_tick(rom, build)
    draw = sym["ngc_sprite_draw"]
    steps = {sym[n]: n for n in COMMITS if n in sym}
    m.set_breakpoints([tick, draw, *steps])
    scene_at = sym.get("ngc_scene")
    series, previous, current, lines, frame = [], set(), None, {}, 0
    xsp = native.REG_NAMES.index("xsp")
    while frame < frames:
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError(f"tick not reached at frame {frame} (status {stop.stop_status})")
        if stop.stop_pc == tick:
            if current is not None:
                series.append(current)
                previous = current["frames"]
            frame += 1
            held = 0
            for start, end, bits in inputs:
                if start <= frame < end:
                    held |= bits
            m.write(0x00B0, bytes([held]))
            scene = m.read(scene_at, 1)[0] if scene_at is not None else 0
            # Ground truth: the hardware OAM as the last commit left it (PR.C != 0 = shown).
            oam = m.read(0x8800, 256)
            shown = {oam[4 * i] | ((oam[4 * i + 1] & 1) << 8) for i in range(64) if oam[4 * i + 1] & 0x18}
            current = {"tick": frame, "scene": scene, "line_tick": stop.scanline, "lines": {},
                       "frames": set(), "calls": 0, "oam_tiles": len(shown),
                       "oam_sprites": sum(1 for i in range(64) if oam[4 * i + 1] & 0x18)}
            continue
        if current is None:
            continue
        if stop.stop_pc in steps:
            name = steps[stop.stop_pc]
            current["lines"].setdefault(name, stop.scanline)   # first call in the tick
            continue
        # ngc_sprite_draw(frame, x, y): return address at XSP, then the arguments.
        sp = m.cpu().regs[xsp]
        f, x, y = struct.unpack("<HHH", m.read(sp + 4, 6))
        current["calls"] += 1
        t = tables.get(current["scene"])
        if t and drawn(t, f, signed(x), signed(y)):
            current["frames"].add(f)
            current.setdefault("order", []).append(f)     # draw order = OAM order
    out = []
    for i, c in enumerate(series):
        t = tables.get(c["scene"], {})
        need = set().union(*(frame_tiles(t, f) for f in c["frames"])) if c["frames"] and t else set()
        before = series[i - 1]["frames"] if i and series[i - 1]["scene"] == c["scene"] else set()
        new = c["frames"] - before
        copy = set().union(*(frame_tiles(t, f) for f in new)) if new and t else set()
        out.append({"tick": c["tick"], "scene": c["scene"], "calls": c["calls"], "frames_drawn": len(c["frames"]),
                    "tiles_needed": len(need), "new_frames": len(new), "tiles_to_copy": len(copy),
                    "line_tick": c["line_tick"], "lines": c["lines"],
                    "oam_tiles": c["oam_tiles"], "oam_sprites": c["oam_sprites"],
                    "frame_ids": sorted(c["frames"]), "frame_order": c.get("order", [])})
    return out


def summary(series):
    def stat(key):
        values = [s[key] for s in series]
        return {"max": max(values), "median": statistics.median(values),
                "p99": sorted(values)[int(0.99 * (len(values) - 1))]} if values else {}
    lines = {}
    for name in COMMITS:
        values = [s["lines"][name] for s in series if name in s["lines"]]
        if values:
            lines[name] = {"min": min(values), "median": statistics.median(values), "max": max(values)}
    # The labs's count against the hardware OAM, which shows a drawing one or two ticks later.
    agree = {}
    for lag in range(4):
        pairs = [(series[i]["tiles_needed"], series[i + lag]["oam_tiles"]) for i in range(len(series) - lag)
                 if series[i]["scene"] == series[i + lag]["scene"]]
        agree[lag] = round(sum(1 for a, b in pairs if a == b) / len(pairs), 4) if pairs else None
    return {"ticks": len(series), "scenes": sorted({s["scene"] for s in series}), "oam_agreement_by_lag": agree,
            "oam_tiles": stat("oam_tiles"),
            "frames_drawn": stat("frames_drawn"), "tiles_needed": stat("tiles_needed"),
            "new_frames": stat("new_frames"), "tiles_to_copy": stat("tiles_to_copy"),
            "ticks_copying": sum(1 for s in series if s["tiles_to_copy"]),
            "line_at_tick": {"min": min(s["line_tick"] for s in series), "max": max(s["line_tick"] for s in series)},
            "commit_lines": lines}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("project", "out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--start")
    p.add_argument("--x", type=int)
    p.add_argument("--y", type=int)
    p.add_argument("--frames", type=int, default=1200)
    p.add_argument("--inputs", type=Path)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out}: output must be new")
    project = a.out / "project"
    shutil.copytree(a.project, project, ignore=shutil.ignore_patterns("build", "build_export"))
    if a.start:
        path = project / "project/settings.gbsres"
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings["startSceneId"] = a.start
        if a.x is not None:
            settings.update(startX=a.x, startY=a.y)
        write_json(path, settings)
    inputs = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else []
    export_project(project, a.out / "export")
    build_rom(a.out / "export", a.toolchain)
    series = run(a.out / "export", a.frames, inputs, a.emulator, a.bios)
    result = {"schema": "ngpcraft-tile-demand/1", "project": str(a.project), "start": a.start,
              "inputs": inputs, "summary": summary(series), "series": series,
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False}
    write_json(a.out / "result.json", result)
    print(json.dumps(result["summary"], indent=1))


if __name__ == "__main__":
    main()
