"""Camera Set Lock and Camera Property Set: bytecode against the released
ScriptBuilder (25 cases), then a native ROM (tools/lab_camera_properties.py)."""
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
FILES = {'EVENT_CAMERA_SET_LOCK': 'eventCameraSetLock.js', 'EVENT_CAMERA_PROPERTY_SET': 'eventCameraPropertySet.js'}
OPTIONS = {'scene': {'id': 's', 'actors': []}}


def n(v):
    return {'type': 'number', 'value': v}


def var(v):
    return {'type': 'variable', 'value': v}


def cases():
    out = [[{'command': 'EVENT_CAMERA_SET_LOCK', 'args': {'axis': a, 'preventScroll': p}}]
           for a, p in ((['x', 'y'], []), (['x'], ['left', 'up']), ([], ['right', 'down']), (['y'], []))]
    for prop in ('camera_deadzone_x', 'camera_deadzone_y', 'camera_offset_x', 'camera_offset_y'):
        for v in (n(12), n(55), n(-3), var('4'), {'type': 'add', 'valueA': var('4'), 'valueB': n(2)}):
            out.append([{'command': 'EVENT_CAMERA_PROPERTY_SET', 'args': {'property': prop, 'value': v}}])
    out.append([{'command': 'EVENT_CAMERA_PROPERTY_SET', 'args': {'value': n(7)}}])
    return out


def v3(events):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, events, ['script[0]'], [])
    b._pack_locals()
    return out, b.stack_ptr


class CameraPropertyTests(unittest.TestCase):
    def test_unknown_property_is_refused(self):
        with self.assertRaisesRegex(CompileError, "camera property 'camera_zoom'"):
            v3([{'command': 'EVENT_CAMERA_PROPERTY_SET', 'args': {'property': 'camera_zoom', 'value': n(1)}}])

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': e, 'eventFiles': FILES} for e in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clean = lambda lines: [l for l in lines if l.strip() and not l.lstrip().startswith(';')]
        for events, reference in zip(cases(), json.loads(proc.stdout)['results']):
            with self.subTest(events=events):
                out, stack = v3(events)
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class CameraPropertyRomTests(unittest.TestCase):
    def test_offset_deadzone_and_lock(self):
        from tools.lab_camera_properties import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)
        self.assertIn('camera_properties', result['features'])


if __name__ == '__main__':
    unittest.main()
