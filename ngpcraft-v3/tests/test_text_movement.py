"""Native modal freeze and moving A3/A4 cache/camera proof against GBVM."""
import os
from pathlib import Path
import tempfile
import unittest
from tools.lab_text_movement import build_case, compare_movement
from tools.lab_modal_sprite_stress import observe

ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]

@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class TextMovementRomTests(unittest.TestCase):
    def run_case(self, *, rows=11, witness=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            build_case(root,Path(ENV[0]),rows=rows,witness=witness)
            return observe(root,Path(ENV[1]),Path(ENV[2]),frames=680,comparator=compare_movement)

    def check_valid(self, result):
        self.assertTrue(result['pixels_passed'], result['sprite_frames_with_mismatch'][:3])
        self.assertTrue(result['commits_passed'])
        self.assertTrue(result['cadence_passed'])
        self.assertFalse(result['scroll_frames_with_mismatch'])
        self.assertIn(0,result['oam_lengths'])
        self.assertGreater(len(result['player_positions']),100)
        self.assertGreater(result['text']['priority_pixels_checked'],1000)

    def test_modal_freeze_then_motion_with_four_row_dialogue(self):
        self.check_valid(self.run_case(rows=4))

    def test_modal_freeze_then_motion_with_maximum_dialogue(self):
        self.check_valid(self.run_case())

    def test_cache_ignoring_position_is_detected(self):
        result=self.run_case(witness='position')
        self.assertFalse(result['pixels_passed'])
        self.assertTrue(result['sprite_frames_with_mismatch'])
        self.assertFalse(result['scroll_frames_with_mismatch'])

    def test_motion_inside_modal_loop_is_detected(self):
        result=self.run_case(witness='modal_motion')
        self.assertFalse(result['pixels_passed'])
        self.assertTrue(result['scroll_frames_with_mismatch'])
        self.assertLess(result['scroll_frames_with_mismatch'][0]['frame'], result['oracle_resume_tick'])
