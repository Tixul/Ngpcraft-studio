"""Adventure scene type: GBVM states/adventure.c vendored (tools/extract_genres.py),
its events against the released compiler, then native ROMs compared tick by
tick with the original C (tools/lab_adventure.py, tools/genre_oracle.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools import extract_genres
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = {'scene': {'id': 's', 'actors': []}}
STATES = [{'state': s} for s in ('ground', 'dash', 'knockback', 'blank', 'run', 'push', 'nope')] + [{}]


def v3(command, args):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, [{'command': command, 'args': args}], ['script[0]'], [])
    return out, b.stack_ptr


class AdventureExportTests(unittest.TestCase):
    def test_frozen_source_matches_published_gbvm(self):
        current = json.loads(extract_genres.OUT['adventure'].read_text(encoding='utf-8'))
        self.assertEqual(current, json.loads(json.dumps(extract_genres.extract('adventure'))))
        self.assertEqual(current['unadapted'], [])

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_state_set_matches_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': [{'command': 'EVENT_ADVENTURE_STATE_SET', 'args': a}],
                 'eventFiles': {'EVENT_ADVENTURE_STATE_SET': 'eventAdventureStateSet.js'}} for a in STATES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for args, reference in zip(STATES, json.loads(proc.stdout)['results']):
            with self.subTest(args=args):
                out, stack = v3('EVENT_ADVENTURE_STATE_SET', args)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])


CALLBACK_FILES = {'EVENT_SET_ADVENTURE_CALLBACK_SCRIPT': 'eventAdventureCallbackScriptSet.js',
                  'EVENT_REMOVE_ADVENTURE_CALLBACK_SCRIPT': 'eventAdventureCallbackScriptRemove.js',
                  'EVENT_SET_VALUE': 'eventVariableSetToValue.js'}


def callback_cases():
    set_value = lambda v: {'command': 'EVENT_SET_VALUE', 'args': {'variable': '4', 'value': {'type': 'number', 'value': v}}}
    out = []
    for event in ('groundStart', 'dashReady', 'pushEnd', 'nope'):
        out.append([{'command': 'EVENT_SET_ADVENTURE_CALLBACK_SCRIPT', 'args': {'event': event},
                     'children': {'script': [set_value(7)]}}])
        out.append([{'command': 'EVENT_REMOVE_ADVENTURE_CALLBACK_SCRIPT', 'args': {'event': event}}])
    return out


class AdventureCallbackTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_callbacks_match_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': e, 'eventFiles': CALLBACK_FILES, 'subScripts': True}
                for e in callback_cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clean = lambda lines: [s for s in lines if s.strip() and not s.lstrip().startswith(';')]
        for events, reference in zip(callback_cases(), json.loads(proc.stdout)['results']):
            with self.subTest(events=events):
                options = {**OPTIONS, 'additionalScripts': {}, 'additionalScriptsCache': {}, 'symbols': {},
                           'maxDepth': 5, 'context': 'entity'}
                out = []
                b = ScriptBuilder(out, options)
                compile_events(b, events, ['script[0]'], [])
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(b.stack_ptr, reference['stack'])
                self.assertEqual(sorted(options['additionalScripts']), sorted(reference['additionalScripts']))


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS', 'NGC_TEST_CLANG')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator, BIOS and Clang')
class AdventureRomTests(unittest.TestCase):
    def test_every_case_matches_original_c(self):
        from tools.lab_adventure import CASES, run_case
        with tempfile.TemporaryDirectory() as tmp:
            for name in sorted(CASES):
                with self.subTest(case=name):
                    result = run_case(tmp, name, *ROM_ENV)
                    self.assertTrue(result['passed'], {k: result[k] for k in ('mismatches', 'first')})

    def test_witness_is_detected(self):
        from tools.lab_adventure import double_step, run_case
        with tempfile.TemporaryDirectory() as tmp:
            result = run_case(tmp, 'adv-8way', *ROM_ENV, mutate=double_step)
        self.assertGreater(result['mismatches'], 0)


if __name__ == '__main__':
    unittest.main()
