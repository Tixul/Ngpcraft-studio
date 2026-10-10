"""S2 flash laboratory: addressing, append, refusal, and a negative control.

Emulator checks prove addressing/data/refusal logic only; the emulator does not
model flash erase/program timing (see docs/S2_FLASH.md). Console runs are separate.
"""
import os
import tempfile
import unittest
from pathlib import Path

from tools.lab_flash_s2 import build, check

TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS, "set Toshiba/emulator/BIOS paths")
class FlashLabTests(unittest.TestCase):
    def run_lab(self, mode, negative=False):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "lab"
            build(out, TOSHIBA, mode, negative)
            return check(out, mode, EMULATOR, BIOS)

    def test_read_only_detects_three_sizes_and_writes_nothing(self):
        report = self.run_lab("read")
        self.assertTrue(report["passed"], report["scenarios"])
        self.assertEqual(len(report["scenarios"]), 3)

    def test_append_three_power_ons_and_full_block_refused(self):
        report = self.run_lab("append")
        self.assertTrue(report["passed"], report["scenarios"])

    def test_negative_control_is_detected(self):
        report = self.run_lab("append", negative=True)
        self.assertFalse(report["passed"])
        self.assertFalse(report["scenarios"][0]["ok"])
        # Refusal logic does not depend on the page marker and must still hold.
        self.assertTrue(report["scenarios"][-1]["ok"])


if __name__ == "__main__":
    unittest.main()
