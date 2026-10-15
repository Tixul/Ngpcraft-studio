"""vm_rpn in assembly (ngpc.engine_speedups, docs/ETUDE_CADENCE.md §6.6). The game is proven the
same image for image by tools/lab_speedups; here: off leaves the source untouched, on keeps the
original C as the hand-over, the assembly uses only listed forms, and a ROM computes random
expressions exactly as the C does (tools/lab_rpn_diff, with a planted fault as witness)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gbs_fixture import GBS2
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm import rpn_fast
from ngpcraft.gbvm.assembler import load_model
from ngpcraft.gbvm.emit import RUNTIME, gbvm_source

CASES = {"VM_OP_INT8", "VM_OP_INT16", "VM_OP_REF", "VM_OP_REF_SET", "VM_OP_ADD", "VM_OP_LT", "VM_OP_DIV",
         "VM_OP_SHL", "VM_OP_REF_MEM"}
TOOLS = ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")


def runtime():
    return json.loads(RUNTIME.read_text(encoding="utf-8"))


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class RpnFastTests(unittest.TestCase):
    def source(self, speedups, cases=CASES, constant_pairs=0):
        return gbvm_source(runtime(), {"VM_RPN"}, set(cases), [], ["game_time"], load_model(),
                           speedups=speedups, constant_pairs=constant_pairs)[0]

    def test_off_is_the_source_before(self):
        source = self.source(False)
        self.assertNotIn("ngc_rpn_c", source)
        self.assertNotIn("__asm", source)
        self.assertEqual(source.count("static void vm_rpn(SCRIPT_CTX * THIS) {\n\n    INT16 * A, * B, * ARGS;"), 1)

    def test_on_keeps_the_original_c_for_the_other_cases(self):
        source = self.source(True)
        self.assertEqual(source.count("static void ngc_rpn_c(SCRIPT_CTX * THIS, INT16 * ARGS) {"), 1)
        self.assertNotIn("ARGS = sp;", source)
        # The original cases stay in the C, the assembly hands over DIV and SHL.
        c = source[source.index("static void ngc_rpn_c("):source.index("static void vm_rpn(")]
        for case in CASES:
            self.assertIn(f"case {case}", c)
        asm = source[source.index("static void vm_rpn("):]
        self.assertIn('__asm("cal _ngc_rpn_c");', asm)
        self.assertIn('__asm("j eq, ngc_rpn_10");', asm)       # VM_OP_ADD in assembly
        self.assertNotIn("ngc_rpn_13:", asm)                    # VM_OP_DIV not
        self.assertNotIn("ngc_rpn_18:", asm)                    # VM_OP_SHL not (shift by A stays C)
        self.assertNotIn("ngc_rpn_20:", asm)                    # VM_OP_MIN unused: absent
        self.assertLess(source.index("static UBYTE * ngc_memory("), source.index("static void vm_rpn("))

    def test_constant_pairs_keep_their_interpreter(self):
        cases = {"VM_OP_INT16", "VM_OP_REF_SET"}
        self.assertEqual(self.source(True, cases, 2), self.source(False, cases, 2))

    def test_every_form_listed_and_variants_refused(self):
        values = rpn_fast._values(runtime()["c_defines"])
        lines = rpn_fast.rpn_asm(set(rpn_fast.FAST), values)
        rpn_fast.check_forms(lines)
        for line in lines:
            self.assertNotIn("djnz", line)
            self.assertFalse(line.startswith(("sll a", "srl a", "sla a", "sra a")), line)
        with self.assertRaises(CompileError):
            rpn_fast.check_forms(["ld bc, (-xiy:2)"])          # pre-decrement: cc900 never emits it
        same, report = rpn_fast.fast_rpn("static void vm_rpn(SCRIPT_CTX * THIS) {}", CASES,
                                         runtime()["c_defines"], runtime()["context"])
        self.assertEqual((same, report["fast_rpn"]), ("static void vm_rpn(SCRIPT_CTX * THIS) {}", False))


@unittest.skipUnless(all(os.environ.get(k) for k in TOOLS) and GBS2.is_dir(), "toolchain, emulator and BIOS required")
class RpnFastRomTests(unittest.TestCase):
    def run_lab(self, seed):
        from tools.lab_rpn_diff import run
        out = Path(tempfile.mkdtemp(prefix="ngc_rpn_")) / "lab"
        return run(out, seed, 120, *(os.environ[k] for k in TOOLS))

    def test_random_expressions_match_the_c(self):
        result = self.run_lab(11)
        self.assertEqual(result["assembly"], (True, False))
        self.assertEqual(result["differences"], {})
        self.assertGreater(result["distinct_results"], 30)

    def test_witness_planted_fault_is_seen(self):
        original = rpn_fast._case
        with mock.patch.object(rpn_fast, "_case", lambda name, tag: [
                line.replace("scc lt, wa", "scc le, wa").replace("add wa, (xiy-0x2)", "sub wa, (xiy-0x2)")
                for line in original(name, tag)]):
            result = self.run_lab(11)
        self.assertTrue(result["differences"])


if __name__ == "__main__":
    unittest.main()
