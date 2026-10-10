"""EVENT_ACTOR_SET_COLLISION_BOX: bytecode against the released ScriptBuilder
(actorSetBoundToScriptValues), then a native ROM where the box changes where
a moving player stops (tools/lab_actor_collision_box.py)."""
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
OPTIONS = {'scene': {'id': 's', 'actors': [{'id': 'a'}, {'id': 'b'}]}}


def n(v):
    return {'type': 'number', 'value': v}


def var(v):
    return {'type': 'variable', 'value': v}


def cases():
    out = [dict(actorId=a, x=n(0), y=n(-8), width=n(16), height=n(8)) for a in ('a', 'player', '$self$')]
    out += [dict(actorId='b', x=var('3'), y=n(2), width=var('4'), height={'type': 'add', 'valueA': var('5'), 'valueB': n(1)}),
            dict(actorId='a', x=n(4), y=n(4), width=n(1), height=n(1))]
    return out


def v3(args):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, [{'command': 'EVENT_ACTOR_SET_COLLISION_BOX', 'args': args}], ['script[0]'], [])
    b._pack_locals()
    return out, b.stack_ptr


class CollisionBoxTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': [{'command': 'EVENT_ACTOR_SET_COLLISION_BOX', 'args': c}],
                 'eventFiles': {'EVENT_ACTOR_SET_COLLISION_BOX': 'eventActorSetCollisionBox.js'}} for c in cases()]
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
class CollisionBoxRomTests(unittest.TestCase):
    def test_wider_box_stops_the_player_earlier(self):
        from tools.lab_actor_collision_box import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)
        self.assertIn('actor_set_bounds', result['features'])

    def test_witness_without_the_event(self):
        from tools.lab_actor_collision_box import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, witness=True)
        self.assertEqual(result['gap'], 0)
        self.assertNotIn('actor_set_bounds', result['features'])


if __name__ == '__main__':
    unittest.main()
