"""Frame rate of a project, scene by scene, and where the cycles go (docs/ETUDE_CADENCE.md).

The project (copied, never modified) is exported once per scene measured, the
scene as start scene, and played on the native core (silicon timing, noisy
power-on RAM) with the same held buttons (written to the port 0xB0 and to its
BIOS copy 0x6F82 at each tick entry, the one the game reads). Per scene:

- rate: game ticks per vertical blank over the window, from GBVM's sys_time
  (ngc_sys_time, +1 per vertical blank): 60 = every tick in its frame;
- ticks holding modal frames (a dialogue or menu waiting, GBVM ui_run_modal) are counted
  apart: their frames are not game ticks;
- work per tick: cycles from the tick entry to ngc_vm_frame_end (the wait for the
  next blank excluded); over one frame (102 485 cycles) the tick costs an image;
- self cycles per function over the profiled ticks (all, and those over one frame).
  Static functions are made public in a throw-away copy of the export so that each
  is billed to itself; the measured ROM is that copy (same code).

  python -B -m tools.lab_cadence --project <dir> --out <new dir> --scene NAME [--scene ...]
         [--ticks 900] [--skip 60] [--profile 240] [--inputs run.json]
         --toolchain C:/t900 --emulator <Ngpcraft_emulator> --bios <bios>
"""
import argparse
import bisect
import collections
import json
from pathlib import Path
import re
import shutil
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ngpcraft.toolchain as toolchain  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.lab_multi_banks import held_at, rehash  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402

FRAME = 102_485          # cycles per frame (199 lines of 515)
STATIC_FUNCTION = re.compile(r"^static (?!const\b)((?:(?:unsigned|signed|volatile|struct)\s+)*\w+[\s*]+\w+\s*\()", re.M)
WAIT = {"_ngc_vm_frame_end", "_wait_vbl_done"}


class _AnyPublic(set):
    """The link audit's expected public symbols, for the profiling copy only."""
    def __ne__(self, other):
        return False


def expose_statics(export_dir):
    """Every static function of the export made public (scene_link already prefixes
    each scene's names s<k>_)."""
    for path in sorted((export_dir / "src").glob("*.c")):
        text = path.read_text(encoding="utf-8")
        new, count = STATIC_FUNCTION.subn(r"\1", text)
        # startup.c's VBlank counter, the clock of last resort (measure()).
        frame = "static volatile unsigned char ngc_frame;"
        if frame in new:
            new, count = new.replace(frame, "volatile unsigned char ngc_frame;"), count + 1
        if count:
            path.write_text(new, encoding="utf-8")
            rehash(export_dir, f"src/{path.name}")


def build_exposed(export_dir, tool):
    real = toolchain.public_symbols
    toolchain.public_symbols = lambda features: _AnyPublic(real(features))
    try:
        return toolchain.build_rom(export_dir, tool)
    finally:
        toolchain.public_symbols = real


