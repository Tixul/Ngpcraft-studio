"""Observed height of every GBVM context stack, against the exported static bound.

The export's own ROM keeps the stride computed by ngpcraft/gbvm/stack_budget.py,
so a context that outgrew it would write into its neighbour without trace. The
measure therefore runs a COPY of the export rebuilt with the vendored stride
(VM_CONTEXT_STACK_SIZE 64, nothing else changed): each context keeps its own
64 words and the emulator's write log (PC-independent, no ROM patch) gives the
highest word each context wrote. The verdict compares it with the stride of the
original export. Observation of one run, not a proof of the static bound.

Boot as the other probes: BIOS handoff, noisy user RAM, silicon timing. The log
is armed after the first tick (vm_init clears the whole array). Buttons follow
source.json "input_schedule" at the game_time of each tick when present.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_rom import power_on_ram, verified_tick  # noqa: E402
from tools.gbvm_topdown import held_at  # noqa: E402

VENDORED = 64
DEFINE = re.compile(r"^#define (VM_CONTEXT_STACK_SIZE|VM_MAX_CONTEXTS|VM_HEAP_SIZE) (\d+)$", re.M)


def defines(export_dir):
    found = dict((k, int(v)) for k, v in DEFINE.findall((export_dir / "src/gbvm.c").read_text(encoding="utf-8")))
    if set(found) != {"VM_CONTEXT_STACK_SIZE", "VM_MAX_CONTEXTS", "VM_HEAP_SIZE"}:
        raise SystemExit("gbvm.c defines not found")
    return found


def widened(export_dir, out_dir, toolchain):
    """A fresh copy of the export, rebuilt with the vendored stride only."""
    from ngpcraft.toolchain import build_rom
    shutil.copytree(export_dir, out_dir, ignore=shutil.ignore_patterns("rom", "objects"))
    path = out_dir / "src/gbvm.c"
    text = path.read_text(encoding="utf-8")
    stride = defines(export_dir)["VM_CONTEXT_STACK_SIZE"]
    old, new = f"#define VM_CONTEXT_STACK_SIZE {stride}\n", f"#define VM_CONTEXT_STACK_SIZE {VENDORED}\n"
    if text.count(old) != 1:
        raise SystemExit("stride define not found once")
    temp = path.with_suffix(".tmp")
    temp.write_text(text.replace(old, new), encoding="utf-8")
    temp.replace(path)
    manifest = json.loads((out_dir / "export.json").read_text(encoding="utf-8"))
    manifest["sha256"]["src/gbvm.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
    temp = out_dir / "export.json.tmp"
    temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temp.replace(out_dir / "export.json")
    build_rom(out_dir, toolchain)
    return out_dir


def observe(export_dir, emulator, bios_path, ticks, schedule=None, frame_schedule=None):
    """schedule: source.json buttons by game_time; frame_schedule: inputs.json
    buttons by frame entry (game tick or modal commit), as tools/probe_gbvm.py."""
    rom = (export_dir / "rom/game.ngc").read_bytes()
    build = json.loads((export_dir / "rom/build-report.json").read_text(encoding="utf-8"))
    tick = verified_tick(rom, build)
    symbols = build["link_audit"]["public_symbols"]
    # A modal dialogue runs its own frames: their entry is ngc_ui_modal_commit.
    entries = [tick] + ([symbols["_ngc_ui_modal_commit"]["address"]]
                        if "_ngc_ui_modal_commit" in symbols else [])
    d = defines(export_dir)
    stride, count, heap = d["VM_CONTEXT_STACK_SIZE"], d["VM_MAX_CONTEXTS"], d["VM_HEAP_SIZE"]
    base = symbols["_script_memory"]["address"] + 2 * heap
    end = base + 2 * stride * count - 1
    game_time = symbols.get("_ngc_game_time", {}).get("address")
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    bios = Path(bios_path).read_bytes()
    machine = native.NativeMachine(rom, bios=bios)
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    ram = power_on_ram(machine, "noise")
    machine.write(0xA000, bytes(8192))

    def to_entry(budget):
        machine.set_breakpoints(entries)
        stop = machine.run(budget, record=False)[0]
        if stop.stop_status != 40:
            raise SystemExit("frame entry not reached")

    to_entry(3_000_000)
    machine.write(0x00B0, bytes([0]))
    high = [0] * count          # words written, from the base of each context
    writes = 0
    for index in range(ticks):
        if frame_schedule is not None:
            machine.write(0x00B0, bytes([held_at(frame_schedule, index)]))
        elif schedule is not None:
            now = int.from_bytes(machine.read(game_time, 2), "little") if game_time else index
            machine.write(0x00B0, bytes([held_at(schedule, now)]))
        machine.set_write_log(base, end)
        machine.set_breakpoints([])
        machine.run(1, record=False)            # leave the breakpoint
        to_entry(3_000_000)
        seen = machine.write_log_count()
        log = machine.write_log()
        if seen != len(log):
            raise SystemExit(f"write log lost {seen - len(log)} writes at tick {index}")
        writes += seen
        for rec in log:
            word = (rec.addr - base) >> 1
            k, offset = divmod(word, stride)
            high[k] = max(high[k], offset + 1)
    machine.set_write_log(1, 0)
    return {"ticks": ticks, "contexts": count, "stride_words": stride, "heap_words": heap,
            "stacks_address": base, "stack_writes": writes, "observed_words": high,
            "max_observed_words": max(high), "rom_sha256": hashlib.sha256(rom).hexdigest(),
            "emulator_core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            "bios_sha256": hashlib.sha256(bios).hexdigest(), **ram}


def measure(case, toolchain, emulator, bios, ticks, work):
    """Bound of the case's export vs. the height observed on its widened copy."""
    export = case / "export"
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    budget = manifest["gbvm"]["stack_budget"]
    source, inputs = case / "source.json", case / "inputs.json"
    schedule = json.loads(source.read_text(encoding="utf-8")).get("input_schedule") if source.exists() else None
    frame_schedule = json.loads(inputs.read_text(encoding="utf-8")) if inputs.exists() else None
    wide = widened(export, work, toolchain)
    result = observe(wide, emulator, bios, ticks, schedule, frame_schedule)
    result["inputs"] = "inputs.json" if frame_schedule else ("source.json" if schedule else None)
    result["static_bound_words"] = budget["words_per_context"]
    result["fallbacks"] = {k: v["reason"] for k, v in budget["scripts"].items() if v["method"] != "bytecode_cfg"}
    result["within_bound"] = result["max_observed_words"] <= budget["words_per_context"]
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("case", type=Path, help="directory holding export/ (and source.json if buttons)")
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--ticks", type=int, default=600)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        p.error("output must be new")
    args.out.mkdir(parents=True)
    result = measure(args.case, args.toolchain, args.emulator, args.bios, args.ticks, args.out / "wide-export")
    temp = args.out / "vm_stacks.json.tmp"
    temp.write_text(json.dumps(result, indent=1), encoding="utf-8")
    temp.replace(args.out / "vm_stacks.json")
    print(json.dumps({k: result[k] for k in ("static_bound_words", "max_observed_words", "within_bound",
                                             "observed_words", "stack_writes")}))
    if not result["within_bound"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
