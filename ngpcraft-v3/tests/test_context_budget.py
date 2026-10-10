"""Automatic NGPC VM context count (docs/M4_CONTEXT_BUDGET.md)."""
import unittest

from ngpcraft.gbvm.emit import (CONTEXT_HEADER_BYTES, CONTEXT_RAM_BUDGET, GBVM_CONTEXTS,
                                SAVE_VM_BUDGET, SV_CONTEXT, SV_HEADER, fit_contexts)
from ngpcraft.save_state import check_capacity


class ContextBudgetTests(unittest.TestCase):
    def test_a_fitting_estimate_is_kept_even_below_gbvm(self):
        self.assertEqual(fit_contexts(5, 5), (5, None))
        self.assertEqual(fit_contexts(61, 5), (61, None))      # thirty NPCs (REPRISE R-TD)
        self.assertEqual(fit_contexts(None, 64), (None, None))

    def test_ram_limit_takes_the_largest_pool_that_fits(self):
        contexts, report = fit_contexts(205, 16)
        self.assertEqual(contexts, CONTEXT_RAM_BUDGET // (CONTEXT_HEADER_BYTES + 32))
        self.assertLessEqual(report["ram_bytes"], CONTEXT_RAM_BUDGET)
        self.assertEqual(report["conservative_estimate"], 205)

    def test_never_fewer_than_gbvm(self):
        self.assertEqual(fit_contexts(200, 64)[0], CONTEXT_RAM_BUDGET // (CONTEXT_HEADER_BYTES + 128))
        # 600 saved variables leave room for 4 contexts in the save share: 16 anyway.
        contexts, report = fit_contexts(200, 64, heap_words=600, saves=True)
        self.assertEqual((contexts, report["limits"]["save"]), (GBVM_CONTEXTS, 4))
        self.assertEqual(fit_contexts(12, 64), (12, None))

    def test_a_saving_game_keeps_its_vm_share_of_the_journal(self):
        # Dead Dungeon: 124 heap words, 16-word stacks, saving: 31 contexts.
        contexts, report = fit_contexts(205, 16, heap_words=124, saves=True)
        self.assertEqual(contexts, 31)
        self.assertEqual(report["limits"], {"ram": 62, "save": 31})
        vm = SV_HEADER + SV_CONTEXT * 31 + 2 * (124 + 31 * 16)
        self.assertEqual(report["save_vm_bytes"], vm)
        self.assertLessEqual(vm, SAVE_VM_BUDGET)
        # One more context would exceed the share.
        self.assertGreater(vm + SV_CONTEXT + 32, SAVE_VM_BUDGET)
        check_capacity(SAVE_VM_BUDGET)          # the share alone fits the journal


if __name__ == "__main__":
    unittest.main()
