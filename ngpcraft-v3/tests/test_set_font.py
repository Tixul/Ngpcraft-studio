"""Set Font: bytecode against the released ScriptBuilder (symbol, unknown
font = 0), then a native ROM where the same box changes glyphs
(tools/lab_set_font.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
FONTS = [{'id': 'f0', 'symbol': 'font_gbs_mono', 'mapping': {}}, {'id': 'f1', 'symbol': 'font_gbs_var', 'mapping': {}}]
OPTIONS = {'scene': {'id': 's', 'actors': []}, 'fonts': FONTS, 'defaultFontId': 'f0'}
CASES = [{'fontId': 'f1'}, {'fontId': 'f0'}, {'fontId': 'nope'}, {}]


class SetFontTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': [{'command': 'EVENT_SET_FONT', 'args': a}],
                 'eventFiles': {'EVENT_SET_FONT': 'eventSetFont.js'}} for a in CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clean = lambda lines: [l for l in lines if l.strip() and not l.lstrip().startswith(';')]
        for args, reference in zip(CASES, json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out = []
                compile_events(ScriptBuilder(out, dict(OPTIONS)), [{'command': 'EVENT_SET_FONT', 'args': args}],
                               ['script[0]'], [])
                self.assertEqual(clean(out), clean(reference['output']))


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class SetFontRomTests(unittest.TestCase):
    def test_box_glyphs_change(self):
        from tools.lab_set_font import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)

    def test_witness_same_glyphs(self):
        from tools.lab_set_font import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, witness=True)
        self.assertEqual(result['changed_pixels'], 0)


if __name__ == '__main__':
    unittest.main()
