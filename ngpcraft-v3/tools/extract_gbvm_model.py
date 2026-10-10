"""M1: freeze the GBVM bytecode model, extracted from ITS sources.

The V3 assembles GB Studio's script output (the text scriptBuilder writes)
into GBVM bytecode. The encoding of each instruction is not written by hand:
this tool reads the macros of include/vm.i (opcode and operand bytes) and the
argument lengths of src/core/vm_instructions.c (script_cmds), checks that
they agree, and writes ngpcraft/gbvm_model.json. A test re-extracts and
compares, so a new GBVM or a hand edit turns red.

A macro is "simple" when its body is one `.db` line of opcode and operand
bytes: `#>P` high byte, `#<P` or `#P` low byte of the parameter P. Any other
macro (RPN sequences, `.dw`, `.if`, nested macros) is listed as composite and
is refused by the assembler until it is delivered.

Sources (reference: tools/reference.py, GBVM pinned by GB Studio 4.3.2):
  appData/engine/gbvm/include/vm.i
  appData/engine/gbvm/src/core/vm_instructions.c

  python -B -m tools.extract_gbvm_model write | check
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
from tools.reference import GBVM_COMMIT, LABEL, REFERENCE
MODEL = ROOT / "ngpcraft" / "gbvm_model.json"
GBVM = "appData/engine/gbvm"
SOURCES = {"macros": f"{GBVM}/include/vm.i", "instructions": f"{GBVM}/src/core/vm_instructions.c"}


class ParseError(ValueError):
    pass


def _number(text):
    text = text.strip()
    if re.fullmatch(r"-?0x[0-9A-Fa-f]+", text):
        return int(text, 16)
    if re.fullmatch(r"0b[01]+", text):
        return int(text[2:], 2)                        # .CAMERA_LOCK = 0b00000011
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"'[ -~]'", text):
        return ord(text[1])                            # .MEM_I8 = 'i'
    return None


def parse_macros(text):
    text = re.sub(r"\r\n?", "\n", text)
    opcodes, constants, macros, composite = {}, {}, {}, {}
    body_spans = []
    for m in re.finditer(r"^\.macro[ \t]+(\S+)[ \t]*([^\n;]*)\n(.*?)^\.endm", text, re.M | re.S):
        # sdas accepts a comma after the name (`.macro VM_RATE_LIMIT_CONST, N, IDX, LABEL`).
        # Parameters are separated by commas or blanks (`.macro .CGB_PAL R1,G1,B1 R2,G2,B2 ...`).
        name, params, body = m[1].rstrip(","), re.split(r"[\s,]+", m[2].strip()) if m[2].strip() else [], m[3]
        body_spans.append(m.span())
        lines = [l.split(";")[0].strip() for l in body.split("\n")]
        lines = [l for l in lines if l]
        if name in macros or name in composite:
            raise ParseError(f"duplicate macro {name}")
        if not all(l.startswith(".db ") for l in lines):
            # Expanded by the assembler, line by line (gbvm/assembler.py).
            composite[name] = {"params": params, "lines": lines}
            continue
        # Only .db lines (VM_RAND writes its operands on a second line): one
        # fixed-length instruction, its bytes in order.
        operands = []
        for token in (t.strip() for l in lines for t in l[4:].split(",")):
            hm = re.fullmatch(r"#([<>]?)([A-Za-z_][A-Za-z0-9_]*)", token)
            if re.fullmatch(r"OP_VM_[A-Z0-9_]+", token) and not operands:
                operands.append({"opcode": token})
            elif _number(token) is not None:
                operands.append({"byte": _number(token)})
            elif hm and hm[2] in params:
                operands.append({"param": hm[2], "part": "high" if hm[1] == ">" else "low"})
            else:
                operands = None
                break
        if not operands or "opcode" not in operands[0]:
            composite[name] = {"params": params, "lines": lines}
            continue
        macros[name] = {"params": params, "bytes": operands}
    outside = text
    for start, end in reversed(body_spans):
        outside = outside[:start] + outside[end:]
    for m in re.finditer(r"^(OP_VM_[A-Z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+)\s*$", outside, re.M):
        if m[1] in opcodes:
            raise ParseError(f"duplicate opcode {m[1]}")
        opcodes[m[1]] = int(m[2], 16)
    # Dotted constants, and the exception codes of VM_RAISE (EXCEPTION_CHANGE_SCENE...).
    pending = dict(re.findall(r"^(\.[A-Z][A-Z0-9_]*|EXCEPTION_[A-Z_]+)\s*=\s*([^;\n]+?)\s*(?:;.*)?$", outside, re.M))
    while pending:
        progress = False
        for name, expr in list(pending.items()):
            value = _number(expr)
            if value is None and expr in constants:
                value = constants[expr]
            if value is not None:
                constants[name] = value
                del pending[name]
                progress = True
        if not progress:
            # Expressions of several terms are not needed yet: keep them out.
            break
    for name, spec in macros.items():
        if spec["bytes"][0]["opcode"] not in opcodes:
            raise ParseError(f"macro {name}: unknown opcode {spec['bytes'][0]['opcode']}")
    return opcodes, constants, macros, dict(sorted(composite.items()))


def parse_instructions(text):
    """script_cmds[] of vm_instructions.c: opcode (index + 1) -> handler, args bytes."""
    body = re.search(r"const SCRIPT_CMD script_cmds\[\] = \{(.*?)\n\};", text, re.S)
    if not body:
        raise ParseError("script_cmds not found")
    table = {}
    for index, m in enumerate(re.finditer(r"\{\s*(\w+)\s*,\s*([^,]+?)\s*,\s*(\d+)\s*\}\s*,?[ \t]*(?://\s*(0x[0-9A-Fa-f]+))?",
                                          body[1])):
        opcode = index + 1
        if m[4] is not None and int(m[4], 16) != opcode:
            raise ParseError(f"script_cmds entry {m[1]} commented {m[4]}, at position {opcode:#04x}")
        if m[1] != "0":
            table[opcode] = {"handler": m[1], "args": int(m[3])}
    return table


def extract(root=REFERENCE):
    root = Path(root)
    texts = {k: (root / p).read_text(encoding="utf-8-sig") for k, p in SOURCES.items()}
    opcodes, constants, macros, composite = parse_macros(texts["macros"])
    instructions = parse_instructions(texts["instructions"])
    by_value = {v: k for k, v in opcodes.items()}
    for name, spec in macros.items():
        code = opcodes[spec["bytes"][0]["opcode"]]
        if code == 0:
            continue                                   # VM_STOP: the terminator, not in script_cmds
        if code not in instructions:
            raise ParseError(f"macro {name}: opcode {code:#04x} has no script_cmds entry")
        if len(spec["bytes"]) - 1 != instructions[code]["args"]:
            raise ParseError(f"macro {name}: {len(spec['bytes']) - 1} operand bytes, "
                             f"script_cmds says {instructions[code]['args']}")
    return {
        "reference": LABEL,
        "gbvm_commit": GBVM_COMMIT,
        "sources": {k: {"path": p, "sha256": hashlib.sha256((root / p).read_bytes()).hexdigest()}
                    for k, p in SOURCES.items()},
        "opcodes": opcodes,
        "instructions": {by_value.get(c, f"{c:#04x}"): {**v, "opcode": c} for c, v in sorted(instructions.items())},
        "constants": constants,
        "macros": macros,
        "composite_macros": composite,
    }


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("write", "check"))
    a = p.parse_args()
    fresh = extract()
    if a.command == "write":
        write_json(MODEL, fresh)
        print(f"written {MODEL}: {len(fresh['macros'])} simple macros, "
              f"{len(fresh['composite_macros'])} composite, {len(fresh['opcodes'])} opcodes")
        return 0
    same = json.loads(MODEL.read_text(encoding="utf-8")) == fresh
    print("GBVM model up to date" if same else "GBVM model differs from the reference: rerun write and review")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
