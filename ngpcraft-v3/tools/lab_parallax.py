"""GB Studio scene parallax in a native ROM, compared pixel for pixel
(docs/M6_PARALLAX.md).

gbs2 "Parallax Example" (3 slices: rows 0-9 at 1/16, 10-12 at 1/2, the rest
with the camera), its actors removed and the player hidden: every visible pixel
is background.
Each frame, the picture must equal the background composed as GBVM shows it:
screen line L belongs to the slice whose first row is above it; that slice's x
scroll is the camera scroll (ngc_scroll, read in the ROM) shifted by its speed
(scroll.c scroll_viewport), its rows at their place (SCY 0, no vertical scroll).
--shake: a timer script shakes the camera diagonally (magnitude 3) every
second: upper slices keep SCY 0 and the shaken x shifted, the last slice
follows the shaken camera; pixels showing cells outside the map, or ring rows
the last slice never loads (above its first row), are not compared (their
content is ECARTS 17's border, not GB Studio's).
Witnesses (--witness): the split one line late, the chain off.

  python -B -m tools.lab_parallax --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> [--witness late|off] [--layers JSON] [--shake]
"""
from array import array
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from ngpcraft.background_stream import view_of
from ngpcraft.parallax import FIXED, bands, compile_parallax
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_source
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.probe_rom import power_on_ram, verified_tick, write_frame

RIGHT, LEFT, A = 8, 4, 16
# Walk right across the scene, jumps, walk back, run right again.
SCHEDULE = [(10, 260, RIGHT), (60, 64, A), (150, 154, A), (280, 420, LEFT), (440, 600, RIGHT)]


def project_case(root, layers=None, shake=False):
    derive(GBS2, root / "project", "parallax_example", (4, 10), fade=False)
    path = next((root / "project/project/scenes").rglob("scene.gbsres"))
    for sub in ("actors", "triggers"):          # background only: nothing drawn over it
        if (path.parent / sub).is_dir():
            shutil.rmtree(path.parent / sub)
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["script"] = [ev("hide-player", "EVENT_ACTOR_HIDE", actorId="player")]
    if shake:
        timer = ev("shake-timer", "EVENT_SET_TIMER_SCRIPT", timer=1, units="time", duration=1)
        timer["children"] = {"script": [ev("shake", "EVENT_CAMERA_SHAKE", units="frames", frames=40,
                                           shakeDirection="diagonal", magnitude=num(3))]}
        scene["script"].append(timer)
    if layers is not None:
        scene["parallax"] = layers
    write_json(path, scene)
    return root / "project"


def witness_source(export, witness):
    path = export / "src/parallax.c"
    source = path.read_text(encoding="utf-8")
    old = {"late": "(unsigned char)(ngc_parallax_line[0] + 1u)", "off": "if (!ngc_parallax_n) return;"}[witness]
    new = {"late": "(unsigned char)(ngc_parallax_line[0] + 2u)", "off": "return;"}[witness]
    if source.count(old) != 1:
        raise ValueError(f"witness {witness}: anchor not found")
    path.write_text(source.replace(old, new), encoding="utf-8")
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    manifest["sha256"]["src/parallax.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(export / "export.json", manifest)


def expected_picture(bg, slices, starts, top, view_h, scroll_x, scroll_y):
    """The 160x152 picture GBVM's slices give (colours of the compiled
    background); None where the comparison does not apply (see --shake)."""
    picture = [0] * (160 * 152)
    for line in range(top, top + view_h):
        b = max(k for k, start in enumerate(starts) if start <= line)
        first, _, shift = slices[b]
        x = scroll_x if shift is None else 0 if shift == FIXED else scroll_x >> shift
        y = (line - top) + (scroll_y if shift is None else 0)
        if not first * 8 <= y < bg.height:
            picture[line * 160:(line + 1) * 160] = [None] * 160
            continue
        row = bg.pixels[y * bg.width:(y + 1) * bg.width]
        picture[line * 160:(line + 1) * 160] = [row[x + i] if 0 <= x + i < bg.width else None for i in range(160)]
    return picture


def run(out, toolchain, emulator, bios, frames=620, witness=None, layers=None, shake=False):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    project_path = project_case(out, layers, shake)
    manifest = export_project(project_path, out / "export")
    if witness:
        witness_source(out / "export", witness)
    build = build_rom(out / "export", Path(toolchain))
    project = load_project(project_path)
    scene = project.scenes[0]
    bg = background_source(project, scene)
    view_h, top = view_of(bg)
    steps = compile_parallax(scene)
    slices, lines = bands(steps, bg.height // 8, view_h, top, "lab") if steps else ([(0, bg.height // 8, None)], [])
    starts = [top] + lines
    symbols = build["link_audit"]["public_symbols"]
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    tick = verified_tick(rom, build)
    machine.set_breakpoints([tick])
    scroll = symbols["_ngc_scroll"]["address"]
    mismatches, lines_wrong, scrolls, previous, dropped, shaken = [], {}, set(), None, [], set()
    for k in range(frames + 1):
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError(f"tick {k} not reached")
        if previous is not None and k > 3 and stop.frame_count - previous != 1:
            dropped.append(k)
        previous = stop.frame_count
        sx = int.from_bytes(machine.read(scroll, 2), "little", signed=True)
        sy = int.from_bytes(machine.read(scroll + 2, 2), "little", signed=True)
        scrolls.add(sx)
        if k >= 3:
            picture = array("H", (v & 4095 for v in machine.framebuffer()))
            expected = expected_picture(bg, slices, starts, top, view_h, sx, sy)
            wrong = sorted({j // 160 for j, (a, e) in enumerate(zip(picture, expected)) if e is not None and a != e})
            shaken.add((sx, sy))
            if wrong:
                mismatches.append({"tick": k, "scroll_x": sx, "lines": wrong[:8], "count": len(wrong)})
                for line in wrong:
                    lines_wrong[line] = lines_wrong.get(line, 0) + 1
            if k in (200, 400, 600):
                write_frame(out / f"frame-{k}.ppm", machine.framebuffer())
        held = 0
        for start, end, mask in SCHEDULE:
            if start <= k < end:
                held |= mask
        machine.set_breakpoints([])
        machine.run(1, record=False)
        machine.set_breakpoints([tick])
        machine.write(0x00B0, bytes([held]))
    result = {"frames": frames, "witness": witness, "slices": [list(s) for s in slices], "split_lines": lines,
              "scroll_x_values": len(scrolls), "scroll_x_range": [min(scrolls), max(scrolls)],
              "scroll_y_values": sorted({y for _, y in shaken}),
              "mismatch_frames": len(mismatches), "first": mismatches[:3],
              "lines_wrong": dict(sorted(lines_wrong.items())[:12]), "dropped_frames": dropped,
              "features": manifest["features"], "parallax_report": manifest["scene_engine"].get("parallax"),
              "rom_sha256": hashlib.sha256(rom).hexdigest()}
    write_json(out / "comparison.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--frames", type=int, default=620)
    p.add_argument("--witness", choices=("late", "off"))
    p.add_argument("--layers", type=json.loads)
    p.add_argument("--shake", action="store_true")
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.frames, a.witness, a.layers, a.shake)
    print(json.dumps(r, indent=2))
    return int(bool(r["mismatch_frames"]) != bool(a.witness))


if __name__ == "__main__":
    raise SystemExit(main())
