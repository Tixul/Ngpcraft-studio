"""The journal keeps its scan between writes (save_journal.c sj_scanned).

MegaX v22 (2026-10-13): a title reading 48 variables (Peek Data) rescanned both
flash blocks and re-checked the 4.4 KB record's CRC at each read: 6.8 s of white
screen on the emulator with the cartridge's wait states. The scan is now kept
until the journal writes, and forgotten at boot (random RAM on a console)."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE = (ROOT / "ngpcraft/runtime/save_journal.c").read_text(encoding="utf-8")


def body(name):
    match = re.search(r"\n[^\n]*\b" + name + r"\([^\n]*\)\n\{\n(.*?)\n\}\n", SOURCE, re.S)
    if not match:
        raise AssertionError(f"{name} not found")
    return match.group(1)


class ScanCacheTests(unittest.TestCase):
    def test_find_scans_only_when_needed(self):
        self.assertIn("if (!sj_scanned) sj_scan();", body("ngc_journal_find"))
        self.assertIn("sj_scanned = 1u;", body("sj_scan"))

    def test_every_write_forgets_the_scan(self):
        for name in ("ngc_journal_save", "ngc_journal_clear"):
            with self.subTest(name=name):
                code = body(name)
                self.assertLess(code.index("sj_write("), code.index("sj_scanned = 0u;"))
        # sj_write is the only path that programs or erases the blocks
        writers = [n for n in ("ngc_journal_save", "ngc_journal_clear", "sj_write")
                   if "ngc_flash_program" in body(n) or "ngc_flash_erase" in body(n) or "sj_write(" in body(n)]
        self.assertEqual(sorted(writers), ["ngc_journal_clear", "ngc_journal_save", "sj_write"])

    def test_boot_forgets_the_scan(self):
        self.assertIn("sj_scanned = 0u;", body("ngc_journal_forget"))
        from ngpcraft.gbvm import emit
        text = Path(emit.__file__).read_text(encoding="utf-8")
        self.assertIn('"    ngc_journal_forget();', text)


if __name__ == "__main__":
    unittest.main()
