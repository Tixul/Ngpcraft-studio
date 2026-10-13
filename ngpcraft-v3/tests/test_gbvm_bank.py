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
from ngpcraft.gbvm.assembler import assemble, assemble_bank, assemble_banks


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


def placed(name, body):
    """A script as scriptBuilderBase writes it: its own bank placeholder first."""
    return f".module {name}\n___bank_{name} = 255\n.globl ___bank_{name}\n\n_{name}::\n{body}"


class SeveralBanksTests(unittest.TestCase):
    """docs/PLAN_MULTI_BANQUES.md É1. Bytes from vm.i: STOP=00, PUSH_CONST=01
    value(hi,lo), JUMP=09 addr(hi,lo), CALL_FAR=0A addr(hi,lo) bank,
    RET_FAR=0B 00, IDLE=18, CONTEXT_PREPARE=55 addr(hi,lo) bank slot."""

    def test_scripts_fill_banks_in_order_and_far_calls_carry_the_bank(self):
        init = placed("init", "        VM_CALL_FAR ___bank_lib, _lib\n        VM_STOP\n")       # 5 bytes
        update = placed("update", "1$:\n        VM_IDLE\n        VM_JUMP 1$\n")                 # 4 bytes
        lib = placed("lib", "        VM_CONTEXT_PREPARE 1, ___bank_update, _update\n"
                            "        VM_PUSH_CONST ___bank_init\n        VM_RET_FAR\n")         # 10 bytes
        banks, entries, _, _ = assemble_banks([init, update, lib], {}, bank_bytes=12)
        self.assertEqual([(e.symbol, e.bank, e.address) for e in entries],
                         [("init", 0, 0), ("update", 0, 5), ("lib", 1, 1)])
        self.assertEqual(banks[0].hex(" "), "0a 00 01 01 00 18 09 00 05")
        # Bank 1: reserved VM_STOP at 0, then lib at offset 1.
        self.assertEqual(banks[1].hex(" "), "00 55 00 05 00 01 01 00 00 0b 00")

    def test_local_labels_are_offsets_in_their_own_bank(self):
        filler = placed("filler", "        VM_STOP\n" * 6)
        loop = placed("loop", "        VM_STOP\n1$:\n        VM_IDLE\n        VM_JUMP 1$\n")
        banks, entries, _, _ = assemble_banks([filler, loop], {}, bank_bytes=8)
        self.assertEqual([(e.bank, e.address) for e in entries], [(0, 0), (1, 1)])
        self.assertEqual(banks[1].hex(" "), "00 00 18 09 00 02")

    def test_a_script_ending_exactly_at_the_bank_end_stays_there(self):
        texts = [placed("a", "        VM_STOP\n" * 5), placed("b", "        VM_STOP\n" * 3),
                 placed("c", "        VM_STOP\n")]
        banks, entries, _, _ = assemble_banks(texts, {}, bank_bytes=8)
        self.assertEqual([(e.bank, e.address) for e in entries], [(0, 0), (0, 5), (1, 1)])
        self.assertEqual([len(b) for b in banks], [8, 2])

    def test_too_large_scripts_are_refused_never_split(self):
        with self.assertRaisesRegex(CompileError, r"script\[0\] big: 9 bytes do not fit a script bank of 8"):
            assemble_banks([placed("big", "        VM_STOP\n" * 9)], {}, bank_bytes=8)
        # Bank 1.. keep byte 0: 8 bytes fit bank 0 alone, not a later bank.
        with self.assertRaisesRegex(CompileError, r"script\[1\] full: 8 bytes do not fit"):
            assemble_banks([placed("a", "        VM_STOP\n"), placed("full", "        VM_STOP\n" * 8)], {},
                           bank_bytes=8)
        with self.assertRaisesRegex(CompileError, "script bank size"):
            assemble_banks([placed("a", "        VM_STOP\n")], {}, bank_bytes=65537)

    def test_one_bank_is_what_assemble_bank_gives(self):
        texts = [placed("init", "        VM_CALL_FAR ___bank_lib, _lib\n        VM_STOP\n"),
                 placed("lib", "        VM_RET_FAR\n")]
        banks, entries, macros, rpn = assemble_banks(texts, {})
        self.assertEqual((banks[0], entries, macros, rpn), assemble_bank(texts, {}))
        self.assertEqual(len(banks), 1)
        self.assertTrue(all(e.bank == 0 for e in entries))

    def test_placement_is_deterministic(self):
        texts = [placed(f"s{i}", "        VM_STOP\n" * (1 + i % 5)) for i in range(40)]
        first = assemble_banks(texts, {}, bank_bytes=16)
        self.assertEqual(first, assemble_banks(texts, {}, bank_bytes=16))
        self.assertGreater(len(first[0]), 1)
        for e in first[1]:
            self.assertEqual(first[0][e.bank][e.address:e.address + len(e.data)], e.data)
            self.assertNotEqual((e.bank > 0, e.address), (True, 0))


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
            return assemble_banks([script("prefix", "        VM_STOP\n" * 17), *texts],
                                  variables, model, **options)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_project(root / "project", [loop])
            out = root / "out"
            with patch("ngpcraft.gbvm.emit.assemble_banks", side_effect=prefixed):
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
