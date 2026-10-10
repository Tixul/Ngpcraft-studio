"""Set Dialogue Frame: bytecode against the released ScriptBuilder, then the
frame of a dialogue box redrawn with the selected tileset in a native ROM
(tools/lab_dialogue_frame.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
TILESETS = [{'id': 't24', 'symbol': 'tileset_frame_wood', 'imageWidth': 24, 'imageHeight': 24, 'width': 3},
            {'id': 't32', 'symbol': 'tileset_big', 'imageWidth': 32, 'imageHeight': 24, 'width': 4}]
OPTIONS = {'scene': {'id': 's', 'actors': []}, 'tilesets': TILESETS}
CASES = [{'tilesetId': 't24'}, {'tilesetId': 'nope'}, {}]


def v3(args):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, [{'command': 'EVENT_SET_DIALOGUE_FRAME', 'args': args}], ['script[0]'], [])
    return out, b.stack_ptr


class DialogueFrameTests(unittest.TestCase):
    def test_wrong_size_is_refused_like_gb_studio(self):
        with self.assertRaisesRegex(CompileError, '32x24px. Please select a 24x24 tileset'):
            v3({'tilesetId': 't32'})

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': [{'command': 'EVENT_SET_DIALOGUE_FRAME', 'args': a}],
                 'eventFiles': {'EVENT_SET_DIALOGUE_FRAME': 'eventSetDialogueFrame.js'}} for a in CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for args, reference in zip(CASES, json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out, stack = v3(args)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class DialogueFrameRomTests(unittest.TestCase):
    def test_frame_redrawn(self):
        from tools.lab_dialogue_frame import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
