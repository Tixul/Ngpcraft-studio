"""Case B modal text: resource budgets, selection, native pixels and R2."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.emit_ui import shared_map_support, check_shared_commands, check_tiles
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.check_text import check
from tools.demo_gbs_scene import write_json
from tools.demo_text import PAGES, project_case
from tools.demo_text_switch import project_case as switch_project

ROOT = Path(__file__).resolve().parents[1]


class SharedTextExportTests(unittest.TestCase):
    def test_physical_ui_rows_have_an_explicit_budget(self):
        for rows in (0, 12, True, 1.5):
            with self.subTest(rows=rows), self.assertRaises(CompileError):
                shared_map_support(rows)
        # GBVM pointer arithmetic retains a complete map; physical upload is
        # separately limited to the free rows, and only the visible rows.
        source = shared_map_support(11)
        self.assertIn('ngc_ui_map[1024]', source)
        self.assertIn('y < visible_rows', source)
        with self.assertRaisesRegex(CompileError, 'town.*12 rows.*11'):
            check_shared_commands('VM_OVERLAY_CLEAR 0, 0, 20, 12, 1, 1', 'scene town')

    def test_dynamic_cache_must_leave_text_tiles_free(self):
        report = {'background': {'tile_base': 0, 'tile_count': 100}, 'sprite_tiles': 100,
                  'dynamic_priority': {'cache_tile_base': 360, 'cache_tiles': 24}}
        check_tiles(report, 'scene town')
        report['dynamic_priority']['cache_tiles'] = 25
        with self.assertRaisesRegex(CompileError, 'scene town.*text needs tiles'):
            check_tiles(report, 'scene town')

    def test_case_b_selects_raster_and_sprite_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root, case_b=True)
            manifest = export_project(project, root / 'export')
            self.assertIn('text_shared', manifest['features'])
            self.assertGreater(manifest['scene_engine']['ui_palette_base'], 0)
            self.assertEqual(manifest['scene_engine']['planes']['raster_status'], 'emitted')
            gbvm = (root / 'export/src/gbvm.c').read_text()
            self.assertIn('ngc_ui_map[1024]', gbvm)
            self.assertIn('ngc_ui_map_commit(line)', gbvm)
            self.assertIn('ngc_split_init()', gbvm)
            sprites = (root / 'export/src/scene_sprites.c').read_text()
            self.assertIn('ngc_ui_sprite_cut()', sprites)
            self.assertIn('0xE7FFu', sprites)
            self.assertEqual(manifest['scene_engine']['background_priority']['status'], 'emitted_dynamic')
            self.assertEqual(manifest['scene_engine']['dynamic_priority']['cache_tiles'], 40)
            self.assertIn('ngc_mask_draw(', sprites)
            self.assertIn('ngc_mask_reset();', sprites)
            self.assertLess(sprites.index('ngc_mask_copy();'), sprites.rindex('ngc_soam_copy();'))

    def test_case_a_has_no_shared_map_interrupt_or_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            manifest = export_project(project, root / 'export')
            self.assertNotIn('text_shared', manifest['features'])
            self.assertNotIn('dynamic_priority', manifest['scene_engine'])
            self.assertNotIn('ngc_mask_', (root / 'export/src/scene_sprites.c').read_text())
            gbvm = (root / 'export/src/gbvm.c').read_text()
            for name in ('ngc_ui_map', 'ngc_split_', 'ngc_ui_sprite_cut'):
                self.assertNotIn(name, gbvm)

    def test_case_b_without_text_has_no_shared_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root, case_b=True)
            path = next(project.rglob('scene.gbsres'))
            data = json.loads(path.read_text(encoding='utf-8'))
            data['script'][0]['children']['true'] = data['script'][0]['children']['true'][1:]
            write_json(path, data)
            manifest = export_project(project, root / 'export')
            self.assertNotIn('text', manifest['features'])
            self.assertNotIn('text_shared', manifest['features'])
            self.assertNotIn('ngc_ui_', (root / 'export/src/gbvm.c').read_text())
            self.assertNotIn('ngc_ui_sprite_cut', (root / 'export/src/scene_sprites.c').read_text())


ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class SharedTextRomTests(unittest.TestCase):
    def probe(self, root, frames):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/probe_gbvm.py'),
                                 str(root / 'export'), '--emulator', ENV[1], '--bios', ENV[2],
                                 '--out', str(root / 'probe'), '--frames', str(frames),
                                 '--inputs', str(root / 'inputs.json'),
                                 '--shots', ','.join(map(str, range(1, frames + 1))),
                                 '--duty-cycle', '0.25', '--sprite-mask-trace'],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def town(self, root):
        return check(root, scene_folder='Sample Town', offset=8, bottom=152, committed_map=True)

    def build_town(self, root, mutate=False):
        project, _ = project_case(root, wait=120, case_b=True, mask_witness=True)
        manifest = export_project(project, root / 'export')
        if mutate:
            path = root / 'export/src/scene_sprites.c'
            source = path.read_text()
            old = '            *q = *a;'  # Remove restoration on frame-cache hits.
            self.assertEqual(source.count(old), 1)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(source.replace(old, '            /* Witness: keep stale attributes. */'), encoding='utf-8')
            temporary.replace(path)
            manifest['sha256']['src/scene_sprites.c'] = hashlib.sha256(path.read_bytes()).hexdigest()
            write_json(root / 'export/export.json', manifest)
        build_rom(root / 'export', Path(ENV[0]))
        write_json(root / 'inputs.json', [[150, 158, 16], [300, 308, 16]])

    def test_modal_pages_restore_decor_and_sprite_visibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build_town(root)
            self.probe(root, 420)
            result = self.town(root)
            self.assertGreater(result['compared_frames'], 300)
            self.assertGreater(result['hidden_sprite_samples'], 0)
            self.assertGreater(result['visible_sprite_samples'], 0)
            self.assertEqual(result['wrong_frames'], [])
            self.assertEqual(result['background_errors'], [])
            self.assertEqual(result['sprite_mask_errors'], [])

    def test_dynamic_priority_pixels_during_modal_dialogue_and_missing_mask_witness(self):
        from unittest.mock import patch
        from ngpcraft.sprite_priority import masked_sprites_source
        for witness in (False, True):
            with self.subTest(witness=witness), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project, _ = project_case(root, wait=120, case_b=True)
                settings_path = project / 'project/settings.gbsres'
                settings = json.loads(settings_path.read_text(encoding='utf-8'))
                settings.update(startX=12, startY=43)
                write_json(settings_path, settings)
                def emitter(*args, **kwargs):
                    renderer = masked_sprites_source(*args, **kwargs)
                    if witness:
                        renderer = renderer.replace('if (sx > ', 'if (1u || sx > ', 1)
                    return renderer
                with patch('ngpcraft.sprite_priority.masked_sprites_source', side_effect=emitter):
                    export_project(project, root / 'export')
                build_rom(root / 'export', Path(ENV[0]))
                write_json(root / 'inputs.json', [[150, 158, 16], [300, 308, 16]])
                self.probe(root, 420)
                result = self.town(root)
                self.assertGreater(result['priority_pixels_checked'], 0)
                self.assertEqual(bool(result['background_errors']), witness)
                self.assertEqual(result['wrong_frames'], [])
                self.assertEqual(result['sprite_mask_errors'], [])

    def test_cached_hidden_attributes_are_detected_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build_town(root, mutate=True)
            self.probe(root, 420)
            result = self.town(root)
            self.assertTrue(result['sprite_mask_errors'])
            self.assertTrue(any(e['frame'] > 320 for e in result['sprite_mask_errors']))

    def test_dialogues_survive_repeated_case_b_to_a_to_b_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = switch_project(root)
            export_project(project, root / 'export')
            build_rom(root / 'export', Path(ENV[0]))
            write_json(root / 'inputs.json', [[k, k + 8, 16] for k in range(80, 901, 100)])
            self.probe(root, 860)
            town, cave = self.town(root), check(root)
            self.assertGreaterEqual(len(town['starts']), 3)
            self.assertGreaterEqual(len(cave['starts']), 2)
            self.assertEqual(town['wrong_frames'], [])
            self.assertEqual(town['sprite_mask_errors'], [])
            self.assertEqual(town['background_errors'], [])
            self.assertEqual(cave['wrong_frames'], [])
