"""Events deprecated in the editor but compiled by GB Studio 4.3.2, and the trivial
ones (docs/M4_DEPRECATED_EVENTS.md). GB Studio 3/4 projects keep them: in the
test corpus EVENT_IF_VALUE alone has 7 473 uses in 12 modern projects."""
import json
import os
from pathlib import Path
import subprocess
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.demo_actor_moves import ev
from tools.lab_event_gaps import branch, set_value
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_IF_VALUE': 'eventIfVariableValue.js', 'EVENT_IF_VALUE_COMPARE': 'eventIfVariableCompare.js',
         'EVENT_IF_TRUE': 'eventIfVariableTrue.js', 'EVENT_IF_FALSE': 'eventIfVariableFalse.js',
         'EVENT_SET_TRUE': 'eventVariableSetToTrue.js', 'EVENT_SET_FALSE': 'eventVariableSetToFalse.js',
         'EVENT_IF_EXPRESSION': 'eventIfExpression.js', 'EVENT_TEXT_SET_ANIMATION_SPEED': 'eventTextSetAnimationSpeed.js',
         'EVENT_COMMENT': 'eventComment.js', 'EVENT_STOP': 'eventScriptStop.js',
         'EVENT_SET_VALUE': 'eventVariableSetToValue.js'}
ARG = {'type': 'argument', 'symbol': '.SCRIPT_ARG_INDIRECT_0_VARIABLE', 'indirect': True}


def cases():
    out = []
    for indirect in (False, True):
        options = {'argLookup': {'variable': [['V0', ARG]], 'actor': []}} if indirect else {}
        var = 'V0' if indirect else '12'
        yes, no = [set_value(1, 11)], [set_value(1, 22)]
        for op in ('==', '!=', '<', '>', '<=', '>='):
            for comparator in (0, 5, -3, None, '7'):
                for disabled in (False, True):
                    out.append((options, [branch(ev('v', 'EVENT_IF_VALUE', variable=var, operator=op,
                                                    comparator=comparator, __disableElse=disabled), yes, no)]))
            out.append((options, [branch(ev('c', 'EVENT_IF_VALUE_COMPARE', vectorX=var, operator=op, vectorY='3'),
                                         yes, no)]))
        for command in ('EVENT_IF_TRUE', 'EVENT_IF_FALSE'):
            for disabled in (False, True):
                out.append((options, [branch(ev('t', command, variable=var, __disableElse=disabled), yes, no)]))
        out.append((options, [ev('st', 'EVENT_SET_TRUE', variable=var), ev('sf', 'EVENT_SET_FALSE', variable=var)]))
    for expression in ('$12$ > 3', '($00$ + 2) * $05$ == 8', '1', ''):
        out.append(({}, [branch(ev('e', 'EVENT_IF_EXPRESSION', expression=expression), [set_value(1, 1)], [])]))
    for args in ({'speedIn': 1, 'speedOut': 2, 'speed': 3, 'allowFastForward': True},
                 {'speedIn': -3, 'speedOut': '-3', 'speed': 0, 'allowFastForward': False},
                 {'speedIn': 4, 'speedOut': 0}):
        out.append(({}, [ev('speed', 'EVENT_TEXT_SET_ANIMATION_SPEED', **args)]))
    out.append(({}, [ev('c', 'EVENT_COMMENT', text='note'), ev('stop', 'EVENT_STOP'), set_value(1, 2)]))
    return out


class DeprecatedEventTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_whole_released_handlers_and_compiler(self):
        rows = [{'options': o, 'events': e, 'eventFiles': FILES} for o, e in cases()]
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

    def test_undefined_operators_and_speeds_are_refused(self):
        for event in (ev('v', 'EVENT_IF_VALUE', variable='1', operator='=~', comparator=1),
                      ev('c', 'EVENT_IF_VALUE_COMPARE', vectorX='1', vectorY='2'),
                      ev('s', 'EVENT_TEXT_SET_ANIMATION_SPEED', speedOut=1),
                      ev('s', 'EVENT_TEXT_SET_ANIMATION_SPEED', speedIn='fast', speedOut=1)):
            with self.subTest(event=event), self.assertRaises(CompileError):
                compile_events(ScriptBuilder([], {}), [event], ['script[0]'], [])

    def test_deprecation_is_warned_like_gb_studio(self):
        b = ScriptBuilder([], {})
        compile_events(b, [ev('st', 'EVENT_SET_TRUE', variable='3')], ['script[0]'], [])
        self.assertTrue(any('"variableSetToTrue" is deprecated' in w and 'EVENT_SET_TRUE' in w
                            for w in b.options['warnings']))


if __name__ == '__main__':
    unittest.main()
