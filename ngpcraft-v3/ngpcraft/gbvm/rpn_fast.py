"""vm_rpn in assembly, its common cases with the stack in registers (docs/ETUDE_CADENCE.md §6.6,
ngpc.engine_speedups).

Measured (MegaX, ETUDE_CADENCE §3 ter): a VM_RPN block costs 3 300 to 3 650 cycles, mostly
fixed (80 % of the expressions have fewer than 5 elements). cc900 keeps the interpreter's pc,
sp and ARGS in stack memory and reloads and stores them around every element; the work of an
element is a few instructions.

vm_rpn becomes an assembly loop with pc in XIX, sp in XIY, ARGS in XIZ (callee-saved: pushed)
and script_memory in XDE. It runs the cases of FAST that the game uses; STOP (0) stores pc and
sp and returns, as the C does. On any other byte the loop hands over to the original C
(renamed ngc_rpn_c, given ARGS instead of recomputing it from sp): pc back on that byte, pc
and sp stored in the context, the C finishes the expression exactly as before (division,
modulo, shifts, atan2, abs, isqrt, random, REF_MEM_IND, and every unknown byte, which it
ends on as before). The C is otherwise the vendored function with the same NGPC
replacements.

Same writes as the C, in the same order where it matters (a REF_SET's target address is
computed before its value is popped; a memory store calls ngc_memory before popping; an
unknown memory type stores nothing and, for REF_MEM, still pushes the slot unchanged).
Words are read byte by byte as NGC_LE16 does (no word read at an odd ROM address).

Assembly forms: only forms cc900 itself emits (ASM_FORMS, seen in cc900 -S -O3 of the
MegaX v24 engine; registers normalised); check_forms refuses any other line. No
pre-decrement and no djnz (cc900 emits neither), no shift by A (the shift cases stay C).
"""
import re

from ..compiler import CompileError

# Cases run in assembly (vm.h VM_OP_*); the others go to the C.
FAST = ("VM_OP_REF", "VM_OP_INT16", "VM_OP_INT8", "VM_OP_REF_SET", "VM_OP_REF_IND", "VM_OP_REF_SET_IND",
        "VM_OP_REF_MEM", "VM_OP_REF_MEM_SET",
        "VM_OP_ADD", "VM_OP_SUB", "VM_OP_EQ", "VM_OP_NE", "VM_OP_LT", "VM_OP_LE", "VM_OP_GT", "VM_OP_GE",
        "VM_OP_AND", "VM_OP_OR", "VM_OP_NOT", "VM_OP_B_AND", "VM_OP_B_OR", "VM_OP_B_XOR",
        "VM_OP_MIN", "VM_OP_MAX", "VM_OP_MUL", "VM_OP_B_NOT", "VM_OP_NEG")
MEMORY_TYPES = {"VM_OP_MEM_I8": "'i'", "VM_OP_MEM_U8": "'u'", "VM_OP_MEM_I16": "'I'"}
# SCRIPT_CTX as cc900 lays it out: PC at 0, stack_ptr at 0x10 (4-byte pointers, words aligned).
CONTEXT = """typedef struct SCRIPT_CTX {
    const UBYTE * PC;
    UBYTE bank;

    struct SCRIPT_CTX * next;

    void * update_fn;
    UBYTE update_fn_bank;

    UWORD * stack_ptr;"""

C_HEAD = "static void vm_rpn(SCRIPT_CTX * THIS) {\n"
C_LOCALS = "    INT16 * A, * B, * ARGS;\n"
C_ARGS = "    ARGS = sp;\n"

