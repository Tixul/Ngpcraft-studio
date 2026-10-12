"""Second background with Camera Set Bounds and camera shake (PLAN_MEGAMAN S1, S2)."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.pipeline import export_project

ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]
num = lambda v: {'type': 'number', 'value': v}


class SecondCameraExportTests(unittest.TestCase):
    def test_set_bounds_clamps_the_main_position_scr1_follows(self):
        from tools.demo_second_background import project_case
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            path, _ = project_case(r, text=False, transitions=False)
            scene = next((path / 'project/scenes').rglob('scene.gbsres'))
            data = json.loads(scene.read_text(encoding='utf-8'))
            data['script'].insert(0, {'id': 'bounds-second', 'command': 'EVENT_CAMERA_SET_BOUNDS',
                                      'args': {'x': num(2), 'y': num(0), 'width': num(25), 'height': num(18)}})
            scene.write_text(json.dumps(data), encoding='utf-8')
            export_project(path, r / 'out')
            stream = (r / 'out/src/scene_stream.c').read_text(encoding='utf-8')
            self.assertIn('unsigned short ngc_scroll_x_min;', stream)
            self.assertEqual(stream.count('(unsigned short)x < ngc_scroll_x_min'), 1)
            if ROM_ENV[0]:
                from ngpcraft.toolchain import build_rom
                build_rom(r / 'out', Path(ROM_ENV[0]))


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class SecondCameraRomTests(unittest.TestCase):
    def run_lab(self, witness):
        from tools.lab_second_camera import build, observe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'lab'
            root.mkdir()
            main, front, view = build(root, Path(ROM_ENV[0]), witness)
            return observe(root, ROM_ENV[1], ROM_ENV[2], main, front, view)

    def test_bounds_and_shake_at_the_pixel(self):
        result = self.run_lab(False)
        self.assertTrue(result['passed'], result)
        self.assertLessEqual(len(result['repaint_frames_partial']), 2)

    def test_scr1_from_the_shaken_position_is_detected(self):
        self.assertFalse(self.run_lab(True)['passed'])


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class SecondBehindTests(unittest.TestCase):
    """second_background.behind: SCR2 in front, SCR1 seen through its backdrop."""
    def observe(self, rom_behind, model_behind):
        from tools.lab_second_stream import build_case, observe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'lab'
            build_case(root, Path(ROM_ENV[0]), behind=rom_behind)
            spec = json.loads((root / 'source.json').read_text(encoding='utf-8'))
            spec['behind'] = model_behind
            (root / 'source.json').write_text(json.dumps(spec), encoding='utf-8')
            return observe(root, Path(ROM_ENV[1]), Path(ROM_ENV[2]))

    def test_main_background_in_front_at_the_pixel(self):
        self.assertTrue(self.observe(True, True)['passed'])

    def test_plane_order_is_observed(self):
        self.assertFalse(self.observe(False, True)['passed'])


if __name__ == '__main__':
    unittest.main()
