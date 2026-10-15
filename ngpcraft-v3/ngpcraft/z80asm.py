"""A small Z80 assembler for the sound CPU's code (docs/PLAN_MUSIQUE_Z80.md Z1).

Two passes, the forms the V3's Z80 code uses and no more; anything else is refused (a
CompileError naming the line). Encodings: Zilog Z80 CPU User Manual (UM0080), tables of the
8-bit and 16-bit loads, arithmetic, rotates and shifts, bit operations, jumps, calls and
returns. Checked against the console-validated relay bytes of audio.c (tests/test_z80asm.py)
and run on the emulator's Z80 core.

Syntax: one statement per line, `;` comments, `label:`, `name equ expr`, `org expr`,
`db expr, ...`, `dw expr, ...`, `ds count`. Operands are lower case; expressions are integers
(decimal, 0x..), labels and equ names joined by + and -; `(ix+d)` / `(iy+d)` take a signed
displacement expression.
"""
import re

from .compiler import CompileError

R8 = {"b": 0, "c": 1, "d": 2, "e": 3, "h": 4, "l": 5, "a": 7}
RP = {"bc": 0, "de": 1, "hl": 2, "sp": 3}
RP_PUSH = {"bc": 0, "de": 1, "hl": 2, "af": 3}
CC = {"nz": 0, "z": 1, "nc": 2, "c": 3, "po": 4, "pe": 5, "p": 6, "m": 7}
JR_CC = {"nz": 0, "z": 1, "nc": 2, "c": 3}
ALU = {"add": 0, "adc": 1, "sub": 2, "sbc": 3, "and": 4, "xor": 5, "or": 6, "cp": 7}
ROT = {"rlc": 0, "rrc": 1, "rl": 2, "rr": 3, "sla": 4, "sra": 5, "srl": 7}
INDEX = {"ix": 0xDD, "iy": 0xFD}
SIMPLE = {"nop": [0x00], "rlca": [0x07], "rrca": [0x0F], "rla": [0x17], "rra": [0x1F], "cpl": [0x2F],
          "scf": [0x37], "ccf": [0x3F], "halt": [0x76], "exx": [0xD9], "di": [0xF3], "ei": [0xFB],
          "ret": [0xC9], "neg": [0xED, 0x44], "ldi": [0xED, 0xA0], "ldir": [0xED, 0xB0],
          "ldd": [0xED, 0xA8], "lddr": [0xED, 0xB8]}


class _Pending(Exception):
    """A label not yet defined (first pass)."""


def _split(operands):
    out, depth, cur = [], 0, ""
    for ch in operands:
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


