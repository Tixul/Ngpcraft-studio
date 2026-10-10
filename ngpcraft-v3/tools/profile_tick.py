"""Cycles of each module of the game tick, measured from outside the ROM.

The calls of `ngc_game_tick` are read in the exported `src/game.c`; each
callee with a public cartridge address gets a breakpoint at its entry. On a
hit, the return address is read on the stack (as `probe_gbvm` does for the
modal commit) and the machine runs to it with no other breakpoint: the time
is inclusive, nested profiled calls are not counted twice. Repeated calls in
one tick (the script runner loop) are summed for that tick.

`ngc_vm_frame_end` waits for the next vertical blank: it is reported apart
("wait"), never as work. `glue` is the tick's own code between the calls.
Interrupt handlers count in the function they interrupt. Nothing is added to
the ROM; a stale build report is refused (`verified_tick`). Scenes with text
(a modal commit inside the tick) are refused: their frames are not ticks.

  python -B tools/profile_tick.py <export_dir> --emulator <dir> --bios <file>
         --out <new dir> [--frames N] [--skip N] [--inputs schedule.json]
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402  (before the emulator's own `tools`)

LINE_CYCLES = 515
FRAME_CYCLES = 515 * 199      # Ngpcraft_emulator core.cpp: cycles per line x lines per frame
BREAKPOINT = 40               # Summary.stop_status on a breakpoint (probe_gbvm)
WAIT = "ngc_vm_frame_end"
KEYWORDS = ("if", "while", "for", "switch", "return", "sizeof")


def tick_calls(game_c):
    """Function names called by ngc_game_tick, in source order, once each."""
    body = re.search(r"void ngc_game_tick\(void\)\n\{\n(.*?)\n\}\n", game_c, re.S)
    if not body:
        raise ValueError("ngc_game_tick not found in src/game.c")
    names = []
    for name in re.findall(r"\b([A-Za-z_]\w*)\s*\(", body.group(1)):
        if name not in names and name not in KEYWORDS:
            names.append(name)
    return names


def resolve(names, symbols, rom_size):
    entries = {}
    for name in names:
        symbol = symbols.get("_" + name)
        if (symbol and symbol.get("section") == "f_code" and type(symbol.get("address")) is int
                and 0x200040 <= symbol["address"] < 0x200000 + rom_size):
            entries[symbol["address"]] = name
    return entries


def summary(values):
    middle = statistics.median(values)
    return {"median": middle, "mean": round(statistics.fmean(values), 1),
            "min": min(values), "max": max(values),
            "median_lines": round(middle / LINE_CYCLES, 2),
            "max_lines": round(max(values) / LINE_CYCLES, 2),
            "median_percent_frame": round(100 * middle / FRAME_CYCLES, 2)}


def measure(export_dir, emulator, bios_path, frames, skip=2, schedule=None, ram="noise"):
    rom = (export_dir / "rom/game.ngc").read_bytes()
    build = json.loads((export_dir / "rom/build-report.json").read_text(encoding="utf-8"))
    manifest = json.loads((export_dir / "export.json").read_text(encoding="utf-8"))
    sources = [s.get("source", "") for s in manifest["scripts"].values()]
    if "text" in manifest["features"] and any(".UI_MODAL" in s or "VM_CHOICE" in s for s in sources):
        raise ValueError("modal text runs whole frames inside a tick; profile a scene without modal waits")
    tick = verified_tick(rom, build)
    names = tick_calls((export_dir / "src/game.c").read_text(encoding="utf-8"))
    entries = resolve(names, build["link_audit"]["public_symbols"], len(rom))
    bios = bios_path.read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    noise = power_on_ram(machine, ram)

    def run(points):
        machine.set_breakpoints(points)
        stop, _ = machine.run(3_000_000, record=False)
        if stop.stop_status != BREAKPOINT:
            raise SystemExit(f"breakpoint not reached (status {stop.stop_status})")
        return stop

    def hold(frame):
        if schedule is not None:
            mask = 0
            for start, end, bits in schedule:
                if start <= frame < end:
                    mask |= bits
            machine.write(0x00B0, bytes([mask]))   # hardware port; never write Sys Lever

    stop = run([tick])
    rows = []
    for frame in range(frames):
        hold(frame)
        entry_frame = stop.frame_count
        row = {name: 0 for name in entries.values()}
        glue = 0
        points = list(entries) + [tick]
        while True:
            stop = run(points)
            glue += stop.total_cycles
            if stop.stop_pc == tick:
                break
            name = entries[stop.stop_pc]
            back = int.from_bytes(machine.read(machine.cpu().regs[7], 4), "little") & 0xFFFFFF
            done = run([back])
            row[name] += done.total_cycles
        # The last stretch (frame end return to the next tick entry) is the main
        # loop and, at 30 images/s, the second vertical blank: it is waiting.
        row["glue"] = glue
        row["physical_frames"] = stop.frame_count - entry_frame
        rows.append(row)
    steady = rows[skip:]
    work_names = [n for n in entries.values() if n != WAIT]
    work = [sum(r[n] for n in work_names) + r["glue"] for r in steady]
    return {
        "scope": "tick entry to next tick entry; inclusive per callee; wait = " + WAIT
                 + "; glue includes the main loop back to the tick",
        "export": str(export_dir), "rom_sha256": hashlib.sha256(rom).hexdigest(),
        "bios_sha256": hashlib.sha256(bios).hexdigest(),
        "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
        "silicon_timing": {"word_wait": 10, "bios_wait": 8}, "boot": "handoff", **noise,
        "frames": frames, "skip": skip, "tick_calls": names,
        "unresolved_calls": [n for n in names if n not in entries.values()],
        "frame_cycles": FRAME_CYCLES,
        "work": summary(work),
        "modules": {n: summary([r[n] for r in steady]) for n in list(entries.values()) + ["glue"]},
        "late_ticks": sum(r["physical_frames"] != 1 for r in steady),
        "rows": rows,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("export_dir", type=Path)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--frames", type=int, default=300)
    p.add_argument("--skip", type=int, default=2, help="first ticks left out of the statistics (scene start)")
    p.add_argument("--inputs", type=Path, help="[[start, end, mask], ...] held on port 0xB0, as probe_gbvm")
    p.add_argument("--ram", choices=["noise", "zero"], default="noise")
    args = p.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        p.error("output directory must be new or empty")
    if args.frames <= args.skip:
        p.error("--frames must be greater than --skip")
    schedule = json.loads(args.inputs.read_text(encoding="utf-8")) if args.inputs else None
    result = measure(args.export_dir, args.emulator, args.bios, args.frames, args.skip, schedule, args.ram)
    args.out.mkdir(parents=True, exist_ok=True)
    temp = args.out / "profile.json.tmp"
    temp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temp.replace(args.out / "profile.json")
    print(json.dumps({"work": result["work"], "late_ticks": result["late_ticks"],
                      "unresolved": result["unresolved_calls"],
                      "modules": {k: v["median"] for k, v in result["modules"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
