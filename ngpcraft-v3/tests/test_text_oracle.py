"""Regression tests for bounded dialogue replay and BIOS input latency."""
import unittest
from types import SimpleNamespace
from tools.check_text import dialogues

class TextOracleTests(unittest.TestCase):
    def font(self):
        return SimpleNamespace(table=[0] * 128, data=bytes([255] * 16),
                               widths=[8], is_variable_width=False, is_1bit=True)

    def test_recording_ends_while_waiting_for_a(self):
        states = dialogues([1], [b"A", b"B"], self.font(), lambda k: 0, 40, 4)
        self.assertEqual(set(states), set(range(41)))
        self.assertEqual(states[40][0], 112)

    def test_bios_delayed_press_moves_close_two_frames(self):
        def replay(delay):
            return dialogues([1], [b"A"], self.font(),
                             lambda k: 16 if 30 + delay <= k < 33 + delay else 0, 50, 4)
        immediate, delayed = replay(0), replay(2)
        self.assertEqual(immediate[31][0], 114)
        self.assertEqual(delayed[31][0], 112)
        self.assertEqual(delayed[33][0], immediate[31][0])

if __name__ == "__main__":
    unittest.main()