def measure(export_dir, emulator, bios, inputs, ticks, skip, profile):
    rom = (export_dir / "rom/game.ngc").read_bytes()
    build = json.loads((export_dir / "rom/build-report.json").read_text(encoding="utf-8"))
    sym = build["link_audit"]["public_symbols"]
    tick_pc = verified_tick(rom, build)
    end_pc = sym["_ngc_vm_frame_end"]["address"]
    # GBVM sys_time when a script uses it, else startup.c's VBlank counter (one byte).
    if "_ngc_sys_time" in sym:
        clock, clock_bytes = sym["_ngc_sys_time"]["address"], 2
    elif "_g_vb_counter" in sym:
        clock, clock_bytes = sym["_g_vb_counter"]["address"], 1
    else:
        clock, clock_bytes = sym["_ngc_frame"]["address"], 1
    scene_at = sym.get("_ngc_scene", {}).get("address")      # absent from a one-scene export
    code = sorted((v["address"], k) for k, v in sym.items() if v["section"] == "f_code")
    addrs = [a for a, _ in code]
    if str(emulator) not in sys.path:
        sys.path.insert(0, str(emulator))
    from core import native
    m = native.NativeMachine(rom, bios=bios.read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    modal_pc = sym.get("_ngc_ui_modal_commit", {}).get("address")
    m.set_breakpoints([tick_pc, end_pc] + ([modal_pc] if modal_pc else []))
    modal = [0]

    def owner(pc):
        if pc >= 0xFF0000:
            return "BIOS"
        if pc < 0x200000:
            return "RAM"
        i = bisect.bisect_right(addrs, pc) - 1
        return code[i][1] if i >= 0 else hex(pc)

    def run_to(pc, spent=None):
        """Run to the breakpoint at pc (the other one may be met first: run again).
        Returns the cycles run (stop.total_cycles counts one call)."""
        cycles = 0
        while True:
            stop, records = m.run(5_000_000, record=spent is not None)
            if spent is not None:
                for rec in records:
                    spent[owner(rec.pc)] += rec.cycles
            cycles += stop.total_cycles
            if stop.stop_status != 40:
                raise RuntimeError("breakpoint not reached")
            if stop.stop_pc == modal_pc:
                modal[0] += 1                # a modal frame (dialogue, menu) inside the tick
            if stop.stop_pc == pc:
                return cycles

    run_to(tick_pc)
    times, work, scenes, modal_ticks = [], [], collections.Counter(), set()
    all_ticks, late = collections.Counter(), collections.Counter()
    profiled = late_ticks = 0
    for k in range(ticks):
        held = held_at(inputs, k)
        m.write(0x00B0, bytes([held]))
        m.write(0x6F82, bytes([held]))
        times.append(int.from_bytes(bytes(m.read(clock, clock_bytes)), "little"))
        scenes[m.read(scene_at, 1)[0] if scene_at is not None else 0] += 1
        spent = collections.Counter() if skip <= k < skip + profile else None
        modal[0] = 0
        busy = run_to(end_pc, spent)
        run_to(tick_pc, spent)             # the wait, then the next tick entry
        if modal[0]:
            modal_ticks.add(k)             # its frames are dialogue frames: counted apart
            continue
        work.append((k, busy))
        if spent is not None:
            profiled += 1
            all_ticks.update(spent)
            if busy > FRAME:
                late_ticks += 1
                late.update(spent)
    wrap = 1 << (8 * clock_bytes)
    window = [(b - a) % wrap for k, (a, b) in enumerate(zip(times, times[1:])) if k >= skip and k not in modal_ticks]
    measured = sorted(b for k, b in work if k >= skip) or [0]
    top = lambda c, n: [[f, round(v / max(1, n))] for f, v in c.most_common(30) if f not in WAIT]
    return {"scenes": dict(scenes), "ticks": ticks, "modal_ticks": len(modal_ticks),
            "frames_per_second": round(60 * len(window) / max(1, sum(window)), 1),
            "ticks_over_one_frame": sum(1 for v in window if v > 1),
            "work": {"median": statistics.median(measured), "p90": measured[int(0.9 * (len(measured) - 1))],
                     "max": measured[-1], "over_frame": sum(1 for v in measured if v > FRAME)},
            "profiled_ticks": profiled, "profiled_late_ticks": late_ticks,
            "self_all": top(all_ticks, profiled), "self_late": top(late, late_ticks)}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("project", "out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--scene", action="append", required=True,
                   help="scene name, or NAME=inputs.json for its own buttons (repeatable)")
    p.add_argument("--ticks", type=int, default=900)
    p.add_argument("--skip", type=int, default=60)
    p.add_argument("--profile", type=int, default=240)
    p.add_argument("--inputs", type=Path)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out}: output must be new")
    inputs = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else []
    result = {"schema": "ngpcraft-cadence-lab/1", "project": str(a.project), "inputs": inputs,
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False, "scenes": {}}
    project = a.out / "project"
    shutil.copytree(a.project, project, ignore=shutil.ignore_patterns("build", "build_export"))
    path = project / "project/settings.gbsres"
    settings = json.loads(path.read_text(encoding="utf-8"))
    ids = {}
    for scene in (project / "project/scenes").rglob("*.gbsres"):
        data = json.loads(scene.read_text(encoding="utf-8"))
        if data.get("_resourceType") == "scene":
            ids[data["name"]] = data["id"]
    for n, name in enumerate(a.scene):
        scene_inputs = inputs
        if "=" in name:
            name, own = name.rsplit("=", 1)
            scene_inputs = json.loads(Path(own).read_text(encoding="utf-8"))
        if name not in ids:
            raise SystemExit(f"unknown scene {name!r}; known: {sorted(ids)}")
        settings["startSceneId"] = ids[name]
        write_json(path, settings)
        export = a.out / f"scene{n}/export"
        export_project(project, export)
        expose_statics(export)
        build_exposed(export, a.toolchain)
        result["scenes"][name] = measure(export, a.emulator, a.bios, scene_inputs, a.ticks, a.skip, a.profile)
        write_json(a.out / "result.json", result)
        s = result["scenes"][name]
        print(name, {k: s.get(k) for k in ("frames_per_second", "ticks_over_one_frame", "modal_ticks", "work")}, flush=True)


if __name__ == "__main__":
    main()
