"""Thirty visible ordinary update scripts: capacity and overload safety."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.pipeline import export_project
from ngpcraft.runtime_units import select_units
from tools.lab_active_rtd import fixture, build, observe
from tools.demo_actor_moves import GBS2

ENV = [os.environ.get(k) for k in
       ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(GBS2.is_dir(), 'GB Studio reference not present')
class ActiveExportTests(unittest.TestCase):
    def test_no_ui_scene_keeps_frame_wait_and_single_sprite_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'case'
            manifest = export_project(fixture(root, 30), root / 'export')
            self.assertNotIn('text', manifest['features'])
            self.assertNotIn('fades', manifest['features'])
            self.assertIn('gbvm_frame_wait', {u.name for u in select_units(manifest['features'])})
            self.assertEqual(len(manifest['scripts']), 31)
            self.assertEqual(manifest['gbvm']['stack_budget']['words_per_context'], 5)
            game = (root / 'export/src/game.c').read_text()
            self.assertLess(game.index('    ngc_actors_render();'), game.index('    ngc_vm_frame_end();'))
            self.assertIn('while (*(volatile UBYTE *)0x8009 >= 152u)',
                          (root / 'export/src/gbvm.c').read_text())
            data = json.loads((root / 'fixture.json').read_text())
            self.assertEqual(data['visible_workers'], 30)
            self.assertEqual(data['sprites_per_actor'], 1)


@unittest.skipUnless(GBS2.is_dir() and all(ENV), 'native prerequisites missing')
class ActiveNativeTests(unittest.TestCase):
    def test_all_visible_pixels_and_negative_witnesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            for witness in (None, 'capacity', 'sync'):
                with self.subTest(witness=witness):
                    root = Path(tmp) / (witness or 'normal')
                    with contextlib.redirect_stdout(io.StringIO()):
                        build(root, 30, ENV[0], witness)
                        result = observe(root, ENV[1], ENV[2], 180)
                    self.assertTrue(result['all_scripts_started'])
                    if witness is None:
                        self.assertTrue(result['pixels_passed'])
                        self.assertTrue(result['all_actors_drawn'])
                        self.assertEqual(result['oam_slots_observed'], [31])
                        self.assertEqual(result['commits_over_vblank'], 0)
                        self.assertEqual(result['commits_started_outside_vblank'], 0)
                        self.assertGreater(result['physical_frames'], result['steady_entries'])
                    elif witness == 'capacity':
                        self.assertFalse(result['pixels_passed'])
                        self.assertFalse(result['all_actors_drawn'])
                    else:
                        # Entry captures can look correct after an unsafe commit.
                        # The external timing gate must detect this omission.
                        self.assertGreater(result['commits_started_outside_vblank'], 0)
