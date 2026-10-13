"""Cost of the engine's block copies on the native core (silicon timing):
LDIRW / LDIR as they run in a real export (docs/ETUDE_RECYCLAGE_TUILES.md T1).

Breaks on ngc_sprites_commit (shadow OAM -> 0x8800, RAM -> video RAM) and on
ngc_sprite_draw (frame words ROM -> shadow, ROM -> RAM), records the
instructions that follow and reports, for each LDIRW/LDIR met: source and
destination regions, words moved, cycles, cycles per word. A repeated block
instruction may be one record or one per element: both are summed by PC.

  python -B -m tools.lab_copy_cost --export <built export> --emulator <dir> --bios <bin> [--ticks 300]
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402


def region(address):
    if 0x200000 <= address < 0x400000:
        return "rom"
    if 0x4000 <= address < 0x7000:
        return "ram"
    if 0x8000 <= address < 0xC000:
        return "video"
    return f"0x{address:X}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--export", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--ticks", type=int, default=300)
    p.add_argument("--inputs", type=Path)
    a = p.parse_args()
    rom = (a.export / "rom/game.ngc").read_bytes()
    build = json.loads((a.export / "rom/build-report.json").read_text(encoding="utf-8"))
    sym = {k[1:]: v["address"] for k, v in build["link_audit"]["public_symbols"].items()}
    sys.path.insert(0, str(a.emulator))
    from core import native
    m = native.NativeMachine(rom, bios=a.bios.read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    tick = verified_tick(rom, build)
    targets = [sym["ngc_sprites_commit"], sym["ngc_sprite_draw"]]
    inputs = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else []
    m.set_breakpoints([tick, *targets])
    # 1. Find the block copies the engine runs (one recorded pass after each target).
    blocks = {}
    frame = 0
    while frame < 30:
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise SystemExit(f"stopped: {stop.stop_status}")
        if stop.stop_pc == tick:
            frame += 1
            continue
        m.set_breakpoints([])
        _, recs = m.run(400, record=True)
        m.set_breakpoints([tick, *targets])
        for r in recs:
            raw = bytes(r.raw[:r.raw_len])
            if len(raw) >= 2 and raw[1] == 0x11 and raw[0] in (0x83, 0x93):   # LDIR (byte) / LDIRW (word)
                blocks[r.pc] = "ldirw" if raw[0] == 0x93 else "ldir"
    # 2. Break ON each copy: BC = elements, then run that one instruction, recorded.
    bc = native.REG_NAMES.index("xbc")
    xhl, xde = native.REG_NAMES.index("xhl"), native.REG_NAMES.index("xde")
    m.set_breakpoints([tick, *blocks])
    samples = {pc: [] for pc in blocks}
    frame = 0
    while frame < a.ticks:
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise SystemExit(f"stopped: {stop.stop_status}")
        if stop.stop_pc == tick:
            frame += 1
            held = sum(bits for s0, e0, bits in inputs if s0 <= frame < e0) & 0xFF
            m.write(0x00B0, bytes([held]))
            continue
        cpu = m.cpu()
        count = cpu.regs[bc] & 0xFFFF
        src, dst = cpu.regs[xhl], cpu.regs[xde]
        m.set_breakpoints([])
        before = m.run(1, record=False)[0]
        cycles, steps = before.total_cycles, 1
        # A repeated instruction may retire element by element: run until PC leaves it.
        while m.cpu().pc == stop.stop_pc and steps < 70000:
            cycles += m.run(1, record=False)[0].total_cycles
            steps += 1
        m.set_breakpoints([tick, *blocks])
        if count:
            samples[stop.stop_pc].append((count, cycles, region(src), region(dst)))
    out = []
    for pc, kind in sorted(blocks.items()):
        rows = samples[pc]
        if not rows:
            continue
        elements = sum(c for c, _, _, _ in rows)
        cycles = sum(y for _, y, _, _ in rows)
        out.append({"pc": f"0x{pc:06X}", "kind": kind, "calls": len(rows), "elements": elements,
                    "cycles": cycles, "cycles_per_element": round(cycles / elements, 2),
                    "from_to": sorted({f"{s0}->{d0}" for _, _, s0, d0 in rows}),
                    "elements_per_call": [min(c for c, _, _, _ in rows), max(c for c, _, _, _ in rows)]})
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
