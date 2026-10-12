"""Background palette recycling (ngpcraft/palette_recycle.py, docs/PALETTE_RECYCLE.md)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.background import compile_background  # noqa: E402
from ngpcraft.background_stream import stream_source  # noqa: E402
from ngpcraft.compiler import CompileError  # noqa: E402
from ngpcraft.palette_recycle import CHUNK, WINDOW, assign, reach  # noqa: E402
from tools.lab_palette_recycle import BACKDROP, H, W, image, run  # noqa: E402

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


class AssignTests(unittest.TestCase):
    def test_one_palette_a_block_needs_sixteen_slots(self):
        # 16 x 5 blocks of 8 x 8 tiles, one palette each: a window spans 4 x 4 blocks.
        w, h = 128, 40
        cells = [(y // CHUNK) * (w // CHUNK) + x // CHUNK for y in range(h) for x in range(w)]
        slots, blocks = assign(w, h, cells, 80)
        self.assertEqual(max(slots) + 1, 16)
        rx, ry = reach(WINDOW[0]), reach(WINDOW[1])
        for (ax, ay), pa in blocks.items():         # no two palettes of one window share a slot
            for (bx, by), pb in blocks.items():
                if (ax, ay) != (bx, by) and abs(ax - bx) <= rx and abs(ay - by) <= ry:
                    self.assertNotEqual(slots[pa[0]], slots[pb[0]])

    def test_too_many_together_is_located(self):
        cells = [n % 17 for n in range(20 * 19)]       # 17 palettes in one screen
        with self.assertRaisesRegex(CompileError, r"around tile \(\d+, \d+\) 17 palettes can be on screen"):
            assign(20, 19, cells, 17)


class CompileTests(unittest.TestCase):
    def test_refused_without_recycle_recycled_with_it(self):
        pixels = image()
        with self.assertRaisesRegex(CompileError, "allocation uses 80 palettes, budget 16"):
            compile_background(W * 8, H * 8, pixels, planes=(2, 1), backdrop=BACKDROP)
        asset = compile_background(W * 8, H * 8, pixels, planes=(2, 1), backdrop=BACKDROP, recycle=True)
        logical, hardware, _ = asset.recycle
        self.assertEqual((len(logical), len(asset.palettes[0])), (80, 16))
        for n, word in enumerate(asset.maps[0]):     # each cell points at its palette's slot
            self.assertLess((word >> 9) & 15, 16)
        source = stream_source(asset)
        self.assertIn("static void ngc_pal_recycle(short tx, short ty)", source)
        self.assertIn("    ngc_pal_recycle(tx, ty);", source)

    def test_editor_plan_reports_the_recycling(self):
        from PIL import Image
        from ngpcraft.native_color import palette_plan
        from tools.demo_gbs_scene import write_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets/backgrounds").mkdir(parents=True)
            img = Image.new("RGB", (W * 8, H * 8))
            img.putdata([((v & 15) * 17, ((v >> 4) & 15) * 17, ((v >> 8) & 15) * 17) for v in image()])
            img.save(root / "assets/backgrounds/big.png")
            write_json(root / "assets/backgrounds/big.png.gbsres", {"_resourceType": "background", "id": "bg-big"})
            plan = palette_plan(root, "bg-big", "000000")
        self.assertEqual(plan["recycle"], {"palettes": 80, "slots": 16})
        self.assertEqual(plan["errors"], [])


@unittest.skipUnless(all(ENV), "needs NGC_TEST_TOSHIBA, NGC_TEST_EMULATOR and NGC_TEST_BIOS")
class RecycleRomTests(unittest.TestCase):
    def test_every_frame_to_the_pixel_and_witness_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run(Path(tmp) / "ok", *ENV)
            self.assertTrue(r["passed"], r)
            self.assertEqual((r["palettes"], r["slots"], r["frames_with_mismatch"]), (80, 16, 0))
            w = run(Path(tmp) / "witness", *ENV, witness=True)
            self.assertFalse(w["passed"])
            self.assertGreater(w["frames_with_mismatch"], 100)


if __name__ == "__main__":
    unittest.main()
