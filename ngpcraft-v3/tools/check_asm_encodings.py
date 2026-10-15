"""Decode the hand-written assembly of a built export and check every instruction against
the emulator's database of silicon-broken encodings (docs/ETUDE_CADENCE.md §6.2).

The NGPC has encodings that hang the real CPU and that the emulator runs (ALU on WA
with a 16-bit immediate, shifts by A): a test on the emulator cannot see them. The
functions checked are the public symbols matching --symbols (default: the sprite
fast actor loop of every scene); each is decoded from its address to the next public
symbol with the emulator's decoder, and each instruction goes through
core.quirks.match_known_silicon_broken. Exit 1 on any match or undecodable byte.

  python -B -m tools.check_asm_encodings <built export> --emulator <Ngpcraft_emulator>
         [--symbols REGEX]
"""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


@dataclass
class _Read:
    status: str
    data: bytes


class _RomBus:
    def __init__(self, rom):
        self.rom = rom

    def read_bytes(self, address, size=1):
        at = address - 0x200000
        if 0 <= at and at + size <= len(self.rom):
            return _Read("ok", self.rom[at:at + size])
        return _Read("unmapped", b"")


def _unproven(raw):
    """Register-prefixed forms never cleared on silicon, which the emulator's database does not
    list (its d8_df entry names them as "a separate quirk"): the shift by A (second byte
    0xF8..0xFF) and the 0xB8..0xBF hole, after a byte, word or long register prefix
    (0xC8..0xCF, 0xD8..0xDF, 0xE8..0xEF). Hand-written assembly avoids them; cc900 itself
    emits the shift by A for a variable count (vm_rpn's VM_OP_SHL / VM_OP_SHR: `sll A, DE`,
    found 2026-10-15, docs/ETUDE_CADENCE.md §6.6), so the C runtime is not free of it."""
    if raw and len(raw) >= 2 and (0xC8 <= raw[0] <= 0xCF or 0xD8 <= raw[0] <= 0xDF or 0xE8 <= raw[0] <= 0xEF):
        if raw[1] >= 0xF8:
            return "shift by A (never cleared on NGPC silicon)"
        if 0xB8 <= raw[1] <= 0xBF:
            return "register form 0xB8..0xBF (never cleared on NGPC silicon)"
    return None


def check(export, emulator, symbols=r"_(s\d+_)?ngc_sprite_draw_fast$"):
    """-> (instructions checked, [problems])."""
    if str(emulator) not in sys.path:
        sys.path.insert(0, str(emulator))
    from core.decode import decode_instruction_at
    from core.quirks import match_known_silicon_broken
    rom = (export / "rom/game.ngc").read_bytes()
    sym = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))["link_audit"]["public_symbols"]
    code = sorted(v["address"] for v in sym.values() if v["section"] == "f_code")
    bus, checked, problems = _RomBus(rom), 0, []
    for name, v in sorted(sym.items()):
        if v["section"] != "f_code" or not re.fullmatch(symbols, name):
            continue
        pc = v["address"]
        end = min((a for a in code if a > pc), default=0x200000 + len(rom))
        while pc < end:
            d = decode_instruction_at(bus, pc)
            if d.status != "decoded" or not d.length:
                problems.append(f"{name}+0x{pc - v['address']:X}: undecodable ({d.status})")
                break
            hit = match_known_silicon_broken(d) or _unproven(d.raw_bytes)
            if hit is not None:
                problems.append(f"{name}+0x{pc - v['address']:X}: {d.assembly} = {hit}")
            checked += 1
            pc += d.length
    return checked, problems


def exposed_copy(export, out, toolchain):
    """The export rebuilt with its static functions public (tools/lab_cadence.expose_statics):
    a scene's functions are static in a linked export, so absent from the symbol table."""
    import shutil
    from tools.lab_cadence import build_exposed, expose_statics
    if out.exists():
        raise SystemExit(f"{out}: must be new")
    shutil.copytree(export, out, ignore=shutil.ignore_patterns("rom", "objects"))
    expose_statics(out)
    build_exposed(out, toolchain)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("export", type=Path)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--symbols", default=r"_(s\d+_)?ngc_actors_draw_loop$")
    p.add_argument("--expose", type=Path, help="new dir: check a copy with the static functions public")
    p.add_argument("--toolchain", type=Path, default=Path(r"C:\t900"))
    a = p.parse_args()
    export = exposed_copy(a.export, a.expose, a.toolchain) if a.expose else a.export
    checked, problems = check(export, a.emulator, a.symbols)
    print(f"{checked} instructions checked, {len(problems)} problems")
    for line in problems:
        print("  " + line)
    raise SystemExit(1 if problems or not checked else 0)


if __name__ == "__main__":
    main()
