"""Bank addresses and capacity, independent byte expectations from vm.i."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gbs_fixture import GBS2, event, make_project, number

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble, assemble_bank


def script(name, body):
    return f".module {name}\n_{name}::\n{body}"


class BankTests(unittest.TestCase):
    def test_local_labels_are_relocated_and_scoped_per_script(self):
        first = script("init", "1$:\n        VM_IDLE\n        VM_JUMP 1$\n")
        second = script("update", "1$:\n        VM_SET_CONST VAR_X, 7\n        VM_JUMP 1$\n")
        data, entries, macros, rpn = assemble_bank([first, second], {"VAR_X": 3})
        # vm.i: IDLE=18; JUMP=09 addr(hi,lo); SET_CONST=14 value(hi,lo) index(hi,lo).
        self.assertEqual(data.hex(" "), "18 09 00 00 14 00 07 00 03 09 00 04")
        self.assertEqual([(s.symbol, s.address, len(s.data)) for s in entries],
                         [("init", 0, 4), ("update", 4, 8)])
        self.assertEqual(macros, {"VM_IDLE", "VM_JUMP", "VM_SET_CONST"})
        self.assertEqual(rpn, set())

    def test_unlabelled_script_must_fit_at_its_base(self):
        stop = script("last", "        VM_STOP\n")
        self.assertEqual(assemble(stop, {}, base=65535)[1], b"\x00")
        with self.assertRaisesRegex(CompileError, "exceed the 64 KB bank"):
            assemble(script("last", "        VM_IDLE\n        VM_STOP\n"), {}, base=65535)
        for base in (-1, 65536, 1.5, True):
            with self.subTest(base=base), self.assertRaisesRegex(CompileError, "script base"):
                assemble(stop, {}, base=base)

    def test_full_bank_and_overflow_without_labels(self):
        full = script("full", "        VM_STOP\n" * 65536)
        data, entries, _, _ = assemble_bank([full], {})
        self.assertEqual(len(data), 65536)
        self.assertEqual(entries[0].address, 0)
        with self.assertRaisesRegex(CompileError, r"bank 0 script\[1\].*script base"):
            assemble_bank([full, script("extra", "        VM_STOP\n")], {})
        with self.assertRaisesRegex(CompileError, "exceed the 64 KB bank"):
            assemble(full + "        VM_STOP\n", {})

    def test_duplicate_and_empty_scripts_are_refused(self):
        stop = script("same", "        VM_STOP\n")
        with self.assertRaisesRegex(CompileError, "duplicate script symbol same"):
            assemble_bank([stop, stop], {})
        with self.assertRaisesRegex(CompileError, "empty script empty"):
            assemble_bank([script("empty", "")], {})
        with self.assertRaisesRegex(CompileError, "no scripts"):
            assemble_bank([], {})

    def test_later_script_errors_are_located(self):
        with self.assertRaisesRegex(CompileError, r"bank 0 script\[1\].*undefined label"):
            assemble_bank([script("first", "        VM_STOP\n"),
                           script("second", "        VM_JUMP 1$\n")], {})


@unittest.skipUnless(all(os.environ.get(k) for k in
                        ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS"))
                     and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class BankRomTests(unittest.TestCase):
    def test_entry_and_loop_at_nonzero_bank_address(self):
        from ngpcraft.pipeline import export_project
        from ngpcraft.toolchain import build_rom

        loop = {"id": "loop", "command": "EVENT_LOOP", "args": {}, "children": {"true": [
            event("inc", "EVENT_INC_VALUE", variable="3"),
            event("wait", "EVENT_WAIT", units="frames", frames=number(5))]}}
        # A stopped script before the live script forces a nonzero entry and
        # relocated backward jumps, without adding a new event or runtime API.
        def prefixed(texts, variables, model, **options):
            return assemble_bank([script("prefix", "        VM_STOP\n" * 17), *texts],
                                 variables, model, **options)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_project(root / "project", [loop])
            out = root / "out"
            with patch("ngpcraft.gbvm.emit.assemble_bank", side_effect=prefixed):
                manifest = export_project(project, out)
            entries = list(manifest["scripts"].values())
            self.assertEqual(sorted(s["address"] for s in entries), [0, 17])
            self.assertTrue(all(s["bank"] == 0 for s in entries))
            self.assertIn("ngc_vm_start(ngc_banks, 0u, 17u);",
                          (out / "src/game.c").read_text(encoding="utf-8"))
            build_rom(out, os.environ["NGC_TEST_TOSHIBA"])
            probe = root / "probe"
            subprocess.run([sys.executable, "-B", "-m", "tools.probe_gbvm", str(out),
                            "--emulator", os.environ["NGC_TEST_EMULATOR"],
                            "--bios", os.environ["NGC_TEST_BIOS"], "--out", str(probe),
                            "--frames", "12"], capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        self.assertEqual(changes["VAR_QUEST4"], [{"frame": 0, "value": 0},
                         {"frame": 1, "value": 1}, {"frame": 6, "value": 2},
                         {"frame": 11, "value": 3}])


if __name__ == "__main__":
    unittest.main()
