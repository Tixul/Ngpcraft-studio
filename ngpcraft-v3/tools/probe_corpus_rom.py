"""Observe a corpus ROM under physical input pulses, without modifying its code.

This smoke scenario is not a walkthrough or a proof of every scene's behavior.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.project import _atomic_write
from tools.music_rom import png
from tools.probe_rom import power_on_ram, verified_tick


def run(export, out, emulator, bios, frames=600):
    export, out = Path(export), Path(out)
    if out.exists():
        raise ValueError("output must be new")
    build = json.loads((export / "rom/build-report.json").read_text())
    rom = (export / "rom/game.ngc").read_bytes()
    bios_data = Path(bios).read_bytes()
    tick = verified_tick(rom, build)
    out.mkdir(parents=True)
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=bios_data)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    machine.set_breakpoints([tick])
    stop, _ = machine.run(15_000_000, record=False)
    if stop.stop_status != 40:
        raise ValueError("game entry not reached")
    machine.set_breakpoints([])
    symbols = build["link_audit"]["public_symbols"]
    def read(name, size=1):
        symbol = symbols.get("_" + name)
        return int.from_bytes(machine.read(symbol["address"], size), "little") if symbol else None
    rows, pictures = [], []
    for frame in range(frames):
        # A/B pulses, followed by directional input and further A pulses.
        port = 16 if 40 <= frame < 44 or 70 <= frame < 200 and frame % 20 < 5 else (
            4 if 220 <= frame < 250 else 32 if 260 <= frame < 264 else 8 if 380 <= frame < 410 else
            16 if 430 <= frame < 550 and frame % 30 < 5 else 0)
        machine.write(0xB0, bytes([port]))
        stop = machine.run_frames(1)
        rows.append({"frame": frame, "scene": read("ngc_scene"), "player": [read("act_x", 2), read("act_y", 2)],
                     "port": port, "colors": len(set(machine.framebuffer()))})
        if frame in (25, 65, 210, 255, 275, 350, 500, frames - 1):
            name = f"frame-{frame:04d}-scene-{read('ngc_scene')}.png"
            (out / name).write_bytes(png(machine.framebuffer()))
            pictures.append(name)
    report = {"schema": "ngpcraft-corpus-rom-probe/1", "frames": frames, "inputs": "A/B and directional smoke pulses",
              "scenes_observed": sorted({r["scene"] for r in rows if r["scene"] is not None}),
              "screenshots": pictures, "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "bios_sha256": hashlib.sha256(bios_data).hexdigest(), "silicon_timing": [10, 8], "ram": "noise",
              "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "rows": rows, "console_validated": False,
              "scope": "Normal entry and physical input smoke test; complete gameplay, all scenes and audio fidelity unverified."}
    _atomic_write(out / "probe.json", json.dumps(report, indent=2) + "\n")
    return {k: v for k, v in report.items() if k != "rows"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export")
    parser.add_argument("out")
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--bios", required=True)
    parser.add_argument("--frames", type=int, default=600)
    args = parser.parse_args()
    if args.frames <= 0:
        parser.error("frames must be positive")
    print(json.dumps(run(args.export, args.out, args.emulator, args.bios, args.frames), indent=2))
