"""ngpcraft/sprite_fast.py (docs/ETUDE_CADENCE.md §6.2): only the base renderer and loop,
assembly forms from the whitelist only. The ROM proofs are tools/lab_speedups.py
(the same game image for image) and tools/check_asm_encodings.py."""
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.sprite_fast import DRAW_TEMPLATE, LOOP, check_forms, fast_draw, loop_asm

SPRITES = ("static unsigned short ngc_slot_frame[64];\n"
           "void ngc_sprites_begin(void)\n{\n    ngc_oam_used = 0u;\n}\n\n"
           + DRAW_TEMPLATE.format(frames=16, xmax=159, ymax=151))


def actors(width="short"):
    return (f"unsigned {width} act_x[11];\nunsigned {width} act_y[11];\n"
            "static unsigned char act_flags[11];\nstatic unsigned char act_frame[11];\n"
            "static unsigned char act_prev[11];\n"
            "static const unsigned short act_spr_order[3] = {\n    0u, 1u, 2u,\n};\n"
            "static const unsigned short act_order_base[1] = {\n    0u,\n};\n"
            "static unsigned short draw_x;\nstatic unsigned short draw_y;\n"
            "void ngc_actors_render(void)\n{\n    unsigned char i = 0u;\n    short sx;\n    short sy;\n"
            "    ngc_sprites_begin();\n" + LOOP + "    ngc_sprites_end();\n}\n")


class SpriteFastTests(unittest.TestCase):
    def test_base_renderer_gets_the_loop(self):
        for width, bits in (("short", 16), ("long", 32)):
            sprites, act, report = fast_draw(SPRITES, actors(width))
            self.assertEqual(report["fast_draw"], True)
            self.assertEqual(report["positions"], bits)
            self.assertEqual(report["extra_publics"], ["ngc_slot_frame", "ngc_spr_rec_tab"])
            self.assertNotIn(LOOP, act)
            self.assertIn("    ngc_actors_draw_loop();\n", act)
            self.assertIn("ngc_spr_rec_tab = (unsigned long)spr_rec;", sprites)
            self.assertTrue(sprites.startswith("unsigned short ngc_slot_frame[64];\n"))   # public now
            # The bounds of this scene's C function, read from it.
            self.assertIn('__asm("cp hl, 0x10");', act)
            self.assertIn('__asm("cp hl, 0x9f");', act)
            self.assertIn('__asm("cp hl, 0x97");', act)
            self.assertIn('__asm("srl 0x5, xhl");' if bits == 32 else '__asm("srl 0x5, hl");', act)

    def test_a_variant_keeps_the_c(self):
        variant = SPRITES.replace("    if (*key != frame) {", "    ngc_extra();\n    if (*key != frame) {")
        sprites, act, report = fast_draw(variant, actors())
        self.assertEqual(report["fast_draw"], False)
        self.assertEqual(act, actors())
        self.assertIn(LOOP, act)
        loop_changed = actors().replace("act_frame[i]], sx, sy);", "act_frame[i]], sx, sy + 1);")
        self.assertEqual(fast_draw(SPRITES, loop_changed)[2]["why"], "loop variant")
        signed = actors().replace("unsigned short act_x", "short act_x")
        self.assertEqual(fast_draw(SPRITES, signed)[2]["why"], "actor table widths")

    def test_only_whitelisted_assembly(self):
        check_forms(loop_asm(16, 159, 151) + loop_asm(16, 159, 151, True))
        for line in ("xor wa, 0xffff", "srl a, hl", "add wa, 0x10"):
            with self.assertRaises(CompileError):
                check_forms([line])


if __name__ == "__main__":
    unittest.main()
