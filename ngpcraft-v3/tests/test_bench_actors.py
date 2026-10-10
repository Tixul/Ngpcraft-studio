"""S1 actors hot path: both variants match the oracle; a corruption is caught."""
import os
import tempfile
import unittest
from pathlib import Path

from tools.bench_actors import Oracle, build, observe

TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


class OracleTests(unittest.TestCase):
    def test_oracle_bounces_and_animates(self):
        o = Oracle(2)
        first = o.tick()
        self.assertEqual(first[2], 12)          # 3 actors x 4 hardware sprites
        for _ in range(7):
            o.tick()
        self.assertEqual(o.frame, [1, 1, 1])    # tick 8 (time 0) toggled once


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS, "set Toshiba/emulator/BIOS paths")
class ActorBenchTests(unittest.TestCase):
    def run_variant(self, variant, corrupt=False):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "lab"
            build(dest, TOSHIBA, variant, 3, corrupt)
            return observe(dest, EMULATOR, BIOS, 3, 24)

    def test_specialized_matches_oracle(self):
        self.assertTrue(self.run_variant("A-specialized")["correct"])

    def test_gbvm_port_matches_oracle(self):
        self.assertTrue(self.run_variant("D-gbvm")["correct"])

    def test_corruption_is_detected(self):
        self.assertFalse(self.run_variant("D-gbvm", corrupt=True)["correct"])


if __name__ == "__main__":
    unittest.main()
