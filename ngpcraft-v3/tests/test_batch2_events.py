"""Second corpus batch (docs/M4_BATCH2_EVENTS.md): bytecode against the released
ScriptBuilder, then the NGPC runtime of the new instructions."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.demo_actor_moves import ev, num
from tools.lab_event_gaps import branch, set_value
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_ACTOR_START_UPDATE': 'eventActorStartUpdateScript.js', 'EVENT_OVERLAY_HIDE': 'eventOverlayHide.js',
         'EVENT_SHOW_SPRITES': 'eventSpritesShow.js', 'EVENT_HIDE_SPRITES': 'eventSpritesHide.js',
         'EVENT_IF_CURRENT_SCENE_IS': 'eventIfCurrentSceneIs.js', 'EVENT_CAMERA_SET_POSITION': 'eventCameraSetPosition.js',
         'EVENT_GOTO_LABEL': 'eventLabelGoto.js', 'EVENT_DEFINE_LABEL': 'eventLabelDefine.js',
         'EVENT_ACTOR_SET_POSITION_TO_VALUE': 'eventActorSetPositionToVariables.js',
         'EVENT_ACTOR_MOVE_TO_VALUE': 'eventActorMoveToVariables.js',
         'EVENT_ACTOR_SET_FRAME_TO_VALUE': 'eventActorSetFrameToVariable.js',
         'EVENT_SET_VALUE': 'eventVariableSetToValue.js'}
OPTIONS = {'scene': {'actors': [{'id': 'a'}, {'id': 'b'}]},
           'scenes': [{'id': 's1', 'symbol': 'scene_one'}, {'id': 's2', 'symbol': 'scene_two'}]}
ARG = {'type': 'argument', 'symbol': '.SCRIPT_ARG_INDIRECT_0_VARIABLE', 'indirect': True}


def cases():
    yes, no = [set_value(1, 11)], [set_value(1, 22)]
    out = []
    for actor in ('a', 'b', 'player', '$self$'):
        out.append(({}, [ev('su', 'EVENT_ACTOR_START_UPDATE', actorId=actor)]))
    out += [({}, [ev('oh', 'EVENT_OVERLAY_HIDE')]),
            ({}, [ev('ss', 'EVENT_SHOW_SPRITES'), ev('hs', 'EVENT_HIDE_SPRITES')])]
    for scene in ('s1', 's2', 'nope'):
        for disabled in (False, True):
            out.append(({}, [branch(ev('cs', 'EVENT_IF_CURRENT_SCENE_IS', sceneId=scene, __disableElse=disabled), yes, no)]))
    for x, y, units in ((num(3), num(4), 'tiles'), (num(24), num(40), 'pixels'), ({'type': 'variable', 'value': '5'}, num(2), None)):
        args = {'x': x, 'y': y} | ({'units': units} if units else {})
        out.append(({}, [ev('cam', 'EVENT_CAMERA_SET_POSITION', **args)]))
    out.append(({}, [ev('d', 'EVENT_DEFINE_LABEL', label='top'), set_value(1, 1),
                     ev('g', 'EVENT_GOTO_LABEL', label='top'), ev('g2', 'EVENT_GOTO_LABEL', label='later'),
                     ev('d2', 'EVENT_DEFINE_LABEL', label='later')]))
    for indirect in (False, True):
        options = {'argLookup': {'variable': [['V0', ARG]], 'actor': []}} if indirect else {}
        var = 'V0' if indirect else '12'
        for actor in ('a', 'player'):
            out.append((options, [ev('p', 'EVENT_ACTOR_SET_POSITION_TO_VALUE', actorId=actor, vectorX=var, vectorY='3')]))
            out.append((options, [ev('m', 'EVENT_ACTOR_MOVE_TO_VALUE', actorId=actor, vectorX='3', vectorY=var)]))
            out.append((options, [ev('f', 'EVENT_ACTOR_SET_FRAME_TO_VALUE', actorId=actor, variable=var)]))
    return out


class Batch2BuilderTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_whole_released_handlers_and_compiler(self):
        rows = [{'options': {**OPTIONS, **o}, 'events': e, 'eventFiles': FILES} for o, e in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        original = json.loads(proc.stdout)['results']
        clean = lambda lines: [s for s in lines if s.strip() and not s.lstrip().startswith(';')]
        for row, reference in zip(rows, original):
            with self.subTest(events=row['events']):
                options = dict(row['options'])
                if 'argLookup' in options:
                    options['argLookup'] = {k: dict(v) for k, v in options['argLookup'].items()}
                out = []
                b = ScriptBuilder(out, options)
                compile_events(b, row['events'], [f'script[{i}]' for i in range(len(row['events']))], [])
                b._pack_locals()
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(b.stack_ptr, reference['stack'])
        self.assertEqual(len(original), len(rows))



ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class Batch2RomTests(unittest.TestCase):
    def test_variables_labels_scene_update_restart_and_sprite_visibility_in_rom(self):
        from tools.lab_batch2 import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'batch2', *ROM_ENV)
        self.assertEqual(result['mismatches'], {})
        self.assertEqual(result['visible_sprites_hidden'], 0)
        self.assertGreater(result['visible_sprites_shown'], 0)


if __name__ == '__main__':
    unittest.main()
