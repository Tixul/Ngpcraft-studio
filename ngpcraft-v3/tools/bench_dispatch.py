"""S1 synthetic dispatch benchmark. Not a GBVM implementation or engine verdict.

Three free-running ROMs share bootstrap/input/output; all timing is external.
Each program uses ten operations once: LOAD ADD SUB XOR AND OR SHL SHR NOT STORE.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile

from ngpcraft.compiler import analyze, CompileError
from ngpcraft.emitter import generate
from ngpcraft.toolchain import build_rom
from tools.probe_rom import verified_tick, positive_int
from tools.reproducible_build import write_report, tool_fingerprints


OPERATIONS = (
    ("load", 0, "value = *(volatile unsigned short *)0x5000;"),
    ("add", 257, "value = (unsigned short)(value + operand);"),
    ("sub", 19, "value = (unsigned short)(value - operand);"),
    ("xor", 42330, "value ^= operand;"),
    ("and", 32767, "value &= operand;"),
    ("or", 4096, "value |= operand;"),
    ("shl", 1, "value = (unsigned short)(value << operand);"),
    ("shr", 2, "value >>= operand;"),
    ("not", 0, "value = (unsigned short)~value;"),
    ("store", 0, "*(volatile unsigned short *)0x5002 = value; return;"),
)


def expected(seed):
    # Independent oracle, not evaluated from generated C.
    value = (seed + 257 - 19) & 65535
    value = ((value ^ 42330) & 32767) | 4096
    return (~(((value << 1) & 65535) >> 2)) & 65535


def source(mode, corrupt=False):
    if mode not in ("specialized", "interpreted", "baseline"):
        raise CompileError("unknown laboratory variant")
    head = '#include "game.h"\n'
    init = "void ngc_game_init(void) { *(volatile unsigned short *)0x5000 = 0u; *(volatile unsigned short *)0x5002 = 0u; }\n"
    if mode == "interpreted":
        rows = ", ".join("{%du, %du}" % (i, operand) for i, (_, operand, _) in enumerate(OPERATIONS))
        head += "typedef struct { unsigned short opcode; unsigned short operand; } Instruction;\n"
        head += "static const Instruction program[] = {" + rows + "};\n"
        body = "unsigned short index; unsigned short value; unsigned short operand;\nindex = 0u; value = 0u;\nfor (;;) {\noperand = program[index].operand;\nswitch (program[index].opcode) {\n"
        for i, (_, _, instruction) in enumerate(OPERATIONS):
            body += "case %du: %s break;\n" % (i, instruction)
        body += "default: *(volatile unsigned short *)0x5002 = 0u; return;\n}\n++index;\n}\n"
    elif mode == "specialized":
        body = "unsigned short value;\n"
        for name, operand, instruction in OPERATIONS:
            if corrupt and name == "add":
                operand += 1
            body += instruction.replace("operand", str(operand) + "u") + "\n"
    else:
        body = "*(volatile unsigned short *)0x5002 = *(volatile unsigned short *)0x5000;\n"
    return head + init + "void ngc_game_tick(void) {\n" + body + "}\n"


def build(dest, toolchain, mode, corrupt=False):
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("benchmark destination must be new or empty")
    files = generate(analyze({"schema": "ngpcraft-ir/0.1", "start": "lab", "scenes": [{"id": "lab"}]}), "rom")
    files["src/game.c"] = source(mode, corrupt)
    wait = "        while (previous == ngc_frame) { }"
    if files["src/startup.c"].count(wait) != 1:
        raise CompileError("bootstrap changed: review free-loop transformation")
    files["src/startup.c"] = files["src/startup.c"].replace(wait, "        /* S1 laboratory: free loop, no vsync wait. */")
    manifest = json.loads(files.pop("export.json"))
    manifest["laboratory"] = {"name": "s1-synthetic-dispatch", "mode": mode, "corrupt": corrupt,
                              "scratch_ram": {"address": 0x5000, "bytes": 4}, "free_loop": True}
    manifest["guarantee"] = "Disposable synthetic laboratory, no GBVM compatibility claim or game export."
    manifest["sha256"] = {p: hashlib.sha256(s.encode()).hexdigest() for p, s in files.items()}
    files["export.json"] = json.dumps(manifest, indent=2) + "\n"
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
            f.write(text); temporary = f.name
        os.replace(temporary, path)
    return build_rom(dest, toolchain)


def observe(dest, emulator, bios_path, mode, samples=128):
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(Path(emulator).resolve()):
        raise CompileError("a different emulator binding is already loaded")
    dest = Path(dest)
    rom = (dest / "rom/game.ngc").read_bytes()
    report = json.loads((dest / "rom/build-report.json").read_text())
    tick = verified_tick(rom, report)
    bios = Path(bios_path).read_bytes()
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    machine.set_breakpoints([tick])
    initial, _ = machine.run(2_000_000, record=False)
    if initial.stop_status != 40:
        raise CompileError("benchmark never reached tick")
    rows = []
    seeds = [0, 1, 65535, 32767, 32768, 0x1357, 0xFFFF - 257]
    for i in range(samples):
        seed = seeds[i] if i < len(seeds) else (i * 9973 + 17) & 65535
        machine.write(0x5000, seed.to_bytes(2, "little"))
        # Step ONCE off the breakpoint; include its cycles in the measured interval.
        step, _ = machine.run(1, record=False)
        summary, _ = machine.run(100_000, record=False)
        if summary.stop_status != 40 or machine.cpu().pc != tick:
            raise CompileError("benchmark did not complete an iteration")
        actual = int.from_bytes(machine.read(0x5002, 2), "little")
        wanted = seed if mode == "baseline" else expected(seed)
        # core.cpp accumulates Summary cycles per run, not since machine reset.
        cycles = int(step.total_cycles) + int(summary.total_cycles)
        if cycles <= 0:
            raise CompileError("core returned a non-positive cycle interval")
        rows.append({"seed": seed, "expected": wanted, "actual": actual, "cycles": cycles})
    costs = [row["cycles"] for row in rows]
    return {"mode": mode, "correct": all(r["expected"] == r["actual"] for r in rows),
            "samples": rows, "cycles_per_iteration": {"min": min(costs), "median": statistics.median(costs),
                                                      "mean": statistics.mean(costs), "max": max(costs)},
            "rom_sha256": hashlib.sha256(rom).hexdigest(), "rom_bytes": len(rom),
            "static_ram_bytes": report["link_audit"]["static_ram_bytes"], "scratch_ram_bytes": 4,
            "bios_sha256": hashlib.sha256(bios).hexdigest(),
            "core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            "binding_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
            "silicon_timing": {"word_wait": 10, "bios_wait": 8}, "boot": "handoff",
            "tick_address": tick, "scope": "Tick-entry to next tick-entry in free loop, including shared bootstrap overhead and interrupts; not isolated opcode latency."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--samples", type=positive_int, default=128)
    args = p.parse_args()
    try:
        if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
            raise CompileError("benchmark output must be new or empty")
        fingerprints = tool_fingerprints(args.toolchain)
        results = []
        for mode in ("baseline", "specialized", "interpreted"):
            dest = args.out / mode
            build(dest, args.toolchain, mode)
            results.append(observe(dest, args.emulator, args.bios, mode, args.samples))
        report = {"experiment": "synthetic-ten-op-dispatch", "results": results,
                  "tool_sha256": fingerprints, "toolchain_unchanged": fingerprints == tool_fingerprints(args.toolchain),
                  "command": [sys.executable, "-B", "-m", "tools.bench_dispatch", *sys.argv[1:]],
                  "scope": "Synthetic LOAD/STORE + unsigned16 arithmetic. No GBVM bytecode, scheduler, waits, actors or physics. D-2 remains open."}
        write_report(args.out / "benchmark.json", report)
        for r in results:
            print(r["mode"], "correct=" + str(r["correct"]), r["cycles_per_iteration"], "ROM", r["rom_bytes"])
        return 0 if all(r["correct"] for r in results) and report["toolchain_unchanged"] else 1
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
