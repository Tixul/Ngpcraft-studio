import os
from pathlib import Path
import tempfile
import unittest

from tools.lab_gbvm_scheduler import build, observe, const, move, invoke_wait, WAIT_CASES

class SchedulerEncodingTests(unittest.TestCase):
    def test_invoke_wait_encoding(self):
        self.assertEqual(invoke_wait(), [0x0D, 0xFF, 0xFF, 1, 0x12, 0x34, 1])

    def test_big_endian_and_reversed_argument_order(self):
        self.assertEqual(const(-1,0x1234),[0x14,0x12,0x34,0xff,0xff])
        self.assertEqual(move(1,-1),[0x13,0xff,0xff,0,1])

@unittest.skipUnless(all(os.environ.get(n) for n in ("NGC_TEST_TOSHIBA","NGC_TEST_GBVM","NGC_TEST_EMULATOR","NGC_TEST_BIOS")),
                     "set Toshiba/GBVM/emulator/BIOS paths for scheduler ROMs")
class SchedulerRomTests(unittest.TestCase):
    def test_reference_scheduler_and_negative_controls_on_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            for scenario,negative in (("stack","none"),("locks","none"),("stop_locked","none"),("quantum","none"),("stack","endian"),("quantum","quantum")):
                with self.subTest(scenario=scenario,negative=negative):
                    dest=Path(tmp)/(scenario+negative)
                    expected=build(dest,os.environ["NGC_TEST_GBVM"],os.environ["NGC_TEST_TOSHIBA"],scenario,negative)
                    result=observe(dest,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"],expected)
                    self.assertEqual(result["correct"],negative=="none",result["snapshots"])

    def test_wait_frames_and_mutations_on_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            for scenario, negative in WAIT_CASES:
                with self.subTest(scenario=scenario, negative=negative):
                    dest = Path(tmp) / (scenario + negative)
                    expected = build(dest, os.environ["NGC_TEST_GBVM"], os.environ["NGC_TEST_TOSHIBA"], scenario, negative)
                    result = observe(dest, os.environ["NGC_TEST_EMULATOR"], os.environ["NGC_TEST_BIOS"], expected)
                    self.assertEqual(result["correct"], negative == "none", result["snapshots"][:5])
