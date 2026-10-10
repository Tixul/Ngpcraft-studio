"""The C save journal (ngpcraft/runtime/save_journal.c) gives the oracle's
flash bytes (tools/save_journal.py) after every operation, power cuts
included (docs/M5_SAVE.md step 2). Clang -> wasm32, replayed by Node."""
import hashlib
import json
import os
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.save_journal import BLOCK, HEADER, PAGE, Chip, Journal, PowerCut

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "ngpcraft/runtime/save_journal.c"
HARNESS = Path(__file__).with_name("save_journal_harness.c")
REPLAY = Path(__file__).with_name("save_journal_replay.js")
CLANG = os.environ.get("NGC_TEST_CLANG")
SIGNATURE = 0x1234ABCD
MAX_DATA = 10 * PAGE - HEADER - 1


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


class Model:
    """Runs the oracle and records the operations for the C side."""

    def __init__(self):
        self.chip, self.ops, self.expected, self.saved = Chip(), [], [], None

    def snapshot(self):
        self.saved = self.chip.copy()
        self.ops.append({"op": "snapshot"})

    def restore(self):
        self.chip = self.saved.copy()
        self.ops.append({"op": "restore"})

    def write(self, slot, data=None, cut=-1, erase=-1):
        op = {"op": "save" if data is not None else "clear", "slot": slot, "cut": cut, "erase": erase}
        if data is not None:
            op.update(data=data.hex())
        self.ops.append(op)
        self.chip.budget = None if cut < 0 else cut
        self.chip.erase_cut = None if erase < 0 else erase
        journal = Journal(self.chip, SIGNATURE)
        try:
            ret = journal.save(slot, data) if data is not None else journal.clear(slot)
        except PowerCut:
            ret = None
        self.chip.budget = self.chip.erase_cut = None
        journal = Journal(self.chip, SIGNATURE)
        self.expected.append({"ret": ret, "flash": hashlib.sha256(bytes(self.chip.blocks[0] + self.chip.blocks[1])).hexdigest(),
                              "find": [digest(journal.load(s)) for s in range(3)]})


@unittest.skipUnless(CLANG and Path(CLANG).exists(), "NGC_TEST_CLANG (clang with wasm32) required")
class JournalCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wasm = Path(cls.tmp.name) / "journal.wasm"
        subprocess.run([CLANG, "--target=wasm32", "-nostdlib", "-O2", "-fno-builtin", "-std=c89", "-Wall", "-Werror",
                        "-Wno-unused-function", "-Wl,--no-entry", "-Wl,--export-all", str(UNIT), str(HARNESS),
                        "-o", str(cls.wasm)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def replay(self, model, extra=0):
        ops = Path(self.tmp.name) / "ops.json"
        ops.write_text(json.dumps(model.ops), encoding="utf-8")
        proc = subprocess.run(["node", str(REPLAY), str(self.wasm), str(ops)], capture_output=True, text=True,
                              timeout=1800)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual(len(got), len(model.expected) + extra)
        for k, (g, e) in enumerate(zip(got, model.expected)):
            self.assertEqual(g["flash"], e["flash"], f"operation {k}")
            self.assertEqual(g["find"], e["find"], f"operation {k}")
            if e["ret"] is not None:
                self.assertEqual(bool(g["ret"]), e["ret"], f"operation {k}")
        return got

    def test_random_operations_and_cuts(self):
        rng = random.Random(21)
        model = Model()
        for _ in range(500):
            slot = rng.randrange(3)
            cut = rng.randrange(0, 3 * MAX_DATA) if rng.random() < 0.25 else -1
            erase = rng.randrange(0, BLOCK) if rng.random() < 0.1 else -1
            if rng.random() < 0.08:
                model.write(slot, None, cut, erase)
            else:
                model.write(slot, bytes(rng.randrange(256) for _ in range(rng.randrange(0, MAX_DATA + 1))), cut, erase)
        self.assertGreater(model.chip.erases, 20)
        self.replay(model)

    def test_faulty_producers_write_nothing(self):
        """A producer that refuses (invalid state) or that emits a different
        length on a later run: the save fails before programming its record."""
        rng = random.Random(5)
        model = Model()
        model.write(0, bytes(rng.randrange(256) for _ in range(700)))
        model.write(1, bytes(rng.randrange(256) for _ in range(300)))
        before = model.expected[-1]
        for mode in (1, 2):
            model.ops.append({"op": "save", "slot": 2, "cut": -1, "erase": -1, "mode": mode,
                              "data": bytes(range(200)).hex()})
            model.expected.append({"ret": False, "flash": before["flash"], "find": before["find"]})
        self.replay(model)

    def test_get_reads_the_found_state(self):
        rng = random.Random(9)
        data = bytes(rng.randrange(256) for _ in range(MAX_DATA))
        model = Model()
        model.write(1, data)
        model.ops.append({"op": "get", "slot": 1, "length": len(data)})
        got = self.replay(model, extra=1)
        self.assertEqual(got[-1], {"get": 1})

    def test_cut_at_every_byte_of_a_roll(self):
        rng = random.Random(3)
        model = Model()
        for slot in (1, 2):
            model.write(slot, bytes(rng.randrange(256) for _ in range(MAX_DATA)))
        while model.chip.erases == 0 or model.chip.blocks[1] == b"\xff" * BLOCK or model.chip.blocks[0] == b"\xff" * BLOCK:
            model.write(0, bytes(rng.randrange(256) for _ in range(600)))
        # Fill until the next big save rolls with an erase.
        new = bytes(rng.randrange(256) for _ in range(MAX_DATA))
        while True:
            probe = model.chip.copy()
            before = probe.erases
            Journal(probe, SIGNATURE).save(0, new)
            if probe.erases > before:
                break
            model.write(0, bytes(rng.randrange(256) for _ in range(600)))
        total = probe.programmed - model.chip.programmed
        model.snapshot()
        for cut in range(total + 1):
            model.restore()
            model.write(0, new, cut)
            model.write(1, bytes([cut & 255]) * 300)
        self.assertGreater(len(model.ops), 2 * total)
        self.replay(model)
        type(self).roll_model = model

    def test_witness_without_rerolling_the_current_block(self):
        """The fix of docs/M5_SAVE.md (erase a block holding no unique data,
        the current one after a cut roll) removed: the comparison sees it."""
        source = UNIT.read_text(encoding="utf-8")
        old = "        target = current;\n        if (!sj_erasable(target)) return 0u;\n"
        self.assertEqual(source.count(old), 1)
        mutant = Path(self.tmp.name) / "mutant.c"
        mutant.write_text(source.replace(old, "        return 0u;\n"), encoding="utf-8")
        wasm = Path(self.tmp.name) / "mutant.wasm"
        subprocess.run([CLANG, "--target=wasm32", "-nostdlib", "-O2", "-fno-builtin", "-std=c89",
                        "-Wl,--no-entry", "-Wl,--export-all", str(mutant), str(HARNESS), "-o", str(wasm)], check=True)
        if not hasattr(type(self), "roll_model"):
            self.test_cut_at_every_byte_of_a_roll()
        saved, self.wasm = self.wasm, wasm
        try:
            with self.assertRaises(AssertionError):
                self.replay(type(self).roll_model)
        finally:
            self.wasm = saved


if __name__ == "__main__":
    unittest.main()
