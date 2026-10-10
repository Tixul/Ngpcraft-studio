"""Static VM stack bounds vs. heights observed on ROM (tools.probe_vm_stacks)."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class VmStackBoundRomTests(unittest.TestCase):
    def measure(self, build, ticks):
        from tools.probe_vm_stacks import measure
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            root.mkdir()
            build(root)
            return measure(root, Path(ENV[0]), Path(ENV[1]), Path(ENV[2]), ticks, Path(tmp) / "wide")

    def custom_events(self, by_reference=False):
        from tools.demo_custom_events import build_case
        return self.measure(lambda r: build_case(r, ENV[0], ENV[1], ENV[2], by_reference=by_reference), 300)

    def test_far_calls_by_value_bound_is_reached_exactly(self):
        result = self.custom_events()
        self.assertEqual(result["fallbacks"], {})
        self.assertTrue(result["within_bound"], result["observed_words"])
        self.assertEqual(result["max_observed_words"], result["static_bound_words"])
        self.assertLess(result["static_bound_words"], 64)

    def test_far_calls_by_reference_within_bound(self):
        result = self.custom_events(by_reference=True)
        self.assertEqual(result["fallbacks"], {})
        self.assertTrue(result["within_bound"], result["observed_words"])

    def test_witness_call_frame_one_word_short(self):
        # The analysis forgetting the bank word of vm_call_far.
        # Whether the overflow then visibly breaks the ROM depends on what lies
        # next in RAM (it did before the 2026-10-05 VM changes, no longer does):
        # the measured height is the deterministic verdict.
        from ngpcraft.pipeline import export_project
        from tools.demo_custom_events import project_case
        from tools.probe_vm_stacks import observe, widened
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("ngpcraft.gbvm.stack_budget.CALL_FRAME", 1):
                short = export_project(project_case(root / "bound"), root / "bound/export")
            bound = short["gbvm"]["stack_budget"]["words_per_context"]
            wide = widened(root / "bound/export", root / "wide", Path(ENV[0]))
            result = observe(wide, Path(ENV[1]), Path(ENV[2]), 300)
            # Two nested calls: one word short at each frame.
            self.assertEqual(result["max_observed_words"], bound + 2)

    def test_text_dialogues_closed_by_a_within_bound(self):
        from tools.demo_text_switch import build_case
        result = self.measure(lambda r: build_case(r, Path(ENV[0])), 1000)
        self.assertEqual(result["inputs"], "inputs.json")
        self.assertEqual(result["fallbacks"], {})
        self.assertTrue(result["within_bound"], result["observed_words"])
        self.assertEqual(result["max_observed_words"], result["static_bound_words"])


if __name__ == "__main__":
    unittest.main()