# Each form seen in cc900's output for the engine (where), registers normalised.
ASM_FORMS = {
    r"push xiz": "push XIZ (prologues)",
    r"pop xiz": "pop XIZ (epilogues)",
    r"push (xix|xiy|xiz|xhl|wa)": "push XIX / push WA (argument pushes, vm_step)",
    r"pop (xix|xiy)": "pop XIX (ngc_frame_copy...)",
    r"ld xhl, \(xsp\+0x8\)": "ld XWA,(XSP+0x8) (vm_rpn)",
    r"ld xix, \(xhl\)": "ld XWA,(XBC) (vm_rpn)",
    r"ld xiy, \(xhl\+0x10\)": "ld XWA,(XBC+0x10) (vm_rpn)",
    r"ld \(xhl\), xix": "ld (XIX),XWA (gbvm)",
    r"ld \(xhl\+0x10\), xiy": "ld (XWA+0x10),XBC (vm_rpn)",
    r"ld xiz, xiy": "ld XIX,XIY (gbvm)",
    r"lda xde, _script_memory": "lda XIX,S50004 / ld XBC,_script_memory (vm_rpn)",
    r"lda xsp, xsp\+0x8": "lda XSP,XSP+0x8 (epilogues)",
    r"ld (a|w|c), \(xix\+:1\)": "ld C,(XWA+:1) / ld A,(XBC+:1) (vm_rpn, gbvm)",
    r"ld \(xiy\+:2\), wa": "ld (XIZ+:2),WA (gbvm)",
    r"ld \(xiy\), wa": "ld (XIY),WA (gbvm)",
    r"ld wa, \(xwa\)": "ld WA,(XWA) (vm_rpn)",
    r"ld (wa|bc), \(xiy-0x[24]\)": "ld WA,(XIX-0x4) / ld WA,(XIX-0x2) (gbvm)",
    r"ld \(xiy-0x[24]\), wa": "ld (XBC-0x4),WA / ld (XBC-0x2),WA (gbvm)",
    r"ld \(xwa\), bc": "ld (XWA),BC (vm_rpn)",
    r"ld \(xhl\), (wa|a)": "ld (XHL),WA / ld (XHL),A (vm_rpn)",
    r"ld (a|wa), \(xhl\)": "ld C,(XHL) / ld BC,(XHL) (vm_rpn)",
    r"ld wa, 0x[01]": "ld WA,0x0 / ld WA,0x1 (vm_rpn)",
    r"exts (wa|xwa)": "exts WA / exts XWA (vm_rpn)",
    r"extz wa": "extz WA (vm_rpn)",
    r"add xwa, (xwa|xde|xiz)": "add XWA,XWA / add XBC,XWA (vm_rpn)",
    r"(add|sub|and|or|xor) wa, \(xiy-0x2\)": "add BC,(XIZ) / and WA,(XDE)... (word ALU, memory operand)",
    r"cp wa, \(xiy-0x2\)": "cp WA,(XIZ) (vm_rpn)",
    r"cp wa, 0x0": "cp BC,0x0 (vm_rpn)",
    r"cpw \(xiy-0x[24]\), 0x0": "cpw (XSP+0x4),0x0 / cpw (XIZ),0x0 (vm_rpn)",
    r"cp (a|c), 0x[0-9a-f]+": "cp C,0x49 / cp A,0xff (vm_rpn, actors_render)",
    r"scc (eq|ne|lt|le|gt|ge), wa": "scc eq,BC / scc lt,BC... (vm_rpn)",
    r"muls xwa, bc": "muls XWA,BC (vm_rpn)",
    r"cpl wa": "cpl WA (gbvm)",
    r"neg wa": "neg WA (gbvm)",
    r"inc 0x2, (xiy|xsp)": "inc 0x2,XSP (vm_rpn)",
    r"dec 0x2, xiy": "dec 0x4,XWA (vm_rpn)",
    r"dec 0x1, xix": "dec 0x1,XIX (gbvm)",
    r"cal _(ngc_memory|ngc_rpn_c)": "cal _ngc_memory (vm_rpn)",
    r"j (eq|ne|lt|gt|m), \w+": "j eq,L377 / j m,L426 (vm_rpn)",
    r"j \w+": "j L369 (vm_rpn)",
}


def check_forms(lines):
    """Every assembly line is a label or one of ASM_FORMS (module docstring)."""
    for line in lines:
        if re.fullmatch(r"\w+:", line):
            continue
        if not any(re.fullmatch(form, line) for form in ASM_FORMS):
            raise CompileError(f"vm_rpn: assembly form {line!r} not in ASM_FORMS")


def _values(c_defines):
    values = {}
    for name in FAST + ("VM_OP_STOP",):
        value = c_defines.get(name)
        if value is None or not re.fullmatch(r"-?\d+", value):
            raise CompileError(f"vm.h: {name} is not a number")
        values[name] = int(value)
    for name, char in MEMORY_TYPES.items():
        if c_defines.get(name) != char:
            raise CompileError(f"vm.h: {name} changed")
    if values["VM_OP_STOP"] != 0 or any((values[n] < 0) != (n.startswith("VM_OP_REF") or n.startswith("VM_OP_INT"))
                                        for n in FAST):
        raise CompileError("vm.h: RPN operator signs changed")
    return values


def _read_index():
    # idx = NGC_LE16(pc); pc += 2
    return ["ld a, (xix+:1)", "ld w, (xix+:1)"]


def _address(tag):
    # (idx < 0) ? ARGS + idx : script_memory + idx, from WA into XWA.
    return ["exts xwa", "add xwa, xwa", f"j m, {tag}_a", "add xwa, xde", f"j {tag}_d",
            f"{tag}_a:", "add xwa, xiz", f"{tag}_d:"]


