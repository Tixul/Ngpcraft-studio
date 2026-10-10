"""S1 GBVM idle-context bench: the ported scheduler idles cleanly each frame."""
import os
import tempfile
import unittest
from pathlib import Path

from tools.bench_vm_idle import build, observe

TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")
GBVM = os.environ.get("NGC_TEST_GBVM")


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBVM, "set Toshiba/emulator/BIOS/GBVM paths")
class VmIdleBenchTests(unittest.TestCase):
    def test_more_contexts_cost_more_and_stay_idle(self):
        costs = []
        for n in (1, 6):
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / "lab"
                build(dest, GBVM, TOSHIBA, n)
                r = observe(dest, EMULATOR, BIOS, 16)
                self.assertTrue(r["correct"], r["faults"])
                costs.append(r["cycles"]["median"])
        self.assertGreater(costs[1], costs[0])


if __name__ == "__main__":
    unittest.main()
