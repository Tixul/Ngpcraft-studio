"""Text Draw: bytecode against the released ScriptBuilder, then the text
printed on the background (SCR2) and on the overlay in a native ROM
(tools/lab_text_draw.py)."""
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
FONTS = [{'id': 'f0', 'symbol': 'font_gbs_mono', 'mapping': {}}]
OPTIONS = {'scene': {'id': 's', 'actors': [{'id': 'a'}]}, 'fonts': FONTS, 'defaultFontId': 'f0'}
MONO = {**OPTIONS, 'paletteSettings': {'colorMode': 'mono'}}


def cases():
    return [(OPTIONS, {}), (OPTIONS, {'text': 'HELLO', 'x': 3, 'y': 5}),
            (OPTIONS, {'text': 'Score $00$', 'x': 0, 'y': 17, 'location': 'overlay'}),
            (OPTIONS, {'text': 'AB\nCD', 'x': 19, 'y': 0, 'location': 'background'}),
            (OPTIONS, {'text': '', 'x': 1, 'y': 1}),
            (MONO, {'text': 'MONO', 'x': 2, 'y': 2})]


def v3(options, args):
    out = []
    b = ScriptBuilder(out, dict(options))
    compile_events(b, [{'command': 'EVENT_TEXT_DRAW', 'args': args}], ['script[0]'], [])
    b._pack_locals()
    return out, b.stack_ptr


class TextDrawTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        settings = lambda o: {**o, 'settings': o['paletteSettings']} if 'paletteSettings' in o else o
        rows = [{'options': settings(o), 'events': [{'command': 'EVENT_TEXT_DRAW', 'args': a}],
                 'eventFiles': {'EVENT_TEXT_DRAW': 'eventTextDraw.js'}} for o, a in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for (options, args), reference in zip(cases(), json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out, stack = v3(options, args)
                clean = lambda lines: [l for l in lines if l.strip() and not l.lstrip().startswith(';')]
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class TextDrawRomTests(unittest.TestCase):
    def test_background_and_overlay(self):
        from tools.lab_text_draw import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
