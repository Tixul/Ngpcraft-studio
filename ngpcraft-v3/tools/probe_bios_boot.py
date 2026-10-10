"""P4 attempt: autonomous BIOS -> cartridge boot on the native core.

Two machines, both in real-BIOS mode with silicon timing:

1. *Configuration* (declared intervention): a console whose coin cell was never
   configured sits in the first-boot wizard. We answer it once, the way a user
   does when the console is new, by writing its "setup confirmed" flag
   (0x64E5 = 0xFF, see Ngpcraft_emulator ngpc_shell.py _bios_handoff_assist).
   The resulting coin cell is kept in memory only.
2. *Boot* (no intervention at all): a fresh machine receives that coin cell,
   powers on, and we only watch for the cartridge entry point.

Nothing is written to the user's saves/system.ram. The ROM is not modified.
Exit status 0 only when the entry point is reached without intervention.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

WIZARD = (0xFF3000, 0xFF4000)
SETUP_CONFIRMED = 0x0064E5
BOOT_MARKER = 0x006C7A


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("export_dir", type=Path)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--config-frames", type=int, default=900)
    p.add_argument("--boot-frames", type=int, default=1500)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        p.error("output file exists; choose a new path")
    rom = (a.export_dir / "rom/game.ngc").read_bytes()
    bios = a.bios.read_bytes()
    entry = int.from_bytes(rom[28:32], "little")
    sys.path.insert(0, str(a.emulator.resolve()))
    from core import native

    def machine(coin):
        m = native.NativeMachine(rom, bios=bios)
        m.set_timing_silicon(word_wait=10, bios_wait=8)
        m.set_battery_ram(coin)
        m.reset(bios_handoff=False, real_bios=True)
        return m

    cfg = machine(None)
    interventions = []
    for frame in range(a.config_frames):
        cfg.run_frames(1)
        pc = cfg.cpu().pc
        if WIZARD[0] <= pc < WIZARD[1]:
            cfg.write(SETUP_CONFIRMED, b"\xFF")
            interventions.append({"frame": frame, "pc": pc, "write": [SETUP_CONFIRMED, 0xFF]})
    coin = cfg.battery_ram()

    boot = machine(coin)
    boot.set_breakpoints([entry])
    reached, pages = None, {}
    for frame in range(a.boot_frames):
        if boot.run_frames(1).stop_status == 40:
            reached = frame
            break
        page = boot.cpu().pc & 0xFFFF00
        pages[page] = pages.get(page, 0) + 1
    report = {
        "rom_sha256": sha(rom), "bios_sha256": sha(bios),
        "emulator_core_sha256": sha(Path(boot._lib._name).read_bytes()),
        "silicon_timing": {"word_wait": 10, "bios_wait": 8},
        "entry": entry,
        "configuration": {"frames": a.config_frames, "interventions": interventions,
                          "boot_marker": cfg.read(BOOT_MARKER, 2).hex(),
                          "coin_cell_sha256": sha(coin)},
        "boot": {"frames": a.boot_frames, "interventions": [],
                 "entry_reached_frame": reached, "final_pc": boot.cpu().pc,
                 "top_pc_pages": sorted(({"page": k, "frames": v} for k, v in pages.items()),
                                        key=lambda x: -x["frames"])[:8],
                 "header_seen_by_cpu": boot.read(0x200000, 32).hex()},
        "verdict": "P4" if reached is not None else "not-reached",
        "command": sys.argv,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("verdict", "entry")} |
                     {"final_pc": hex(report["boot"]["final_pc"])}))
    return 0 if reached is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
