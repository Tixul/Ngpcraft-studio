"""Persistent overlay HUD over a scrolling second background (PLAN_MEGAMAN S3)."""
import os
from pathlib import Path
import tempfile
import unittest

ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class SecondHudTests(unittest.TestCase):
    def test_decor_above_the_hud_is_the_plain_run_and_the_band_stays(self):
        from tools.lab_second_hud import run, compare
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ENV)
        self.assertTrue(result['passed'], result)

    def test_hud_in_front_of_a_main_background_in_front(self):
        # Second background behind: the split brings SCR1 (the HUD) in front.
        from tools.lab_second_hud import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ENV, behind=True)
        self.assertTrue(result['passed'], result)

    def test_wrong_split_line_is_detected(self):
        from tools.lab_second_hud import run
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(run(Path(tmp) / 'lab', *ENV, witness=True)['passed'])


if __name__ == '__main__':
    unittest.main()