def _memory():
    # ngc_memory(WA) -> XHL; pc and sp saved around the call (caller-saved registers).
    return ["push xix", "push xiy", "push wa", "cal _ngc_memory", "inc 0x2, xsp", "pop xiy", "pop xix",
            "lda xde, _script_memory"]


def _case(name, tag):
    """Lines of a case, ending on a jump back to the loop."""
    nxt = "j ngc_rpn_next"
    if name == "VM_OP_INT8":
        return ["ld a, (xix+:1)", "exts wa", "ld (xiy+:2), wa", nxt]
    if name == "VM_OP_INT16":
        return _read_index() + ["ld (xiy+:2), wa", nxt]
    if name == "VM_OP_REF":
        return _read_index() + _address(tag) + ["ld wa, (xwa)", "ld (xiy+:2), wa", nxt]
    if name == "VM_OP_REF_IND":
        return (_read_index() + _address(tag + "x") + ["ld wa, (xwa)"] + _address(tag + "y") +
                ["ld wa, (xwa)", "ld (xiy+:2), wa", nxt])
    if name == "VM_OP_REF_SET":
        return _read_index() + _address(tag) + ["ld bc, (xiy-0x2)", "dec 0x2, xiy", "ld (xwa), bc", nxt]
    if name == "VM_OP_REF_SET_IND":
        return (_read_index() + _address(tag + "x") + ["ld wa, (xwa)"] + _address(tag + "y") +
                ["ld bc, (xiy-0x2)", "dec 0x2, xiy", "ld (xwa), bc", nxt])
    if name == "VM_OP_REF_MEM":
        # op = *pc++; switch (op) { I8, U8, I16: *sp = memory } ; pc += 2; sp++
        return (["ld c, (xix+:1)"] + _read_index() +
                ["cp c, 0x49", f"j eq, {tag}_w", "cp c, 0x75", f"j eq, {tag}_u", "cp c, 0x69", f"j ne, {tag}_n"] +
                _memory() + ["ld a, (xhl)", "exts wa", f"j {tag}_s",
                f"{tag}_u:"] + _memory() + ["ld a, (xhl)", "extz wa", f"j {tag}_s",
                f"{tag}_w:"] + _memory() + ["ld wa, (xhl)",
                f"{tag}_s:", "ld (xiy), wa",
                f"{tag}_n:", "inc 0x2, xiy", nxt])
    if name == "VM_OP_REF_MEM_SET":
        # op = *pc++; switch (op) { I8, U8: byte, I16: word = *(--sp) }; pc += 2
        return (["ld c, (xix+:1)"] + _read_index() +
                ["cp c, 0x49", f"j eq, {tag}_w", "cp c, 0x75", f"j eq, {tag}_b", "cp c, 0x69", f"j ne, {tag}_n",
                 f"{tag}_b:"] + _memory() + ["ld wa, (xiy-0x2)", "dec 0x2, xiy", "ld (xhl), a", nxt,
                f"{tag}_w:"] + _memory() + ["ld wa, (xiy-0x2)", "dec 0x2, xiy", "ld (xhl), wa",
                f"{tag}_n:", nxt])
    # Operators: A = sp - 2 (xiy-0x4), B = sp - 1 (xiy-0x2); binary ones then sp--.
    end = ["ld (xiy-0x4), wa", "dec 0x2, xiy", nxt]
    alu = {"VM_OP_ADD": "add", "VM_OP_SUB": "sub", "VM_OP_B_AND": "and", "VM_OP_B_OR": "or", "VM_OP_B_XOR": "xor"}
    if name in alu:
        return ["ld wa, (xiy-0x4)", f"{alu[name]} wa, (xiy-0x2)"] + end
    compare = {"VM_OP_EQ": "eq", "VM_OP_NE": "ne", "VM_OP_LT": "lt", "VM_OP_LE": "le", "VM_OP_GT": "gt",
               "VM_OP_GE": "ge"}
    if name in compare:
        return ["ld wa, (xiy-0x4)", "cp wa, (xiy-0x2)", f"scc {compare[name]}, wa"] + end
    if name == "VM_OP_AND":
        return ["ld wa, 0x0", "cpw (xiy-0x4), 0x0", f"j eq, {tag}_s", "cpw (xiy-0x2), 0x0", f"j eq, {tag}_s",
                "ld wa, 0x1", f"{tag}_s:"] + end
    if name == "VM_OP_OR":
        return ["ld wa, 0x1", "cpw (xiy-0x4), 0x0", f"j ne, {tag}_s", "cpw (xiy-0x2), 0x0", f"j ne, {tag}_s",
                "ld wa, 0x0", f"{tag}_s:"] + end
    if name in ("VM_OP_MIN", "VM_OP_MAX"):
        # (*A < *B) ? *A : *B, (*A > *B) ? *A : *B
        return ["ld wa, (xiy-0x4)", "cp wa, (xiy-0x2)", f"j {'lt' if name == 'VM_OP_MIN' else 'gt'}, {tag}_s",
                "ld wa, (xiy-0x2)", f"{tag}_s:"] + end
    if name == "VM_OP_MUL":
        return ["ld bc, (xiy-0x2)", "ld wa, (xiy-0x4)", "muls xwa, bc"] + end
    # Unary: *B changed in place, sp unchanged (continue).
    unary = {"VM_OP_NOT": ["cpw (xiy-0x2), 0x0", "scc eq, wa"],
             "VM_OP_B_NOT": ["ld wa, (xiy-0x2)", "cpl wa"], "VM_OP_NEG": ["ld wa, (xiy-0x2)", "neg wa"]}
    return unary[name] + ["ld (xiy-0x2), wa", nxt]


