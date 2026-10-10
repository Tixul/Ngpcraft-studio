"""Background colour variants of a native background (ngpcraft/native_variants.py,
docs/PLAN_MEGAMAN.md L5): key validation, the recolouring rule, and the ROM lab."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.background import compile_background
from ngpcraft.compiler import CompileError
from ngpcraft.native_variants import validate_variants, variant_palettes
from ngpcraft.project import SIDECAR, SCHEMA, ProjectError, load_project
from tools.demo_actor_moves import GBS2
from tools.demo_gbs_scene import write_json

ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]
B, R, G, K = 0xFFF, 0x00F, 0x0F0, 0x000      # backdrop, red, green, black (RGB444)


def picture():
    """16x8: two cells; left uses R+G, right uses G+K, backdrop elsewhere."""
    left = [R if x < 2 else G if x < 4 else B for y in range(8) for x in range(8)]
    right = [G if x < 2 else K if x < 4 else B for y in range(8) for x in range(8)]
    return [(left if x < 8 else right)[y * 8 + x % 8] for y in range(8) for x in range(16)]


def asset(pixels):
    return compile_background(16, 8, pixels, planes=(2,), backdrop=B)


class RecolourRuleTests(unittest.TestCase):
    def test_a_consistent_recolouring_gives_the_new_palettes_and_backdrop(self):
        base = picture()
        swap = {R: K, G: R, K: G, B: 0x777}
        palettes, backdrop = variant_palettes(asset(base), base, [swap[c] for c in base], "v")
        self.assertEqual(backdrop, 0x777)
        original = asset(base).palettes[0]
        self.assertEqual(palettes, tuple(tuple(swap[c] for c in p) for p in original))

    def test_refusals_name_the_tile_and_colours(self):
        base = picture()
        split = [0x456 if c == R else c for c in base]
        split[1] = 0x123                               # one red pixel becomes another colour than the others
        with self.assertRaisesRegex(CompileError, r"tile \(0, 0\): colour 00F .* a variant may only recolour"):
            variant_palettes(asset(base), base, split, "v")
        backdrops = [c if c != B else (0x111 if i % 16 < 8 else 0x222) for i, c in enumerate(base)]
        with self.assertRaisesRegex(CompileError, "the backdrop becomes .* one colour only"):
            variant_palettes(asset(base), base, backdrops, "v")
        hole = [None if c == R else c for c in base]
        with self.assertRaisesRegex(CompileError, "transparent pixel"):
            variant_palettes(asset(base), base, hole, "v")
        with self.assertRaisesRegex(CompileError, "background's size"):
            variant_palettes(asset(base), base, base[:-1], "v")

    def test_transparent_backdrop_pixels_keep_the_backdrop(self):
        base = picture()
        palettes, backdrop = variant_palettes(asset(base), base, [None if c == B else c for c in base], "v")
        self.assertEqual(backdrop, B)
        self.assertEqual(palettes, asset(base).palettes[0])


class KeyValidationTests(unittest.TestCase):
    def test_malformed_lists_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.png").write_bytes(b"")
            for value, message in (([], "1..15"), ([{"name": "x"}], "name"), ([{"name": "", "image": "a.png"}], "non-empty"),
                                   ([{"name": "x", "image": "a.png"}, {"name": "x", "image": "a.png"}], "used twice"),
                                   ([{"name": "x", "image": "../a.png"}], "project-relative"),
                                   ([{"name": "x", "image": "b.png"}], "not found")):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                    validate_variants(value, "w", root)
            validate_variants([{"name": "x", "image": "a.png"}], "w", root)

    @unittest.skipUnless(GBS2.is_dir(), "requires published GB Studio 4.3.2")
    def test_variants_need_native_colours(self):
        from tools.lab_bg_variants import project_case
        with tempfile.TemporaryDirectory() as tmp:
            project = project_case(Path(tmp))
            data = json.loads((project / SIDECAR).read_text(encoding="utf-8"))
            (bg, ext), = data["resources"].items()
            ext.pop("ngpc_color")
            write_json(project / SIDECAR, data)
            with self.assertRaisesRegex(ProjectError, "needs native colours"):
                load_project(project)


@unittest.skipUnless(all(ROM_ENV) and GBS2.is_dir(), "requires Toshiba, native emulator and BIOS")
class VariantRomTests(unittest.TestCase):
    def test_switches_and_fades_are_pixel_exact_and_the_witness_is_detected(self):
        from tools.lab_bg_variants import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / "lab", *ROM_ENV)
            witness = run(Path(tmp) / "witness", *ROM_ENV, witness=True)
        self.assertTrue(result["passed"], result["phases"])
        self.assertFalse(witness["passed"])
        self.assertGreater(witness["phases"][1]["wrong_pixels"], 0)


if __name__ == "__main__":
    unittest.main()
