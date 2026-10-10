"""Self cycles and instructions per function inside one tick module (docs/PLAN_MEGAMAN.md L3).

Instruction trace of the native core (silicon timing), from outside the ROM: each
instruction is billed to the closest public symbol at or below its address, so a
static function is billed to the public one before it (make it public in a
throw-away copy of the export to separate it). Complements tools/profile_tick.py
(inclusive time per tick module).

  python -B tools/profile_self.py <export> <emulator> <bios> <inputs.json> <ticks> <module symbol>
"""
import bisect
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402


def main():
    export, emulator, bios, inputs = map(Path, sys.argv[1:5])
    ticks, module = int(sys.argv[5]), sys.argv[6]
    rom = (export / "rom/game.ngc").read_bytes()
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    sym = build["link_audit"]["public_symbols"]
    code = sorted((v["address"], k) for k, v in sym.items() if v["section"] == "f_code")
    addrs = [a for a, _ in code]
    tick = verified_tick(rom, build)
    entry = sym[module]["address"]
    schedule = json.loads(inputs.read_text(encoding="utf-8"))
    sys.path.insert(0, str(emulator))
    from core import native
    m = native.NativeMachine(rom, bios=bios.read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")

    def run(points):
        m.set_breakpoints(points)
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise SystemExit("breakpoint not reached")
        return stop

    def owner(pc):
        if pc >= 0xFF0000:
            return "BIOS"
        i = bisect.bisect_right(addrs, pc) - 1
        return code[i][1] if i >= 0 else hex(pc)

    cyc, ins, size = collections.Counter(), collections.Counter(), collections.Counter()
    run([tick])
    for k in range(ticks):
        mask = 0
        for a, b, bits in schedule:
            if a <= k < b:
                mask |= bits
        m.write(0xB0, bytes([mask]))      # hardware port, as probe_gbvm
        run([entry])
        back = int.from_bytes(m.read(m.cpu().regs[7], 4), "little") & 0xFFFFFF
        m.set_breakpoints([])
        done = False
        while not done:
            _, records = m.run(4000, record=True)
            for r in records:
                if r.pc == back:
                    done = True
                    break
                if k >= 2:
                    o = owner(r.pc)
                    cyc[o] += r.cycles
                    ins[o] += 1
                    size[o] += r.raw_len
        run([tick])
    n = ticks - 2
    total = sum(cyc.values())
    print(f"{module}: {total // n} cycles/tick, {sum(ins.values()) // n} instructions/tick, "
          f"{total / max(1, sum(ins.values())):.1f} cycles/instruction")
    for o, c in cyc.most_common(15):
        print(f"  {o:34s} {c // n:6d} cyc/tick  {ins[o] // n:5d} instr/tick  "
              f"{c / ins[o]:.1f} cyc/instr  {size[o] / ins[o]:.1f} bytes/instr")


if __name__ == "__main__":
    main()
