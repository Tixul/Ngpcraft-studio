"""Overlay Set Scanline Cutoff and the box at the top (docs/M6_SCANLINE_CUTOFF.md):
bytecode against the released ScriptBuilder, the case B refusal, the runtime's
absence, then the window cut at the line in a native ROM
(tools/lab_scanline_cutoff.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.emit_ui import overlay_cut_live
from ngpcraft.pipeline import export_project
from tools.demo_actor_moves import ev
from tools.reference import REFERENCE
from test_threads import v3
from test_text_draw import FONTS, OPTIONS

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_OVERLAY_SET_SCANLINE_CUTOFF': 'eventOverlaySetScanlineCutoff.js', 'EVENT_TEXT': 'eventTextDialogue.js'}
FONT = {'fonts': FONTS, 'defaultFontId': 'f0'}
num = lambda v: {'type': 'number', 'value': v}
var = lambda v: {'type': 'variable', 'value': v}


def cut(y, **units):
    return ev('c', 'EVENT_OVERLAY_SET_SCANLINE_CUTOFF', y=y, **units)


CASES = [({}, [cut(num(150), units='pixels')]), ({}, [cut(num(0))]), ({}, [cut(num(31), units='pixels')]),
         ({}, [cut(var('7'), units='pixels')]),
         ({}, [cut({'type': 'add', 'valueA': var('7'), 'valueB': num(8)}, units='pixels')]),
         ({}, [cut(num(4), units='tiles')]),
         (FONT, [ev('t', 'EVENT_TEXT', text=['Hello top'], position='top')]),
         (FONT, [ev('t', 'EVENT_TEXT', text=['Hi'], position='top', closeWhen='notModal')])]


class ScanlineCutoffTests(unittest.TestCase):
    def test_liveness(self):
        # textCloseNonModal writes LYC_SYNC_VALUE (150): no cut to draw.
        self.assertFalse(overlay_cut_live('        VM_SET_CONST_UINT8      _overlay_cut_scanline, 150'))
        self.assertTrue(overlay_cut_live('        VM_SET_CONST_UINT8      _overlay_cut_scanline, 31'))
        self.assertTrue(overlay_cut_live('        VM_SET_UINT8            _overlay_cut_scanline, .LOCAL_Y'))

    def test_case_b_is_refused(self):
        from tools.lab_actor_push import project_case as sample_town     # case B (shared planes)
        from tools.demo_gbs_scene import write_json
        with tempfile.TemporaryDirectory() as tmp:
            project = sample_town(Path(tmp))
            path = next((project / 'project/scenes').rglob('scene.gbsres'))
            scene = json.loads(path.read_text(encoding='utf-8'))
            scene['script'] = [ev('t', 'EVENT_TEXT', text=['Top'], position='top', closeWhen='notModal')]
            write_json(path, scene)
            with self.assertRaisesRegex(CompileError, 'case B'):
                export_project(project, Path(tmp) / 'out')

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': {**OPTIONS, **o}, 'events': e, 'eventFiles': FILES, 'subScripts': True} for o, e in CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for (options, events), reference in zip(CASES, json.loads(proc.stdout)['results']):
            with self.subTest(events=events):
                out, stack, _ = v3(options, events)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class ScanlineCutoffRomTests(unittest.TestCase):
    def run_lab(self, **mode):
        from tools.lab_scanline_cutoff import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, **mode)
        self.assertTrue(result['passed'], result)

    def test_cut_and_top_box_logical_map(self):
        self.run_lab()

    def test_cut_on_scr1_as_is(self):
        self.run_lab(simple=True)

    def test_witness(self):
        self.run_lab(witness=True)


if __name__ == '__main__':
    unittest.main()
