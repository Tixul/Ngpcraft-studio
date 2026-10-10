"""Observe an exported ROM externally; no test instrumentation in the game.

Requires the user's native NgpCraft emulator and BIOS, supplied via arguments.
Writes raw palette/framebuffer transitions and PPM screenshots without Pillow.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

# A console powers on with random work RAM; the emulator starts with zeros,
# which hid an uninitialised palette copy until the console showed it
# (2026-09-29). Probes fill the user RAM with fixed pseudo-random bytes.
USER_RAM = (0x4000, 0x6C00)
RAM_NOISE_SEED = 0x5EED


def power_on_ram(machine, mode):
    if mode == "zero":
        return {"ram": "zero"}
    start, end = USER_RAM
    rng = random.Random(RAM_NOISE_SEED)
    machine.write(start, bytes(rng.randrange(256) for _ in range(end - start)))
    return {"ram": "noise", "ram_seed": RAM_NOISE_SEED, "ram_range": [start, end]}


def positive_int(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def verified_tick(rom, build):
    """Never observe a different cartridge using stale link addresses."""
    header = build.get("header", {})
    if (build.get("stage") != "linked-rom" or build.get("complete_rom") is not True
            or header.get("sha256") != hashlib.sha256(rom).hexdigest()
            or header.get("size_bytes") != len(rom)):
        raise ValueError("ROM does not match a complete build report; rebuild in a fresh export")
    try:
        symbol = build["link_audit"]["public_symbols"]["_ngc_game_tick"]
        tick = symbol["address"]
    except (KeyError, TypeError) as exc:
        raise ValueError("build report has no game tick symbol") from exc
    if (type(tick) is not int or symbol.get("section") != "f_code"
            or not 0x200040 <= tick < 0x200000 + len(rom)):
        raise ValueError("game tick symbol is outside cartridge code")
    return tick


def write_frame(path, pixels):
    rgb = bytes(component for pixel in pixels for component in
                ((pixel & 15) * 17, ((pixel >> 4) & 15) * 17, ((pixel >> 8) & 15) * 17))
    path.write_bytes(b"P6\n160 152\n255\n" + rgb)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--emulator", type=Path, required=True)
    parser.add_argument("--bios", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--boot", choices=["handoff", "bios"], default="handoff")
    parser.add_argument("--ram", choices=["noise", "zero"], default="noise",
                        help="work RAM at power-on (noise = like a console, the default)")
    parser.add_argument("--frames", type=positive_int, default=240)
    parser.add_argument("--boot-budget", type=positive_int, default=15_000_000,
                        help="maximum instructions before reaching the game tick")
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("output directory must be new or empty")
    rom = (args.export_dir / "rom/game.ngc").read_bytes()
    build = json.loads((args.export_dir / "rom/build-report.json").read_text())
    try:
        tick = verified_tick(rom, build)
    except ValueError as exc:
        parser.error(str(exc))
    bios = args.bios.read_bytes()
    sys.path.insert(0, str(args.emulator.resolve()))
    from core import native
    args.out.mkdir(parents=True, exist_ok=True)
    m = native.NativeMachine(rom, bios=bios)
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True, real_bios=args.boot == "bios")
    ram = power_on_ram(m, args.ram)
    evidence = {"boot": args.boot, **ram, "rom_sha256": hashlib.sha256(rom).hexdigest(),
                "bios_sha256": hashlib.sha256(bios).hexdigest(),
                "build_report_sha256": hashlib.sha256(
                    (args.export_dir / "rom/build-report.json").read_bytes()).hexdigest(),
                "emulator": str(args.emulator.resolve()),
                "emulator_binding_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
                "emulator_core_sha256": hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
                "silicon_timing": {"word_wait": 10, "bios_wait": 8},
                "boot_instruction_budget": args.boot_budget, "game_tick_address": tick}
    m.set_breakpoints([tick])
    reached = False
    for offset in range(0, args.boot_budget, 500_000):
        summary, _ = m.run(min(500_000, args.boot_budget - offset), record=False)
        if summary.stop_status == 40:
            reached = True
            break
    m.set_breakpoints([])
    if not reached:
        write_frame(args.out / "boot-failure.ppm", m.framebuffer())
        failure = {**evidence, "game_entry_reached": False, "pc": m.cpu().pc,
                   "status": summary.stop_status, "screenshot": "boot-failure.ppm",
                   "note": "Full BIOS boot may require console setup/power input; this probe does not automate it."}
        (args.out / "probe.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        raise SystemExit("game entry not reached; see probe.json (this is not a successful ROM validation)")
    transitions, visual, samples = [], [], []
    last_palette = last_pixel = None
    for frame in range(args.frames):
        summary = m.run_frames(1)
        palette = int.from_bytes(m.read(0x83E0, 2), "little") & 4095
        pixels = m.framebuffer()
        center = pixels[76 * 160 + 80] & 4095
        samples.append({"frame": frame, "palette": palette, "center": center,
                        "pixel_colors": sorted(set(v & 4095 for v in pixels)),
                        "status": summary.stop_status, "pc": m.cpu().pc})
        if palette != last_palette:
            transitions.append({"frame": frame, "color": palette})
            last_palette = palette
        if center != last_pixel:
            visual.append({"frame": frame, "color": center})
            last_pixel = center
            write_frame(args.out / f"frame-{frame:04d}.ppm", pixels)
    result = {**evidence, "game_entry_reached": reached,
              "palette_transitions": transitions, "framebuffer_transitions": visual, "samples": samples,
              "scr1_palette_bytes": list(m.read(0x8280, 128))}
    (args.out / "probe.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("samples", "scr1_palette_bytes")}, indent=2))


if __name__ == "__main__":
    main()
