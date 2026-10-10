"""S1: real GBVM scheduler/handlers with a eleven-op NGPC C decoder.

Disposable laboratory only. No actors, calls, generic native INVOKE, full bytecode support or
GB Studio project compatibility. Source GBVM is supplied externally read-only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from ngpcraft.compiler import analyze, CompileError
from ngpcraft.emitter import generate
from ngpcraft.toolchain import build_rom
from tools.probe_gbvm_layout import extract
from tools.probe_rom import verified_tick
from tools.reproducible_build import write_report, tool_fingerprints

OP = {"STOP": 0, "PUSH_CONST": 1, "POP": 2, "INVOKE": 13, "PUSH_VALUE": 17, "RESERVE": 18,
      "SET": 19, "SET_CONST": 20, "IDLE": 24, "LOCK": 37, "UNLOCK": 38}


def word(value):
    if not -32768 <= value <= 65535:
        raise CompileError("laboratory word out of range")
    return list((value & 65535).to_bytes(2, "big"))


def const(index, value):
    return [OP["SET_CONST"], *word(value), *word(index)]


def move(target, source):
    return [OP["SET"], *word(source), *word(target)]


def invoke_wait():
    # Reference macro: PARAMS hi/lo, N, ADDR hi/lo, BANK.
    # This lab resolves only bank=1/address=0x1234 to wait_frames.
    return [OP["INVOKE"], *word(-1), 1, *word(0x1234), 1]


def wait_fixtures(name):
    duration = int(name.split("_")[1]) if name.split("_")[1].isdigit() else 3
    locked = name == "wait_locked"
    busy = name == "wait_busy"
    twice = name == "wait_twice"
    if twice:
        duration = 1
    a = ([37] if locked else []) + const(0, 0x1111) + [1, *word(0xABCD), 1, *word(duration), *invoke_wait()]
    if twice:
        a += const(0, 0x3333) + [1, *word(1), *invoke_wait()]
    a += const(0, 0x2222) + move(1, -1) + [2, 1] + ([38] if locked else []) + [0]
    programs = [a]
    # Extra snapshot fields: stack depth A/B, pending native invocation A/B.
    pending = [0x1111, 0, 1 if locked else 0, 1, 0, 0, 2, 0, 1, 0]
    done = [0x2222, 0xABCD, 0, 0, 0x8000, 0, 0, 0, 0, 0]
    expected = [pending[:] for _ in range(duration)] + [done]
    if twice:
        second = pending[:]
        second[0] = 0x3333
        expected = [pending, second, done]
    if busy:
        programs.append(sum((const(1, i) for i in range(1, 19)), []) + [24, 0])
        first = pending[:]; first[1] = 18
        second = first[:]; second[5] = 0x8000
        done[5] = 0x8000
        # The extra BUSY pass invokes wait_frames twice during the first tick.
        expected = [first, second, done]
    if locked:
        programs.append(const(1, 0xBEEF) + [0])
        done[1] = 0xBEEF; done[5] = 0x8000
    return programs, expected


SCHEDULER_CASES = (("stack","none"),("locks","none"),("stop_locked","none"),
                   ("quantum","none"),("stack","endian"),("quantum","quantum"))
WAIT_CASES = tuple((name,"none") for name in ("wait_0","wait_1","wait_3","wait_255","wait_256",
                                             "wait_busy","wait_locked","wait_twice")) + (
    ("wait_1","wait_count"),("wait_1","wait_cleanup"),("wait_1","wait_rewind"))


def fixtures(name):
    if name.startswith("wait_"):
        return wait_fixtures(name)
    # Snapshot fields: global0, global1, lock state, runner status, handles A/B.
    if name == "stack":
        a = [1, *word(0x1234), 17, *word(-1), *move(0, -1), 2, 1, 18, 1,
             *const(-1, 0xABCD), *move(1, -1), 18, 255, 2, 1, 24,
             *const(0, 0x5678), 0]
        return [a], [[0x1234, 0xABCD, 0, 1, 0, 0], [0x5678, 0xABCD, 0, 0, 0x8000, 0]]
    if name == "locks":
        a = [37, 37, *const(0, 0x1234), 24, *const(0, 0x2345), 38, 24,
             *const(0, 0x3456), 38, 0]
        b = [*const(1, 0x4567), 24, *const(1, 0x5678), 0]
        return [a, b], [[0x1234, 0, 2, 1, 0, 0], [0x2345, 0, 1, 1, 0, 0],
                        [0x3456, 0x4567, 0, 1, 0x8000, 0], [0x3456, 0x5678, 0, 0, 0x8000, 0x8000]]
    if name == "stop_locked":
        return [[37, 24, 0], [*const(1, 0x4567), 0]], [[0, 0, 1, 1, 0, 0], [0, 0x4567, 0, 0, 0x8000, 0x8000]]
    if name == "quantum":
        a = sum((const(0, i) for i in range(1, 17)), []) + [24, *const(0, 17), 0]
        b = [*const(1, 0xBEEF), 24, *const(1, 0xCAFE), 0]
        return [a, b], [[16, 0xBEEF, 0, 1, 0, 0], [17, 0xCAFE, 0, 0, 0x8000, 0x8000]]
    raise CompileError("unknown scheduler fixture")


def function(source, name):
    # Calling-convention annotation: `VM_CALL` (gbvm main) or `OLDCALL BANKED` /
    # `OLDCALL NONBANKED` / `NONBANKED` (gbvm bd6f41c, pinned by GB Studio 4.3.2).
    annotation = r"(?:VM_CALL|OLDCALL BANKED|OLDCALL NONBANKED|NONBANKED)"
    match = re.search(r"^(?:void|UBYTE|UWORD) " + name + r"\([^\n]*\) " + annotation + r" \{", source, re.M)
    if not match:
        raise CompileError("unsupported reference function: " + name)
    end, depth = match.end(), 1
    while depth and end < len(source):
        if source[end] == "{": depth += 1
        elif source[end] == "}": depth -= 1
        end += 1
    if depth:
        raise CompileError("unclosed reference function: " + name)
    head = re.sub(" " + annotation + r" \{$", " {", source[match.start():match.end()])
    return "static " + head + source[match.end():end] + "\n"


def source(reference, scenario, negative):
    if negative not in ("none", "endian", "quantum", "wait_count", "wait_cleanup", "wait_rewind"):
        raise CompileError("unknown laboratory mutation")
    reference = Path(reference)
    raw = {name: (reference/name).read_bytes() for name in ("include/vm.h", "include/vm.i", "src/core/vm.c", "src/core/core.c")}
    opcodes = raw["include/vm.i"].decode("utf-8-sig")
    for name, value in OP.items():
        match = re.search(r"^OP_VM_" + name + r"\s*=\s*0x([0-9A-Fa-f]+)", opcodes, re.M)
        if not match or int(match[1], 16) != value:
            raise CompileError("reference opcode changed: " + name)
    ctx, capacities = extract(raw["include/vm.h"].decode("utf-8-sig"))
    vm = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw["src/core/vm.c"].decode("utf-8-sig"), flags=re.S)
    programs, expected = fixtures(scenario)
    out = "/* GBVM excerpts: MIT, Copyright (c) 2020 Toxa. See GBVM_LICENSE.txt. */\n#include \"game.h\"\n"
    out += "typedef unsigned char UBYTE; typedef unsigned char FASTUBYTE; typedef unsigned short UWORD; typedef signed short INT16; typedef signed char INT8;\n"
    out += ctx + "\n" + "".join("#define %s %d\n" % item for item in capacities.items())
    out += """#define TRUE 1
