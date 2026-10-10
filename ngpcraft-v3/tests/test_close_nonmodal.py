"""Close Non-Modal Dialogue: bytecode against the released ScriptBuilder,
then a native ROM where the screen returns to the one before the box
(tools/lab_close_nonmodal.py), instant and at a speed."""
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
CASES = [{}, {'speed': 0}, {'speed': -3}, {'speed': 2}, {'speed': '3'}]


class CloseNonModalTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': {'scene': {'id': 's', 'actors': []}},
                 'events': [{'command': 'EVENT_DIALOGUE_CLOSE_NONMODAL', 'args': a}],
                 'eventFiles': {'EVENT_DIALOGUE_CLOSE_NONMODAL': 'eventTextCloseNonModal.js'}} for a in CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clean = lambda lines: [l for l in lines if l.strip() and not l.lstrip().startswith(';')]
        for args, reference in zip(CASES, json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out = []
                compile_events(ScriptBuilder(out, {'scene': {'id': 's', 'actors': []}}),
                               [{'command': 'EVENT_DIALOGUE_CLOSE_NONMODAL', 'args': args}], ['script[0]'], [])
                self.assertEqual(clean(out), clean(reference['output']))


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class CloseNonModalRomTests(unittest.TestCase):
    def test_box_closes_back_to_the_scene(self):
        from tools.lab_close_nonmodal import run
        for speed in (-3, 2):
            with self.subTest(speed=speed), tempfile.TemporaryDirectory() as tmp:
                result = run(Path(tmp) / 'lab', *ROM_ENV, speed=speed)
                self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
