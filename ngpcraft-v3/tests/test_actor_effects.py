"""Actor Effects (split in / out, flicker): bytecode against the released
ScriptBuilder (15 cases), then the frame-by-frame trajectory in a native ROM
(tools/lab_actor_effects.py)."""
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
OPTIONS = {'scene': {'id': 's', 'actors': [{'id': 'a'}]}}


def cases():
    out = []
    for effect in ('splitIn', 'splitOut'):
        for d, s, u in ((16, 2, 'pixels'), (3, 1, 'tiles'), (10, 3, 'pixels'), (7, 7, None)):
            args = {'effect': effect, 'actorId': 'a', 'distance': d, 'speed': s}
            if u:
                args['units'] = u
            out.append(args)
    for flicker in ({'timeUnits': 'frames', 'frames': 20}, {'timeUnits': 'frames'}, {'timeUnits': 'time', 'time': 1}, {},
                    {'timeUnits': 'frames', 'frames': 0}, {'timeUnits': 'frames', 'frames': 5}):
        out.append({'effect': 'flicker', 'actorId': 'player', **flicker})
    out.append({'effect': 'splitIn', 'actorId': '$self$', 'distance': 8, 'speed': 1})
    return out


def v3(args):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, [{'command': 'EVENT_ACTOR_EFFECTS', 'args': args}], ['script[0]'], [])
    b._pack_locals()
    return out, b.stack_ptr


class ActorEffectsTests(unittest.TestCase):
    def test_zero_step_split_is_refused(self):
        # GB Studio divides by zero steps and emits Infinity into the script.
        with self.assertRaisesRegex(CompileError, 'no step'):
            v3({'effect': 'splitIn', 'actorId': 'a', 'distance': 1, 'speed': 4})

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': [{'command': 'EVENT_ACTOR_EFFECTS', 'args': a}],
                 'eventFiles': {'EVENT_ACTOR_EFFECTS': 'eventActorEffects.js'}} for a in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clean = lambda lines: [l for l in lines if l.strip() and not l.lstrip().startswith(';')]
        for args, reference in zip(cases(), json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out, stack = v3(args)
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class ActorEffectsRomTests(unittest.TestCase):
    def test_trajectories_and_flicker(self):
        from tools.lab_actor_effects import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