#define FALSE 0
#define CURRENT_BANK 0
#define SWITCH_ROM(bank) ((void)(bank))
#define EXCEPTION_CODE_NONE 0
#define SCRIPT_TERMINATED 0x8000u
#define RUNNER_DONE 0
#define RUNNER_IDLE 1
#define RUNNER_BUSY 2
#define RUNNER_EXCEPTION 3
"""
    out += "#define INSTRUCTIONS_PER_QUANT %d\n" % (15 if negative == "quantum" else 16)
    out += """static UWORD script_memory[VM_HEAP_SIZE + VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE];
static SCRIPT_CTX contexts[VM_MAX_CONTEXTS];
static SCRIPT_CTX *first_ctx, *free_ctxs, *old_executing_ctx, *executing_ctx;
static UBYTE vm_lock_state, vm_exception_code, lab_fault;
static UWORD handles[2];
"""
    for name in ("vm_push", "vm_pop", "vm_push_value", "vm_reserve", "vm_set", "vm_set_const", "vm_idle", "vm_lock", "vm_unlock"):
        out += function(vm, name)
    wait_handler = function(vm, "wait_frames")
    if negative == "wait_count":
        if wait_handler.count("stack_frame[0] + 1") != 1:
            raise CompileError("wait_frames count expression changed")
        wait_handler = wait_handler.replace("stack_frame[0] + 1", "stack_frame[0]")
    out += wait_handler
    invoke = function(vm, "vm_invoke")
    # C89: declare start before the stack_frame assignment; preserve the source expression.
    invoke = invoke.replace("    UWORD * stack_frame =", "    UBYTE start;\n    UWORD * stack_frame =")
    invoke = invoke.replace("    UBYTE start =", "    start =")
    rewind = "(INSTRUCTION_SIZE + sizeof(bank) + sizeof(fn) + sizeof(nparams) + sizeof(idx))"
    if invoke.count(rewind) != 1:
        raise CompileError("reference INVOKE rewind expression changed")
    invoke = invoke.replace(rewind, "9u" if negative == "wait_rewind" else "7u")
    if negative == "wait_cleanup":
        needle = "if (nparams) THIS->stack_ptr -= nparams;"
        if invoke.count(needle) != 1:
            raise CompileError("reference INVOKE cleanup changed")
        invoke = invoke.replace(needle, "/* deliberately missing parameter cleanup */")
    # Only wait_frames is callable. The decoder checks the encoded bank/address.
    out += "#define FAR_CALL_EX(addr, seg, typ, ctx, start, frame) wait_frames(ctx, start, frame)\n"
    out += invoke
    out += "static UWORD read_word(SCRIPT_CTX *ctx) { UWORD value; "
    out += ("value = (UWORD)ctx->PC[0] | ((UWORD)ctx->PC[1] << 8);" if negative == "endian" else
            "value = ((UWORD)ctx->PC[0] << 8) | (UWORD)ctx->PC[1];")
    out += "ctx->PC += 2; return value; }\n"
    out += """static UBYTE VM_STEP(SCRIPT_CTX *ctx)
{
    UBYTE op, count, bank; UWORD first, second;
    op = *ctx->PC;
    if (!op) return 0u;
    ++ctx->PC;
    switch (op) {
    case 1: vm_push(ctx, read_word(ctx)); break;
    case 2: (void)vm_pop(ctx, *ctx->PC++); break;
    case 13:
        first = read_word(ctx); count = *ctx->PC++;
        second = read_word(ctx); bank = *ctx->PC++;
        if (bank != 1u) { lab_fault = 1u; return 0u; }
        if (second != 0x1234u) { lab_fault = 1u; return 0u; }
        vm_invoke(ctx, bank, (UBYTE *)wait_frames, count, (INT16)first); break;
    case 17: vm_push_value(ctx, (INT16)read_word(ctx)); break;
    case 18: vm_reserve(ctx, (INT8)*ctx->PC++); break;
    case 19:
        first = read_word(ctx); second = read_word(ctx);
        vm_set(ctx, (INT16)second, (INT16)first); break;
    case 20:
        first = read_word(ctx); second = read_word(ctx);
        vm_set_const(ctx, (INT16)second, first); break;
    case 24: vm_idle(ctx); break;
    case 37: vm_lock(ctx); break;
    case 38: vm_unlock(ctx); break;
    default: lab_fault = 1u; return 0u;
    }
    return 1u;
}
"""
    out += function(vm, "script_runner_update")
    for i, program in enumerate(programs):
        out += "static const UBYTE program%d[] = {%s};\n" % (i, ",".join(str(x) + "u" for x in program))
    out += """void ngc_game_init(void)
{
    unsigned short i; SCRIPT_CTX *ctx;
    for (i = 0u; i < VM_HEAP_SIZE + VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE; ++i) script_memory[i] = 0u;
    for (i = 0u; i < VM_MAX_CONTEXTS; ++i) {
        ctx = &contexts[i];
        ctx->PC = 0; ctx->bank = 0u; ctx->next = 0; ctx->update_fn = 0; ctx->update_fn_bank = 0u;
        ctx->stack_ptr = script_memory + VM_HEAP_SIZE + i * VM_CONTEXT_STACK_SIZE;
        ctx->base_addr = ctx->stack_ptr; ctx->ID = (UBYTE)(i + 1u); ctx->hthread = 0;
        ctx->terminated = 0u; ctx->waitable = 0u; ctx->lock_count = 0u; ctx->flags = 0u;
    }
    handles[0] = 0u; handles[1] = 0u;
    vm_lock_state = 0u; vm_exception_code = 0u; lab_fault = 0u;
    first_ctx = &contexts[0]; free_ctxs = 0; old_executing_ctx = 0; executing_ctx = 0;
"""
    for i in range(len(programs)):
        out += "    contexts[%d].PC = program%d; contexts[%d].hthread = &handles[%d];\n" % (i,i,i,i)
        if i + 1 < len(programs):
            out += "    contexts[%d].next = &contexts[%d];\n" % (i,i+1)
    out += """}
