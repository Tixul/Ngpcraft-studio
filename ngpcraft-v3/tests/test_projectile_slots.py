"""Load / Launch Projectile Slot (ECARTS 26): bytecode against the released
ScriptBuilder (global tables of five, deduplicated), then a native ROM where
each shot is identified by the hit script its projectile group runs
(tools/lab_projectile_slots.py)."""
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
FILES = {'EVENT_LOAD_PROJECTILE_SLOT': 'eventLoadProjectile.js',
         'EVENT_LAUNCH_PROJECTILE_SLOT': 'eventLaunchProjectileSlot.js'}
OPTIONS = {'scene': {'id': 's', 'actors': [{'id': 'a'}]}}


def load(slot, speed=2, **k):
    return {'command': 'EVENT_LOAD_PROJECTILE_SLOT', 'args': {
        'slot': slot, 'spriteSheetId': 's1', 'spriteStateId': '', 'speed': speed, 'animSpeed': 15, 'loopAnim': True,
        'lifeTime': 1, 'initialOffset': 0, 'destroyOnHit': True, 'collisionGroup': '3', 'collisionMask': ['1'], **k}}


def launch(slot, **k):
    return {'command': 'EVENT_LAUNCH_PROJECTILE_SLOT', 'args': {
        'slot': slot, 'actorId': 'player', 'directionType': 'direction', 'direction': 'right', 'x': 0, 'y': 0, **k}}


def cases():
    return [[load(0)], [load(1), load(2), load(3, speed=3)], [load(i, speed=i + 1) for i in range(5)] + [load(0, speed=9)],
            [load(4, speed=1), load(0, speed=1)], [launch(0)], [launch(3, directionType='angle', angle=64)],
            [launch(2, directionType='actor', otherActorId='player')],
            [launch(1, directionType='target', targetActorId='a')], [launch(-1)]]


def v3(events):
    out = []
    b = ScriptBuilder(out, {**OPTIONS, 'globalProjectiles': [], 'symbols': {}})
    compile_events(b, events, [f'script[{i}]' for i in range(len(events))], [])
    b._pack_locals()
    return out, b.stack_ptr


class ProjectileSlotTests(unittest.TestCase):
    def test_slot_outside_the_five_definitions_is_refused(self):
        for event in (load(5), launch(5)):
            with self.subTest(event=event['command']), self.assertRaisesRegex(CompileError, 'expected 0..4'):
                v3([event])

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': {**OPTIONS, 'globalProjectiles': [], 'symbols': {}}, 'events': e, 'eventFiles': FILES}
                for e in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clean = lambda lines: [l for l in lines if l.strip() and not l.lstrip().startswith(';')]
        for events, reference in zip(cases(), json.loads(proc.stdout)['results']):
            with self.subTest(events=[e['args']['slot'] for e in events]):
                out, stack = v3(events)
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class ProjectileSlotRomTests(unittest.TestCase):
    def test_each_shot_runs_its_definition(self):
        from tools.lab_projectile_slots import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertEqual(result['mismatches'], {})
        self.assertIn('projectile_slots', result['features'])
        # One hit per step, in order.
        steps = result['steps']
        self.assertEqual([(steps[k]['V22'], steps[k]['V24'], steps[k]['V26']) for k in sorted(steps, key=int)],
                         [(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 2, 0), (1, 2, 1), (1, 2, 1)])


if __name__ == '__main__':
    unittest.main()
