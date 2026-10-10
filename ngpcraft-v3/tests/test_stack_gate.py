import os
from pathlib import Path
import tempfile
import unittest

from tools.lab_stack_gate import build
from tools.probe_stack import observe

TOSHIBA=os.environ.get("NGC_TEST_TOSHIBA")
EMU=os.environ.get("NGC_TEST_EMULATOR")
BIOS=os.environ.get("NGC_TEST_BIOS")

@unittest.skipUnless(TOSHIBA and EMU and BIOS,"set Toshiba/emulator/BIOS paths for stack gate")
class StackGateTests(unittest.TestCase):
    def test_deep_stack_is_measured_and_broken_roms_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode,reason in (("deep",None),("bad_pc","pc-outside-linked-code"),("bad_sp","stack-outside-free-user-ram")):
                with self.subTest(mode=mode):
                    dest=Path(tmp)/mode
                    build(dest,TOSHIBA,mode)
                    result=observe(dest,EMU,BIOS,instructions=100_000)
                    self.assertEqual(result["passed"],reason is None,result)
                    if reason:
                        self.assertEqual(result["failure"]["reason"],reason,result)
                    else:
                        self.assertGreaterEqual(result["observed_user_stack_bytes"],512)
                        self.assertGreater(result["tick_caller_returns"],0)
                        self.assertEqual(int.from_bytes(bytes.fromhex(result["scratch_final_hex"]),"little"),520)

    def test_margin_gate_stops_before_allocation_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp,TOSHIBA,"deep")
            result=observe(tmp,EMU,BIOS,instructions=100_000,minimum_margin=3000)
            self.assertFalse(result["passed"])
            self.assertEqual(result["failure"]["reason"],"stack-margin")
            self.assertGreater(result["failure"]["sp"],result["highest_reserved_end"])