void ngc_game_tick(void)
{
    UBYTE status; volatile UWORD *snapshot;
    do { status = script_runner_update(); } while (status == RUNNER_BUSY);
    snapshot = (volatile UWORD *)0x6000;
    snapshot[0] = script_memory[0]; snapshot[1] = script_memory[1];
    snapshot[2] = vm_lock_state; snapshot[3] = status;
    snapshot[4] = handles[0]; snapshot[5] = handles[1];
    snapshot[6] = (UWORD)(contexts[0].stack_ptr - contexts[0].base_addr);
    snapshot[7] = (UWORD)(contexts[1].stack_ptr - contexts[1].base_addr);
    snapshot[8] = (contexts[0].update_fn != 0);
    snapshot[9] = (contexts[1].update_fn != 0); snapshot[10] = lab_fault;
}
"""
    return out, expected, {p: hashlib.sha256(data).hexdigest() for p, data in raw.items()}


def build(dest, reference, toolchain, scenario, negative="none"):
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("laboratory destination must be new or empty")
    code, expected, fingerprints = source(reference, scenario, negative)
    files = generate(analyze({"schema": "ngpcraft-ir/0.1", "start": "lab", "scenes": [{"id": "lab"}]}), "rom")
    files["src/game.c"] = code
    files["GBVM_LICENSE.txt"] = (Path(reference)/"LICENSE").read_text(encoding="utf-8")
    manifest = json.loads(files.pop("export.json"))
    manifest["laboratory"] = {"name": "s1-gbvm-scheduler", "scenario": scenario, "negative_control": negative,
                              "reference_sha256": fingerprints, "expected_snapshots": expected,
                              "scratch_ram": {"address": 0x6000, "bytes": 22}}
    manifest["guarantee"] = "Disposable partial GBVM scheduler port. Only eleven opcodes and controlled fixtures; not an author-project export."
    manifest["sha256"] = {name: hashlib.sha256(s.encode()).hexdigest() for name,s in files.items()}
    files["export.json"] = json.dumps(manifest, indent=2) + "\n"
    for name,text in files.items():
        path = dest/name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
            f.write(text); temporary = f.name
        os.replace(temporary,path)
    result = build_rom(dest,toolchain)
    for section in result["link_audit"]["sections"]:
        if section["region"] == "ram" and section["address"] + section["bytes"] > 0x6000:
            raise CompileError("lab state overlaps observation scratch memory")
    return expected


def observe(dest, emulator, bios_path, expected):
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(Path(emulator).resolve()):
        raise CompileError("a different emulator binding is already loaded")
    dest=Path(dest)
    rom=(dest/"rom/game.ngc").read_bytes()
    report=json.loads((dest/"rom/build-report.json").read_text())
    tick=verified_tick(rom,report)
    bios=Path(bios_path).read_bytes()
    machine=native.NativeMachine(rom,bios=bios)
    machine.set_timing_silicon(word_wait=10,bios_wait=8)
    machine.reset(bios_handoff=True)
    machine.set_breakpoints([tick])
    s,_=machine.run(2_000_000,record=False)
    if s.stop_status != 40: raise CompileError("lab entry not reached")
    # At function entry, the TLCS900 CALL has pushed a four-byte return PC.
    # Break there instead of at tick: cc900 may branch to tick on RUNNER_BUSY.
    entry_sp = int(machine.cpu().regs[7])
    if not 0x4000 <= entry_sp <= 0x6BFC:
        raise CompileError("tick entry stack outside game RAM")
    return_pc = int.from_bytes(machine.read(entry_sp, 4), "little")
    if not 0x200040 <= return_pc < 0x200000 + len(rom):
        raise CompileError("tick caller return address outside ROM")
    rows=[]
    for wanted in expected:
        machine.set_breakpoints([return_pc])
        s,_=machine.run(200_000,record=False)
        if s.stop_status != 40 or machine.cpu().pc != return_pc:
            raise CompileError("lab iteration did not return to caller")
        if int(machine.cpu().regs[7]) != entry_sp + 4:
            raise CompileError("tick did not restore caller stack")
        data=machine.read(0x6000,22)
        values=[int.from_bytes(data[i:i+2],"little") for i in range(0,22,2)]
        selected = values if len(wanted) == 10 else values[:6] + [values[10]]
        rows.append({"expected": wanted+[0], "observed": selected, "full_snapshot": values})
        machine.set_breakpoints([tick])
        s,_=machine.run(200_000,record=False)
        if s.stop_status != 40 or machine.cpu().pc != tick:
            raise CompileError("next tick entry not reached")
        if int(machine.cpu().regs[7]) != entry_sp:
            raise CompileError("tick entry stack changed")
    return {"correct": all(r["expected"] == r["observed"] for r in rows), "snapshots": rows,
            "rom_sha256": hashlib.sha256(rom).hexdigest(), "rom_bytes":len(rom),
            "linked_static_ram_bytes": report["link_audit"]["static_ram_bytes"], "scratch_ram_bytes":22, "observation_boundary":"caller-return",
            "caller_return_pc":return_pc, "tick_entry_sp":entry_sp,
            "bios_sha256":hashlib.sha256(bios).hexdigest(),
            "core_sha256":hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            "binding_sha256":hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
            "silicon_timing":{"word_wait":10,"bios_wait":8}, "boot":"handoff",
            "scope":"Eleven-op partial scheduler port, RAM observations per game tick. No performance, full GBVM or console proof."}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gbvm",type=Path,required=True)
    p.add_argument("--toolchain",type=Path)
    p.add_argument("--emulator",type=Path,required=True)
    p.add_argument("--bios",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--suite", choices=("all", "scheduler", "waits"), default="all")
    args=p.parse_args()
    try:
        if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
            raise CompileError("output must be new or empty")
        tools_before=tool_fingerprints(args.toolchain)
        results=[]
        cases = SCHEDULER_CASES if args.suite == "scheduler" else WAIT_CASES if args.suite == "waits" else SCHEDULER_CASES + WAIT_CASES
        for name,negative in cases:
            dest=args.out/(name+"-"+negative)
            expected=build(dest,args.gbvm,args.toolchain,name,negative)
            result=observe(dest,args.emulator,args.bios,expected)
            result.update({"scenario":name,"negative_control":negative,"expected_success":negative=="none"})
            results.append(result)
            print(name,negative,"matches oracle:",result["correct"])
        report={"results":results,"tool_sha256":tools_before,"toolchain_unchanged":tools_before==tool_fingerprints(args.toolchain),
                "command":[sys.executable,"-B","-m","tools.lab_gbvm_scheduler",*sys.argv[1:]]}
        write_report(args.out/"scheduler.json",report)
        return 0 if all(r["correct"]==r["expected_success"] for r in results) and report["toolchain_unchanged"] else 1
    except (OSError,ValueError,CompileError) as exc:
        print(str(exc),file=sys.stderr);return 2


if __name__=="__main__":
    raise SystemExit(main())