def rpn_asm(cases, values):
    """The assembly body of vm_rpn for the used cases (`cases`, vm.h names)."""
    fast = [n for n in FAST if n in cases]
    lines = ["push xiz",
             # THIS: (xsp+4) at entry, (xsp+8) after the push.
             "ld xhl, (xsp+0x8)", "ld xix, (xhl)", "ld xiy, (xhl+0x10)", "ld xiz, xiy",
             "lda xde, _script_memory",
             "ngc_rpn_next:",
             "ld a, (xix+:1)", "cp a, 0x0", "j lt, ngc_rpn_neg", "j eq, ngc_rpn_stop"]
    positive = [n for n in fast if values[n] > 0]
    negative = [n for n in fast if values[n] < 0]
    for n in positive:
        lines += [f"cp a, 0x{values[n]:x}", f"j eq, ngc_rpn_{values[n]}"]
    lines += ["j ngc_rpn_slow", "ngc_rpn_neg:"]
    for n in negative:
        lines += [f"cp a, 0x{values[n] & 0xFF:x}", f"j eq, ngc_rpn_m{-values[n]}"]
    lines += ["j ngc_rpn_slow"]
    for n in fast:
        tag = f"ngc_rpn_{values[n]}" if values[n] > 0 else f"ngc_rpn_m{-values[n]}"
        lines += [f"{tag}:"] + _case(n, tag)
    lines += [
        # Any other byte: the C, from that byte, with this ARGS.
        "ngc_rpn_slow:",
        "dec 0x1, xix", "ld xhl, (xsp+0x8)", "ld (xhl), xix", "ld (xhl+0x10), xiy",
        "push xiz", "push xhl", "cal _ngc_rpn_c", "lda xsp, xsp+0x8", "j ngc_rpn_end",
        # STOP: THIS->PC = pc; THIS->stack_ptr = sp; return.
        "ngc_rpn_stop:",
        "ld xhl, (xsp+0x8)", "ld (xhl), xix", "ld (xhl+0x10), xiy",
        "ngc_rpn_end:",
        "pop xiz"]
    check_forms(lines)
    return lines


def fast_rpn(rpn_c, cases, c_defines, context):
    """(C, report): rpn_c (the final C vm_rpn) as ngc_rpn_c(THIS, ARGS) plus the assembly vm_rpn,
    or rpn_c unchanged with the reason."""
    if not " ".join(context.split()).startswith(" ".join(CONTEXT.split())):
        return rpn_c, {"fast_rpn": False, "why": "SCRIPT_CTX layout"}
    if rpn_c.count(C_HEAD) != 1 or rpn_c.count(C_LOCALS) != 1 or rpn_c.count(C_ARGS) != 1:
        return rpn_c, {"fast_rpn": False, "why": "vm_rpn variant"}
    values = _values(c_defines)
    slow = (rpn_c.replace(C_HEAD, "static void ngc_rpn_c(SCRIPT_CTX * THIS, INT16 * ARGS) {\n")
            .replace(C_LOCALS, "    INT16 * A, * B;\n").replace(C_ARGS, ""))
    asm = "\n".join(f'    __asm("{line}");' for line in rpn_asm(cases, values))
    fast = ("/* engine_speedups: vm_rpn in assembly, pc/sp/ARGS in registers (docs/ETUDE_CADENCE.md §6.6,\n"
            " * ngpcraft/gbvm/rpn_fast.py); any case it does not run goes on in ngc_rpn_c, the original. */\n"
            "static void vm_rpn(SCRIPT_CTX * THIS) {\n" + asm + "\n}\n")
    return slow + "\n" + fast, {"fast_rpn": True, "assembly": sorted(n for n in FAST if n in cases),
                                "c": sorted(n for n in cases if n not in FAST)}
