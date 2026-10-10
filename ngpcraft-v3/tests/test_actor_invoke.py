"""Invoke Actor Script (ECARTS 25): GB Studio 4.3.2 calls the next actor's
interact script (scene.actors[actorIndex], actorIndex counting the player as
0); NgpCraft calls the named actor's. Same instruction otherwise. ROM proof:
tools/lab_actor_invoke.py."""
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
SCENE = {'id': 's', 'actors': [{'id': 'a', 'symbol': 'actor_a', 'script': [{'command': 'EVENT_END'}]},
                               {'id': 'b', 'symbol': 'actor_b', 'script': [{'command': 'EVENT_END'}]},
                               {'id': 'c', 'symbol': 'actor_c', 'script': []}]}
TARGETS = ('a', 'b', 'c', 'player')


def calls(lines):
    return [line.split()[1].rstrip(',') for line in lines if 'VM_CALL_FAR' in line]


def v3(actor):
    out, warnings = [], []
    b = ScriptBuilder(out, {'scene': SCENE, 'warnings': warnings})
    compile_events(b, [{'command': 'EVENT_ACTOR_INVOKE', 'args': {'actorId': actor}}], ['script[0]'], [])
    return out, warnings


class ActorInvokeTests(unittest.TestCase):
    def test_named_actor_and_warning(self):
        expected = {'a': ['___bank_actor_a_interact'], 'b': ['___bank_actor_b_interact'], 'c': [], 'player': []}
        for actor, want in expected.items():
            with self.subTest(actor=actor):
                out, warnings = v3(actor)
                self.assertEqual(calls(out), want)
                self.assertEqual(len(warnings), len(want))

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler_calls_the_next_actor(self):
        rows = [{'options': {'scene': SCENE}, 'events': [{'command': 'EVENT_ACTOR_INVOKE', 'args': {'actorId': x}}],
                 'eventFiles': {'EVENT_ACTOR_INVOKE': 'eventActorInvoke.js'}} for x in TARGETS]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        released = {x: calls(r['output']) for x, r in zip(TARGETS, json.loads(proc.stdout)['results'])}
        # The documented off-by-one of GB Studio 4.3.2 (ECARTS 25).
        self.assertEqual(released, {'a': ['___bank_actor_b_interact'], 'b': [], 'c': [],
                                    'player': ['___bank_actor_a_interact']})
        # Everything but the target is the released text.
        clean = lambda lines: [l.replace('actor_b', 'actor_a') for l in lines if l.strip() and not l.lstrip().startswith(';')]
        out, _ = v3('a')
        self.assertEqual(clean(out), clean(json.loads(proc.stdout)['results'][0]['output']))


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class ActorInvokeRomTests(unittest.TestCase):
    def test_named_actor_script_runs_and_caller_stops(self):
        from tools.lab_actor_invoke import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertEqual(result['mismatches'], {})
        self.assertEqual(len(result['warnings']), 1)


if __name__ == '__main__':
    unittest.main()
