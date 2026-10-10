"""Native glyph/mask conversion, slot reassignment and modal VBlank budgets."""
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.background import encode_tile
from ngpcraft.compiler import CompileError
from ngpcraft.ui_text import combined_text_rows
from tools.lab_modal_sprite_stress import build_case, observe


class TextRowTests(unittest.TestCase):
    def test_all_opaque_pairs_against_the_tile_encoder(self):
        for ink in (1, 2, 3):
            for paper in (1, 2, 3):
                rows = combined_text_rows(ink, paper)
                for row, actual in enumerate(rows):
                    pixels = [ink if row & (128 >> x) else paper for x in range(8)]
                    self.assertEqual(actual, int.from_bytes(encode_tile(pixels * 8)[:2], 'little'))
        for value in (0, 4, True, 1.5):
            with self.assertRaises(CompileError):
                combined_text_rows(value, 1)


ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class ModalSpriteRomTests(unittest.TestCase):
    def run_case(self, *, rows=4, witness=None, all_animated=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, _ = build_case(root, Path(ENV[0]), rows=rows, witness=witness,
                                     all_animated=all_animated)
            result = observe(root, Path(ENV[1]), Path(ENV[2]))
            self.assertEqual(manifest['scene_engine']['background_priority']['status'], 'emitted_dynamic')
            self.assertGreater(result['modal_frames'], 300)
            self.assertGreater(result['text']['priority_pixels_checked'], 0)
            self.assertGreater(len(result['oam_lengths']), 2)
            return result

    def test_changing_frames_reassign_slots_with_four_row_dialogue(self):
        result = self.run_case()
        self.assertTrue(result['pixels_passed'])
        self.assertTrue(result['commits_passed'])
        self.assertTrue(result['cadence_passed'], result['modal_missed_frames'])

    def test_maximum_dialogue_and_all_empty_animation_phase(self):
        result = self.run_case(rows=11, all_animated=True)
        self.assertIn(0, result['oam_lengths'])
        self.assertTrue(result['pixels_passed'])
        self.assertTrue(result['commits_passed'])
        self.assertTrue(result['cadence_passed'], result['modal_missed_frames'])

    def test_missing_source_key_is_detected(self):
        result = self.run_case(witness='cache_key')
        self.assertFalse(result['pixels_passed'])
        self.assertTrue(result['sprite_frames_with_mismatch'])

    def test_missing_tile_commit_is_detected(self):
        result = self.run_case(witness='commit')
        self.assertFalse(result['pixels_passed'])
        self.assertTrue(result['sprite_frames_with_mismatch'])
