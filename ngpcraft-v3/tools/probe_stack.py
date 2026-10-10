"""Instruction-by-instruction PC/stack gate for the current V3 ROM corpus.

Observed high-water mark only, not a static worst-case bound or hardware proof.
No ROM patch, RAM canary, instrumented runtime or changes to emulator sources.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.compiler import CompileError
from tools.probe_rom import verified_tick, positive_int
from tools.reproducible_build import write_report


def observe(export_dir, emulator, bios_path, instructions=1_000_000, minimum_margin=256):
    if instructions <= 0 or minimum_margin < 0:
        raise CompileError("positive instruction budget and nonnegative margin required")
    root=Path(export_dir)
    rom=(root/"rom/game.ngc").read_bytes()
    build_raw=(root/"rom/build-report.json").read_bytes()
    build=json.loads(build_raw)
    tick=verified_tick(rom,build)
    sections=build["link_audit"]["sections"]
    code=[(s["address"],s["address"]+s["bytes"]) for s in sections if s["section"]=="f_code"]
    if not code: raise CompileError("no linked executable sections")
    linked_end=max([0x4000]+[s["address"]+s["bytes"] for s in sections if s["region"]=="ram"])
    manifest_raw=(root/"export.json").read_bytes()
    manifest=json.loads(manifest_raw)
    reserved_end=linked_end
    scratch=None
    if "laboratory" in manifest and "scratch_ram" in manifest["laboratory"]:
        scratch=manifest["laboratory"]["scratch_ram"]
        address,size=scratch["address"],scratch["bytes"]
        if type(address) is not int or type(size) is not int or size <= 0 or not linked_end <= address < address+size <= 0x6C00:
            raise CompileError("invalid laboratory scratch allocation")
        reserved_end=max(reserved_end,address+size)
    bios=Path(bios_path).read_bytes()
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(Path(emulator).resolve()):
        raise CompileError("a different native binding is already loaded")
    m=native.NativeMachine(rom,bios=bios)
    m.set_timing_silicon(word_wait=10,bios_wait=8)
    m.reset(bios_handoff=True)
    lowest=0x6C00
    lowest_pc=None
    first_tick_sp=return_pc=None
    returns=0
    executed=0
    cycles=0
    failure=None
    bios_system_stack_samples=0
    for index in range(instructions+1):
        cpu=m.cpu()
        pc=int(cpu.pc)
        sp=int(cpu.regs[7])
        in_bios=0xFF0000 <= pc < 0x1000000
        if not in_bios and not any(start <= pc < end for start,end in code):
            failure={"reason":"pc-outside-linked-code", "pc":pc,"sp":sp,"after_instructions":executed};break
        if in_bios and 0x6C00 < sp <= 0x7000:
            bios_system_stack_samples+=1
        elif not reserved_end <= sp <= 0x6C00:
            failure={"reason":"stack-outside-free-user-ram", "pc":pc,"sp":sp,"after_instructions":executed};break
        else:
            if sp < lowest:
                lowest,lowest_pc=sp,pc
            if sp-reserved_end < minimum_margin:
                failure={"reason":"stack-margin", "pc":pc,"sp":sp,"after_instructions":executed};break
        if pc==tick and first_tick_sp is None:
            first_tick_sp=sp
            return_pc=int.from_bytes(m.read(sp,4),"little")
            if not any(start <= return_pc < end for start,end in code):
                failure={"reason":"invalid-tick-return", "pc":pc,"sp":sp,"after_instructions":executed};break
        if pc==return_pc:
            returns+=1
            if sp != first_tick_sp+4:
                failure={"reason":"tick-stack-not-restored", "pc":pc,"sp":sp,"after_instructions":executed};break
        if index==instructions: break
        summary,_=m.run(1,record=False)
        executed+=int(summary.executed)
        cycles+=int(summary.total_cycles)
        if summary.stop_status not in (native.STATUS_OK,native.STATUS_COUNT_REACHED):
            failure={"reason":"emulator-stop", "status":int(summary.stop_status), "pc":int(summary.stop_pc),
                     "sp":int(m.cpu().regs[7]), "after_instructions":executed};break
    completed=executed==instructions
    return {"check":"instruction-pc-stack", "passed":failure is None and completed and returns>0,
            "failure":failure,"instruction_budget":instructions,"executed":executed,"budget_completed":completed,
            "tick_entry_reached":first_tick_sp is not None,"tick_caller_returns":returns,
            "tick_entry_sp":first_tick_sp,"lowest_user_sp":lowest,"lowest_sp_pc":lowest_pc,
            "observed_user_stack_bytes":0x6C00-lowest,"linked_ram_end":linked_end,
            "highest_reserved_end":reserved_end,"observed_margin_bytes":lowest-reserved_end,
            "required_margin_bytes":minimum_margin,"bios_system_stack_samples":bios_system_stack_samples,
            "emulated_cycles_observed":cycles,"rom_sha256":hashlib.sha256(rom).hexdigest(),
            "build_report_sha256":hashlib.sha256(build_raw).hexdigest(),"manifest_sha256":hashlib.sha256(manifest_raw).hexdigest(),
            "bios_sha256":hashlib.sha256(bios).hexdigest(),
            "core_sha256":hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
            "binding_sha256":hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
            "silicon_timing":{"word_wait":10,"bios_wait":8},"boot":"handoff",
            "scratch_final_hex":m.read(scratch["address"],scratch["bytes"]).hex() if scratch else None,
            "scope":"PC and XSP sampled at every instruction boundary including boot/interrupts. Observed stack only, not worst-case proof; 256-byte default margin is an experiment setting, not a product reservation. No RAM-executed code permitted by this gate."}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("export_dir",type=Path)
    p.add_argument("--emulator",type=Path,required=True)
    p.add_argument("--bios",type=Path,required=True)
    p.add_argument("--instructions",type=positive_int,default=1_000_000)
    p.add_argument("--minimum-margin",type=int,default=256)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    try:
        if args.out.exists(): raise CompileError("report already exists; use new output")
        result=observe(args.export_dir,args.emulator,args.bios,args.instructions,args.minimum_margin)
        result["command"]=[sys.executable,"-B","-m","tools.probe_stack",*sys.argv[1:]]
        write_report(args.out,result)
        print(json.dumps({k:result[k] for k in ("passed","failure","executed","tick_caller_returns","observed_user_stack_bytes","observed_margin_bytes")},indent=2))
        return 0 if result["passed"] else 1
    except (OSError,ValueError,CompileError) as exc:
        print(str(exc),file=sys.stderr);return 2


if __name__=="__main__":
    raise SystemExit(main())
