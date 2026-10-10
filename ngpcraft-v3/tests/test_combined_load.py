"""M2 combined load: second decor + dialogues + walking player + moving actors (tools.lab_combined_load)."""
import os
from pathlib import Path
import tempfile
import unittest

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class CombinedLoadRomTests(unittest.TestCase):
    def run_lab(self, workers):
        from tools.lab_combined_load import build_case, run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, Path(ENV[0]), workers)
            return run(root, Path(ENV[1]), Path(ENV[2]))

    def test_seven_actors_no_frame_missed_in_play_and_pixels_exact(self):
        report = self.run_lab(7)
        self.assertTrue(report["check_passed"], report["background_errors"])
        self.assertEqual(report["missed_normal"], [])
        self.assertEqual(report["commits_outside_vblank"], [])
        self.assertEqual(report["commits_overrunning_vblank"], [])
        self.assertGreater(report["camera_positions"], 40)       # the player walked: both planes scrolled
        self.assertGreater(report["front_positions"], 20)
        self.assertGreater(report["second_pixels_checked"], 0)
        self.assertGreaterEqual(report["dialogues"], 4)
        self.assertEqual(report["max_sprites"], 36)
        # Known text cost, independent of the actors (docs/M2_COMBINED_LOAD.md):
        # an instantly drawn page costs more than one frame, once per page change.
        self.assertEqual(len(report["missed_modal"]), 4)

    def test_witness_ten_actors_miss_frames_with_exact_pixels(self):
        report = self.run_lab(10)
        self.assertTrue(report["check_passed"])
        self.assertTrue(report["missed_normal"])
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
