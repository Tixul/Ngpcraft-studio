"""S1: cost of the ported GBVM scheduler per waiting script context per frame.

In GBVM a moving actor is a script context that re-executes one waitable
instruction per frame (VM_ACTOR_MOVE_TO, see gbvm/src/core/vm_actor.c). The
per-frame VM overhead of such an actor is therefore: one scheduler visit + one
decoded instruction that sets `waitable`. This bench measures exactly that
with VM_IDLE, using the real GBVM handlers and script_runner_update extracted by
tools/lab_gbvm_scheduler.py (MIT, (c) 2020 Toxa), N = 1..12 contexts, free loop,
silicon timing. The movement arithmetic itself is not included (it is C in
every option and is covered by docs/S1_ACTORS.md).
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
from tools import lab_gbvm_scheduler as lab
from tools.bench_actors import FRAME_CYCLES, slope
from tools.probe_rom import verified_tick, positive_int
from tools.reproducible_build import write_report, tool_fingerprints

IDLE, STOP, SCRIPT_LENGTH = 24, 0, 200


def gbvm_source(reference, contexts):
    original = lab.fixtures

    def fixtures(name):
        if name == "bench":
            return [[IDLE] * SCRIPT_LENGTH + [STOP] for _ in range(contexts)], []
        return original(name)

    lab.fixtures = fixtures
    try:
        code, _, fingerprints = lab.source(reference, "bench", "none")
    finally:
        lab.fixtures = original
    needle = "static UWORD handles[2];"
    if code.count(needle) != 1:
        raise CompileError("scheduler laboratory source changed: review the bench")
    return code.replace(needle, "static UWORD handles[VM_MAX_CONTEXTS];"), fingerprints


def build(dest, reference, toolchain, contexts):
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("benchmark destination must be new or empty")
    code, fingerprints = gbvm_source(reference, contexts)
    files = generate(analyze({"schema": "ngpcraft-ir/0.1", "start": "lab", "scenes": [{"id": "lab"}]}), "rom")
    files["src/game.c"] = code
    files["GBVM_LICENSE.txt"] = (Path(reference) / "LICENSE").read_text(encoding="utf-8")
    wait = "        while (previous == ngc_frame) { }"
    if files["src/startup.c"].count(wait) != 1:
        raise CompileError("bootstrap changed: review free-loop transformation")
    files["src/startup.c"] = files["src/startup.c"].replace(wait, "        /* S1 laboratory: free loop, no vsync wait. */")
    manifest = json.loads(files.pop("export.json"))
    manifest["laboratory"] = {"name": "s1-vm-idle", "contexts": contexts, "reference_sha256": fingerprints,
                              "free_loop": True, "scratch_ram": {"address": 0x6000, "bytes": 22}}
    manifest["guarantee"] = "Disposable benchmark with MIT GBVM excerpts; not an author-project export."
    manifest["sha256"] = {p: hashlib.sha256(s.encode()).hexdigest() for p, s in files.items()}
    files["export.json"] = json.dumps(manifest, indent=2) + "\n"
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
            f.write(text)
            tmp = f.name
        os.replace(tmp, path)
    return build_rom(dest, toolchain)


def observe(dest, emulator, bios_path, ticks):
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    dest = Path(dest)
    rom = (dest / "rom/game.ngc").read_bytes()
    report = json.loads((dest / "rom/build-report.json").read_text())
    tick = verified_tick(rom, report)
    m = native.NativeMachine(rom, bios=Path(bios_path).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    m.set_breakpoints([tick])
    first, _ = m.run(3_000_000, record=False)
    if first.stop_status != 40:
        raise CompileError("never reached tick")
    costs, faults = [], []
    for t in range(ticks):
        step, _ = m.run(1, record=False)
        summary, _ = m.run(2_000_000, record=False)
        if summary.stop_status != 40 or m.cpu().pc != tick:
            raise CompileError("tick did not complete")
        costs.append(int(step.total_cycles) + int(summary.total_cycles))
        snap = m.read(0x6000, 22)
        status = int.from_bytes(snap[6:8], "little")
        fault = int.from_bytes(snap[20:22], "little")
        # Measured ticks follow the one reached above: every context has already
        # idled once, so each tick must end IDLE (1) with no decoder fault.
        if t < SCRIPT_LENGTH - 2 and (status != 1 or fault):
            faults.append({"tick": t, "status": status, "fault": fault})
    return {"cycles": {"min": min(costs), "median": statistics.median(costs), "max": max(costs)},
            "frame_fraction_median": statistics.median(costs) / FRAME_CYCLES,
            "correct": not faults, "faults": faults[:5], "rom_bytes": len(rom),
            "rom_sha256": hashlib.sha256(rom).hexdigest(),
            "static_ram_bytes": report["link_audit"]["static_ram_bytes"],
            "core_sha256": hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--gbvm", type=Path, required=True)
    p.add_argument("--toolchain", type=Path)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--counts", default="1,4,8,12,16")
    p.add_argument("--ticks", type=positive_int, default=96)
    a = p.parse_args()
    try:
        if a.out.exists() and (not a.out.is_dir() or any(a.out.iterdir())):
            raise CompileError("benchmark output must be new or empty")
        fingerprints = tool_fingerprints(a.toolchain)
        results = []
        for n in (int(c) for c in a.counts.split(",")):
            dest = a.out / f"contexts-{n}"
            build(dest, a.gbvm, a.toolchain, n)
            r = observe(dest, a.emulator, a.bios, a.ticks)
            r["contexts"] = n
            results.append(r)
            print(n, "correct=%s" % r["correct"], "median", r["cycles"]["median"],
                  "frame %.1f%%" % (100 * r["frame_fraction_median"]), "RAM", r["static_ram_bytes"])
        per_context = slope([(r["contexts"], r["cycles"]["median"]) for r in results])
        report = {"experiment": "s1-gbvm-idle-context-cost", "frame_cycles": FRAME_CYCLES,
                  "results": results, "cycles_per_waiting_context": per_context,
                  "tool_sha256": fingerprints, "toolchain_unchanged": fingerprints == tool_fingerprints(a.toolchain),
                  "command": [sys.executable, "-B", "-m", "tools.bench_vm_idle", *sys.argv[1:]],
                  "scope": "Ported GBVM scheduler + VM_IDLE per context per frame, C decoder; excludes the "
                           "movement arithmetic and the decoding of MOVE_TO arguments."}
        write_report(a.out / "benchmark.json", report)
        print("cycles per waiting context:", round(per_context, 1))
        return 0 if all(r["correct"] for r in results) and report["toolchain_unchanged"] else 1
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
