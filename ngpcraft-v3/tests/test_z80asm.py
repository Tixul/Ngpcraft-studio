"""The Z80 assembler (docs/PLAN_MUSIQUE_Z80.md Z1): the console-validated relay of audio.c
reassembled byte for byte, every supported form against the Zilog encodings, refusals."""
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.z80asm import assemble

# audio.c s_z80drv (relay run on every NGPC console since the V3's first sound).
RELAY = bytes([0xC3, 0x13, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
               0x00, 0x00, 0x00, 0x00, 0x00, 0xF3, 0x31, 0x00, 0x10, 0x3A, 0x03, 0x00, 0xB7, 0x28,
               0xFA, 0x47, 0x21, 0x04, 0x00, 0x7E, 0x32, 0x01, 0x40, 0x32, 0x00, 0x40, 0x23, 0x7E,
               0x32, 0x01, 0x40, 0x32, 0x00, 0x40, 0x23, 0x7E, 0x32, 0x01, 0x40, 0x32, 0x00, 0x40,
               0x23, 0x10, 0xE6, 0xAF, 0x32, 0x03, 0x00, 0x18, 0xD6])
RELAY_SOURCE = """
    jp start
    ds 16
start:
    di
    ld sp, 0x1000
wait:
    ld a, (0x0003)
    or a
    jr z, wait
    ld b, a
    ld hl, 0x0004
next:
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    ld a, (hl)
    ld (0x4001), a
    ld (0x4000), a
    inc hl
    djnz next
    xor a
    ld (0x0003), a
    jr wait
"""

# (source, bytes) from the Zilog Z80 CPU User Manual tables.
FORMS = [
    ("ld b, c", "41"), ("ld a, (hl)", "7e"), ("ld (hl), e", "73"), ("ld d, 0x12", "16 12"),
    ("ld (hl), 0x34", "36 34"), ("ld a, (bc)", "0a"), ("ld a, (de)", "1a"), ("ld (bc), a", "02"),
    ("ld (de), a", "12"), ("ld a, (0x1234)", "3a 34 12"), ("ld (0x1234), a", "32 34 12"),
    ("ld c, (ix+5)", "dd 4e 05"), ("ld (iy-2), a", "fd 77 fe"), ("ld (ix+1), 0x7f", "dd 36 01 7f"),
    ("ld bc, 0x1234", "01 34 12"), ("ld sp, 0x1000", "31 00 10"), ("ld ix, 0x0100", "dd 21 00 01"),
    ("ld hl, (0x0200)", "2a 00 02"), ("ld (0x0200), hl", "22 00 02"), ("ld de, (0x0300)", "ed 5b 00 03"),
    ("ld (0x0300), bc", "ed 43 00 03"), ("ld iy, (0x10)", "fd 2a 10 00"), ("ld (0x10), ix", "dd 22 10 00"),
    ("ld sp, hl", "f9"),
    ("push bc", "c5"), ("push af", "f5"), ("pop hl", "e1"), ("push ix", "dd e5"), ("pop iy", "fd e1"),
    ("add a, b", "80"), ("adc a, (hl)", "8e"), ("sub 0x10", "d6 10"), ("sbc a, 1", "de 01"),
    ("and e", "a3"), ("xor a", "af"), ("or (ix+2)", "dd b6 02"), ("cp 0xff", "fe ff"),
    ("add hl, de", "19"), ("adc hl, bc", "ed 4a"), ("sbc hl, de", "ed 52"), ("add ix, bc", "dd 09"),
    ("add iy, iy", "fd 29"),
    ("inc a", "3c"), ("dec b", "05"), ("inc (hl)", "34"), ("dec hl", "2b"), ("inc sp", "33"),
    ("inc ix", "dd 23"), ("dec (iy+3)", "fd 35 03"),
    ("rlc b", "cb 00"), ("srl a", "cb 3f"), ("sla (hl)", "cb 26"), ("rr (ix+4)", "dd cb 04 1e"),
    ("bit 7, a", "cb 7f"), ("set 0, (hl)", "cb c6"), ("res 3, (iy+1)", "fd cb 01 9e"),
    ("ex de, hl", "eb"), ("ex af, af'", "08"), ("ex (sp), hl", "e3"),
    ("jp 0x1234", "c3 34 12"), ("jp nz, 0x0010", "c2 10 00"), ("jp m, 0x0010", "fa 10 00"), ("jp (hl)", "e9"),
    ("call 0x0100", "cd 00 01"), ("call z, 0x0100", "cc 00 01"), ("ret", "c9"), ("ret nc", "d0"),
    ("nop", "00"), ("di", "f3"), ("ei", "fb"), ("halt", "76"), ("exx", "d9"), ("neg", "ed 44"),
    ("ldir", "ed b0"), ("ldi", "ed a0"), ("rra", "1f"), ("cpl", "2f"), ("scf", "37"),
]


class Z80AssemblerTests(unittest.TestCase):
    def test_relay_reassembles_byte_for_byte(self):
        code, symbols = assemble(RELAY_SOURCE)
        self.assertEqual(code, RELAY)
        self.assertEqual(symbols["start"], 0x13)

    def test_forms_match_zilog(self):
        for source, expected in FORMS:
            with self.subTest(source):
                self.assertEqual(assemble(source)[0].hex(" "), expected)

    def test_relative_jumps_forward_and_back(self):
        code, _ = assemble("top:\n jr nc, ahead\n djnz top\nahead:\n jr top")
        self.assertEqual(code.hex(" "), "30 02 10 fc 18 fa")

    def test_data_equ_and_org(self):
        code, symbols = assemble("base equ 0x40\n db 1, base+2\n dw label\n org 8\nlabel: db -1")
        self.assertEqual(code.hex(" "), "01 42 08 00 00 00 00 00 ff")
        self.assertEqual(symbols["label"], 8)

    def test_refusals(self):
        for source in ("ld a, (ix+200)", "jr 0x200", "ld (bc), b", "ld a, undefined", "inc (de)",
                       "lb a, b", "ld b, 300", "label:\nlabel:", "jr pe, 0"):
            with self.subTest(source), self.assertRaises(CompileError):
                assemble(source)


if __name__ == "__main__":
    unittest.main()
