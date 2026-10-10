import os
from pathlib import Path
import tempfile
import unittest

from tools.bench_dispatch import build, observe

TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMU = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


@unittest.skipUnless(TOSHIBA and EMU and BIOS, "set Toshiba/emulator/BIOS paths for S1 dispatch benchmark")
class DispatchBenchTests(unittest.TestCase):
    def test_three_variants_have_correct_observable_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("baseline", "specialized", "interpreted"):
                with self.subTest(mode=mode):
                    dest = Path(tmp) / mode
                    build(dest, TOSHIBA, mode)
                    result = observe(dest, EMU, BIOS, mode, 16)
                    self.assertTrue(result["correct"], result["samples"])
                    self.assertGreater(result["cycles_per_iteration"]["min"], 0)

    def test_changed_operation_is_detected_by_reference_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, TOSHIBA, "specialized", corrupt=True)
            result = observe(tmp, EMU, BIOS, "specialized", 16)
            self.assertFalse(result["correct"])