class Assembler:
    def __init__(self, symbols=None):
        self.symbols = dict(symbols or {})

    def value(self, text, final):
        text = text.replace(" ", "")
        if not text:
            raise CompileError("z80: empty expression")
        total, sign, pos = 0, 1, 0
        for m in re.finditer(r"([+-]?)([^+-]+)", text):
            if m.start() != pos:
                raise CompileError(f"z80: bad expression {text!r}")
            pos = m.end()
            s, term = m.groups()
            sign = -1 if s == "-" else 1
            if re.fullmatch(r"0x[0-9a-f]+|\d+", term):
                v = int(term, 0)
            elif re.fullmatch(r"[a-z_]\w*", term):
                if term in self.symbols:
                    v = self.symbols[term]
                elif final:
                    raise CompileError(f"z80: undefined symbol {term!r}")
                else:
                    raise _Pending(term)
            else:
                raise CompileError(f"z80: bad term {term!r} in {text!r}")
            total += sign * v
        if pos != len(text):
            raise CompileError(f"z80: bad expression {text!r}")
        return total

    def n8(self, text, final):
        v = self.value(text, final)
        if not -128 <= v <= 255:
            raise CompileError(f"z80: {text!r} = {v} does not fit a byte")
        return v & 0xFF

    def n16(self, text, final):
        v = self.value(text, final)
        if not -32768 <= v <= 0xFFFF:
            raise CompileError(f"z80: {text!r} = {v} does not fit a word")
        return [v & 0xFF, (v >> 8) & 0xFF]

    def indexed(self, op, final):
        """(ix+d) / (iy+d) -> (prefix, d) or None."""
        m = re.fullmatch(r"\((ix|iy)\s*([+-].*)?\)", op)
        if not m:
            return None
        d = self.value(m[2], final) if m[2] else 0
        if not -128 <= d <= 127:
            raise CompileError(f"z80: displacement {d} out of range")
        return INDEX[m[1]], d & 0xFF

    @staticmethod
    def memory(op):
        m = re.fullmatch(r"\((.+)\)", op)
        return m[1].strip() if m else None

    def encode(self, mnemonic, ops, pc, final):
        """Bytes of one instruction at pc."""
        if mnemonic in SIMPLE and not ops:
            return list(SIMPLE[mnemonic])
        n = len(ops)
        if mnemonic == "ld" and n == 2:
            return self.ld(ops[0], ops[1], final)
        if mnemonic in ("push", "pop") and n == 1:
            base = 0xC5 if mnemonic == "push" else 0xC1
            if ops[0] in INDEX:
                return [INDEX[ops[0]], base + 0x20]
            if ops[0] in RP_PUSH:
                return [base + (RP_PUSH[ops[0]] << 4)]
        if mnemonic in ALU:
            if mnemonic in ("add", "adc", "sbc") and n == 2 and ops[0] == "hl" and ops[1] in RP:
                return {"add": [0x09 + (RP[ops[1]] << 4)], "adc": [0xED, 0x4A + (RP[ops[1]] << 4)],
                        "sbc": [0xED, 0x42 + (RP[ops[1]] << 4)]}[mnemonic]
            if mnemonic == "add" and n == 2 and ops[0] in INDEX and ops[1] in ("bc", "de", "sp", ops[0]):
                rr = {"bc": 0, "de": 1, ops[0]: 2, "sp": 3}[ops[1]]
                return [INDEX[ops[0]], 0x09 + (rr << 4)]
            src = ops[1] if n == 2 and ops[0] == "a" and mnemonic in ("add", "adc", "sbc") else (ops[0] if n == 1 else None)
            if src is not None:
                k = ALU[mnemonic]
                if src in R8:
                    return [0x80 + (k << 3) + R8[src]]
                if src == "(hl)":
                    return [0x86 + (k << 3)]
                ix = self.indexed(src, final)
                if ix:
                    return [ix[0], 0x86 + (k << 3), ix[1]]
                return [0xC6 + (k << 3), self.n8(src, final)]
        if mnemonic in ("inc", "dec") and n == 1:
            d = mnemonic == "dec"
            r = ops[0]
            if r in R8:
                return [0x04 + d + (R8[r] << 3)]
            if r == "(hl)":
                return [0x34 + d]
            if r in RP:
                return [0x03 + (d << 3) + (RP[r] << 4)]
            if r in INDEX:
                return [INDEX[r], 0x23 + (d << 3)]
            ix = self.indexed(r, final)
            if ix:
                return [ix[0], 0x34 + d, ix[1]]
        if mnemonic in ROT and n == 1:
            k = ROT[mnemonic]
            if ops[0] in R8:
                return [0xCB, (k << 3) + R8[ops[0]]]
            if ops[0] == "(hl)":
                return [0xCB, (k << 3) + 6]
            ix = self.indexed(ops[0], final)
            if ix:
                return [ix[0], 0xCB, ix[1], (k << 3) + 6]
        if mnemonic in ("bit", "set", "res") and n == 2:
            b = self.value(ops[0], final)
            if not 0 <= b <= 7:
                raise CompileError(f"z80: bit number {b}")
            base = {"bit": 0x40, "res": 0x80, "set": 0xC0}[mnemonic] + (b << 3)
            if ops[1] in R8:
                return [0xCB, base + R8[ops[1]]]
            if ops[1] == "(hl)":
                return [0xCB, base + 6]
            ix = self.indexed(ops[1], final)
            if ix:
                return [ix[0], 0xCB, ix[1], base + 6]
        if mnemonic == "ex" and n == 2:
            if ops == ["de", "hl"]:
                return [0xEB]
            if ops == ["af", "af'"]:
                return [0x08]
            if ops == ["(sp)", "hl"]:
                return [0xE3]
        if mnemonic == "jp":
            if n == 1 and ops[0] == "(hl)":
                return [0xE9]
            if n == 1:
                return [0xC3] + self.n16(ops[0], final)
            if n == 2 and ops[0] in CC:
                return [0xC2 + (CC[ops[0]] << 3)] + self.n16(ops[1], final)
        if mnemonic in ("jr", "djnz"):
            if mnemonic == "djnz" and n == 1:
                opcode, target = 0x10, ops[0]
            elif mnemonic == "jr" and n == 1:
                opcode, target = 0x18, ops[0]
            elif mnemonic == "jr" and n == 2 and ops[0] in JR_CC:
                opcode, target = 0x20 + (JR_CC[ops[0]] << 3), ops[1]
            else:
                raise CompileError(f"z80: {mnemonic} {', '.join(ops)}")
            try:
                e = self.value(target, final) - (pc + 2)
            except _Pending:
                if final:
                    raise
                e = 0
            if final and not -128 <= e <= 127:
                raise CompileError(f"z80: relative jump to {target} out of range ({e})")
            return [opcode, e & 0xFF]
        if mnemonic == "call":
            if n == 1:
                return [0xCD] + self.n16(ops[0], final)
            if n == 2 and ops[0] in CC:
                return [0xC4 + (CC[ops[0]] << 3)] + self.n16(ops[1], final)
        if mnemonic == "ret" and n == 1 and ops[0] in CC:
            return [0xC0 + (CC[ops[0]] << 3)]
        raise CompileError(f"z80: unsupported instruction {mnemonic} {', '.join(ops)}")

    def ld(self, dst, src, final):
        # 8-bit register / (hl) / (ix+d)
        if dst in R8 and src in R8:
            return [0x40 + (R8[dst] << 3) + R8[src]]
        if dst in R8 and src == "(hl)":
            return [0x46 + (R8[dst] << 3)]
        if dst == "(hl)" and src in R8:
            return [0x70 + R8[src]]
        if dst in R8 and self.indexed(src, final):
            p, d = self.indexed(src, final)
            return [p, 0x46 + (R8[dst] << 3), d]
        if self.indexed(dst, final) and src in R8:
            p, d = self.indexed(dst, final)
            return [p, 0x70 + R8[src], d]
        if self.indexed(dst, final):
            p, d = self.indexed(dst, final)
            return [p, 0x36, d, self.n8(src, final)]
        if dst == "(hl)":
            return [0x36, self.n8(src, final)]
        if dst == "a" and src in ("(bc)", "(de)"):
            return [0x0A if src == "(bc)" else 0x1A]
        if src == "a" and dst in ("(bc)", "(de)"):
            return [0x02 if dst == "(bc)" else 0x12]
        mem_src, mem_dst = self.memory(src), self.memory(dst)
        if dst == "a" and mem_src is not None:
            return [0x3A] + self.n16(mem_src, final)
        if src == "a" and mem_dst is not None:
            return [0x32] + self.n16(mem_dst, final)
        if dst in R8:
            return [0x06 + (R8[dst] << 3), self.n8(src, final)]
        # 16-bit
        if dst == "sp" and src == "hl":
            return [0xF9]
        if dst == "hl" and mem_src is not None:
            return [0x2A] + self.n16(mem_src, final)
        if src == "hl" and mem_dst is not None:
            return [0x22] + self.n16(mem_dst, final)
        if dst in INDEX and mem_src is not None:
            return [INDEX[dst], 0x2A] + self.n16(mem_src, final)
        if src in INDEX and mem_dst is not None:
            return [INDEX[src], 0x22] + self.n16(mem_dst, final)
        if dst in RP and mem_src is not None:
            return [0xED, 0x4B + (RP[dst] << 4)] + self.n16(mem_src, final)
        if src in RP and mem_dst is not None:
            return [0xED, 0x43 + (RP[src] << 4)] + self.n16(mem_dst, final)
        if dst in RP:
            return [0x01 + (RP[dst] << 4)] + self.n16(src, final)
        if dst in INDEX:
            return [INDEX[dst], 0x21] + self.n16(src, final)
        raise CompileError(f"z80: unsupported ld {dst}, {src}")

    def assemble(self, source, origin=0):
        """-> (bytes, symbols). Two passes; sizes do not depend on label values."""
        lines = []
        for number, raw in enumerate(source.splitlines(), 1):
            text = raw.split(";", 1)[0].strip().lower()
            if text:
                lines.append((number, raw, text))
        for final in (False, True):
            pc, out = origin, bytearray()
            for number, raw, text in lines:
                try:
                    pc = self._statement(text, pc, out, final)
                except _Pending:
                    raise CompileError(f"z80 line {number}: internal pending label")
                except CompileError as e:
                    raise CompileError(f"z80 line {number} ({raw.strip()}): {e}") from None
        return bytes(out), dict(self.symbols)

    def _statement(self, text, pc, out, final):
        m = re.fullmatch(r"([a-z_]\w*):\s*(.*)", text)
        if m:
            name = m[1]
            if not final and name in self.symbols:
                raise CompileError(f"label {name!r} defined twice")
            self.symbols[name] = pc
            text = m[2]
            if not text:
                return pc
        m = re.fullmatch(r"([a-z_]\w*)\s+equ\s+(.+)", text)
        if m:
            self.symbols[m[1]] = self.value(m[2], True)
            return pc
        mnemonic, _, rest = text.partition(" ")
        ops = _split(rest)
        if mnemonic == "org":
            target = self.value(ops[0], True)
            if target < pc:
                raise CompileError("org goes backwards")
            out.extend(b"\0" * (target - pc))
            return target
        if mnemonic in ("db", "dw"):
            for op in ops:
                try:
                    data = [self.n8(op, final)] if mnemonic == "db" else self.n16(op, final)
                except _Pending:
                    data = [0] * (1 if mnemonic == "db" else 2)
                out.extend(data)
                pc += len(data)
            return pc
        if mnemonic == "ds":
            count = self.value(ops[0], True)
            out.extend(b"\0" * count)
            return pc + count
        try:
            data = self.encode(mnemonic, ops, pc, final)
        except _Pending:
            # First pass: the size of an instruction never depends on a label's value.
            data = self.encode_sized(mnemonic, ops, pc)
        out.extend(data)
        return pc + len(data)

    def encode_sized(self, mnemonic, ops, pc):
        saved = dict(self.symbols)
        try:
            for op in ops:
                for name in re.findall(r"[a-z_]\w*", op):
                    if name not in self.symbols and name not in R8 and name not in RP and name not in INDEX \
                            and name not in CC and name not in ("af", "sp"):
                        self.symbols[name] = pc
            return self.encode(mnemonic, ops, pc, False)
        finally:
            self.symbols = saved


def assemble(source, origin=0, symbols=None):
    return Assembler(symbols).assemble(source, origin)
