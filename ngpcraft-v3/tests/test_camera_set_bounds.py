"""Camera Set Bounds: bytecode against the released ScriptBuilder, then the
scroll clamped by the new bounds in a native ROM (tools/lab_camera_bounds.py)."""
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
OPTIONS = {'scene': {'id': 's', 'actors': [{'id': 'a'}]}}
num = lambda v: {'type': 'number', 'value': v}
var = lambda v: {'type': 'variable', 'value': v}


def cases():
    return [{'x': num(2), 'y': num(3), 'width': num(25), 'height': num(20)},
            {'x': num(16), 'y': num(0), 'width': num(100), 'height': num(144), 'units': 'pixels'},
            {'x': var('0'), 'y': var('1'), 'width': num(30), 'height': var('2'), 'units': 'tiles'},
            {'x': num(0), 'y': num(0), 'width': num(5), 'height': num(400)},
            {'x': {'type': 'add', 'valueA': var('3'), 'valueB': num(1)}, 'y': num(1), 'width': var('4'),
             'height': num(18), 'units': 'pixels'}]


def v3(args):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, [{'command': 'EVENT_CAMERA_SET_BOUNDS', 'args': args}], ['script[0]'], [])
    b._pack_locals()
    return out, b.stack_ptr


class CameraSetBoundsTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': [{'command': 'EVENT_CAMERA_SET_BOUNDS', 'args': a}],
                 'eventFiles': {'EVENT_CAMERA_SET_BOUNDS': 'eventCameraSetBounds.js'}} for a in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for args, reference in zip(cases(), json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out, stack = v3(args)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class CameraSetBoundsRomTests(unittest.TestCase):
    def test_scroll_clamped(self):
        from tools.lab_camera_bounds import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
