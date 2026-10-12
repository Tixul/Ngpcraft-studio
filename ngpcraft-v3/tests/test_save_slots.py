"""The save journal sized for the slots the scripts name (docs/M5_SAVE.md, save_state.used_slots)."""
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.save_state import check_capacity, max_payload, used_slots


class SaveSlotsTests(unittest.TestCase):
    def test_highest_slot_named(self):
        self.assertEqual(used_slots("    VM_RAISE EXCEPTION_SAVE, 1\n    .SAVE_SLOT 0\n"), 1)
        self.assertEqual(used_slots("    VM_SAVE_PEEK .LOCAL_TMP0, VAR_A, VAR_A, 1, 1\n"), 2)
        self.assertEqual(used_slots("    VM_SAVE_CLEAR 2\n    .SAVE_SLOT 0\n"), 3)
        # no slot named (only Load's poll): all three, as before
        self.assertEqual(used_slots("    VM_POLL_LOADED .LOCAL_TMP0\n"), 3)
        with self.assertRaises(CompileError):
            used_slots("    .SAVE_SLOT 3\n")

    def test_capacity_per_slot_count(self):
        self.assertEqual(max_payload(3), 2537)       # the previous fixed limit
        self.assertEqual(max_payload(1), 7913)
        self.assertEqual(check_capacity(2542, slots=1), 11)
        with self.assertRaisesRegex(CompileError, "3 slots plus recovery need 34 pages.*2537"):
            check_capacity(2542)
        with self.assertRaisesRegex(CompileError, "1 slot plus recovery"):
            check_capacity(7914, slots=1)


if __name__ == "__main__":
    unittest.main()
