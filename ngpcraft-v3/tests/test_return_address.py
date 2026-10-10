import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.lab_return_address import build

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS, "set Toshiba/emulator/BIOS paths for S1 ABI lab")
class ReturnAddressRomTests(unittest.TestCase):
    def test_wide_returns_pass_and_truncated_returns_fail_on_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            for negative, color in ((False, 240), (True, 15)):
                with self.subTest(negative=negative):
                    dest = Path(tmp) / ("negative" if negative else "candidate")
                    build(dest, TOSHIBA, negative)
                    subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_rom.py"), str(dest),
                                    "--emulator", EMULATOR, "--bios", BIOS, "--out", str(dest / "probe"),
                                    "--frames", "5"], capture_output=True, check=True, timeout=90)
                    report = json.loads((dest / "probe/probe.json").read_text())
                    self.assertTrue(report["game_entry_reached"])
                    self.assertEqual(report["samples"][4]["pixel_colors"], [color])
