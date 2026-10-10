"""Porte M2 : bas des sprites = haut des tuiles de sol, mesuré en pixels (tools.lab_foot_ground)."""
import os
from pathlib import Path
import tempfile
import unittest

from tools.lab_foot_ground import build_case, run, FACES

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]
SHIFT = ("src/scene_actors.c", "sy = (short)(act_y[i] >> 5);", "sy = (short)(act_y[i] >> 5) + 1;")


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class FootGroundTests(unittest.TestCase):
    def report(self, sheet=None, mutate=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, ENV[0], sheet, mutate)
            return run(root, ENV[1], ENV[2])

    def check(self, report, up_gap):
        self.assertTrue(report["passed"], report["bad"][:3])
        self.assertEqual(report["probe"]["frames_with_mismatch"], [])
        self.assertEqual(report["probe"]["dropped_frames"], [])
        self.assertEqual(set(report["faces"]), set(FACES.values()))
        # The gate: lowest drawn row + 1 = top row of the floor tile, at two cameras.
        self.assertEqual(report["down_gaps"], [0])
        self.assertEqual(len(report["faces"]["down"]), 2)
        self.assertEqual({c["measured_gap_px"] for c in report["contacts"] if c["face"] == "up"}, {up_gap})

    def test_player_feet_box(self):
        # gbs2 player.png: box 16x8 at the feet; the head overlaps the wall above
        # by 8 px minus the art's one transparent row, as in GB Studio.
        self.check(self.report(), -7)

    def test_full_box_sprite(self):
        # npc001.png: box 16x16 = the whole canvas; it touches walls on every side.
        self.check(self.report("npc001"), 0)

    def test_witness_drawing_one_pixel_low(self):
        report = self.report(mutate=SHIFT)
        self.assertFalse(report["passed"])
        self.assertEqual(report["down_gaps"], [-1])
        self.assertTrue(report["bad"])


if __name__ == "__main__":
    unittest.main()
