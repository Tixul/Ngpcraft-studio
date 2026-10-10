"""Physical UI height and logical scroll bounds, before project export."""
from pathlib import Path
import os
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.emit_ui import check_window_commands
from ngpcraft.pipeline import export_project
from tools.demo_text import project_case


class TextWindowLimitTests(unittest.TestCase):
    def test_maximum_height_in_each_plane_allocation(self):
        for shared, rows in ((False, 18), (True, 11)):
            with self.subTest(shared=shared):
                check_window_commands(f'VM_OVERLAY_CLEAR 0, 0, 20, {rows}, 1, 1', 'scene room', shared=shared)
                with self.assertRaisesRegex(CompileError, f'scene room: case .*{rows + 1} rows.*{rows}'):
                    check_window_commands(f'VM_OVERLAY_CLEAR 0, 0, 20, {rows + 1}, 1, 1', 'scene room', shared=shared)

    def test_scroll_rectangle_must_stay_inside_the_logical_map(self):
        check_window_commands('VM_OVERLAY_SET_SCROLL 1, 1, 18, 5, 1', 'scene room')
        check_window_commands('VM_OVERLAY_SET_SCROLL 31, 31, 1, 1, 1', 'scene room', shared=True)
        for rect in ('1, 31, 18, 2', '31, 1, 2, 5', '1, 1, 0, 5', '1, 1, 18, 0'):
            with self.subTest(rect=rect), self.assertRaisesRegex(CompileError, 'scene room.*32x32'):
                check_window_commands(f'VM_OVERLAY_SET_SCROLL {rect}, 1', 'scene room')

    def test_overheight_dialogue_fails_without_creating_an_export(self):
        for case_b, rows in ((False, 19), (True, 12)):
            for clear in (False, True):
                with self.subTest(case_b=case_b, clear=clear), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    project, _ = project_case(root, case_b=case_b,
                                              extra={'minHeight': rows, 'maxHeight': rows, 'clearPrevious': clear})
                    with self.assertRaisesRegex(CompileError, f'scene .*case .*{rows} rows'):
                        export_project(project, root / 'export')
                    self.assertFalse((root / 'export').exists())

    def test_scroll_outside_the_map_fails_without_creating_an_export(self):
        for case_b in (False, True):
            with self.subTest(case_b=case_b), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project, _ = project_case(root, case_b=case_b,
                                          extra={'textY': 31, 'textHeight': 5})
                with self.assertRaisesRegex(CompileError, 'scene .*VM_OVERLAY_SET_SCROLL.*32x32'):
                    export_project(project, root / 'export')
                self.assertFalse((root / 'export').exists())


ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class TextWindowLimitRomTests(unittest.TestCase):
    def test_maximum_windows_and_wrapped_row_negative_witness(self):
        from tools.lab_text_limits import run_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for case_b, mutate in ((False, False), (True, False), (False, True)):
                with self.subTest(case_b=case_b, mutate=mutate):
                    result = run_case(root / f'{case_b}-{mutate}', *(Path(v) for v in ENV),
                                      case_b=case_b, mutate=mutate)
                    self.assertGreater(result['compared_frames'], 300)
                    self.assertEqual(result['box_height'], 11 if case_b else 13)
                    self.assertEqual(result['wrong_frames'], [])
                    if mutate:
                        self.assertTrue(result['background_errors'])
                        self.assertTrue(any(e['frame'] > 360 for e in result['background_errors']))
                    else:
                        self.assertEqual(result['background_errors'], [])
                        self.assertEqual(result.get('sprite_mask_errors', []), [])
