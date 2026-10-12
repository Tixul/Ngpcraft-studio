"""Second background with replaced tiles, colour variants, sprite priority and
palette changes (PLAN_MEGAMAN S3, S4): each lab replayed with a band of the
scene's image on SCR1, and its witness."""
import os
from pathlib import Path
import tempfile
import unittest

ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class SecondCombinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_replaced_tiles_never_animate_the_second_background(self):
        from tools.lab_replace_tiles import run
        self.assertTrue(run(self.root / 'lab', *ENV, second=True)['passed'])
        self.assertFalse(run(self.root / 'witness', *ENV, witness='front-shares', second=True)['passed'])

    def test_colour_variants_recolour_the_main_background_only(self):
        from tools.lab_bg_variants import run
        result = run(self.root / 'lab', *ENV, second=True)
        self.assertTrue(result['passed'], result['phases'])
        self.assertGreater(result['second_background_pixels_kept'], 0)

    def test_priority_sprites_are_behind_both_planes(self):
        from tools.lab_sprite_priority import run
        self.assertEqual(run(self.root / 'lab', *ENV, count=300, second=True)['mismatch_frames'], 0)
        self.assertGreater(run(self.root / 'witness', *ENV, count=300, witness=True, second=True)['mismatch_frames'], 0)

    def test_palette_events_leave_the_second_background_palettes(self):
        from tools.lab_palettes import run
        self.assertEqual(run(self.root / 'lab', *ENV, second=True)['mismatches'], [])
        self.assertTrue(run(self.root / 'witness', *ENV, witness=True, second=True)['mismatches'])


if __name__ == '__main__':
    unittest.main()
