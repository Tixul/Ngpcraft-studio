"""Threads (docs/M4_THREADS.md): EVENT_THREAD_START / EVENT_THREAD_STOP against
the released ScriptBuilder (main script and thread script), then the GBVM
vm_beginthread / vm_terminate handlers in a native ROM."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.demo_actor_moves import ev
from tools.lab_event_gaps import set_value
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_THREAD_START': 'eventThreadStart.js', 'EVENT_THREAD_STOP': 'eventThreadStop.js',
         'EVENT_SET_VALUE': 'eventVariableSetToValue.js'}
OPTIONS = {'scene': {'id': 's', 'actors': [{'id': 'a'}, {'id': 'b'}]}}
ARG = {'type': 'argument', 'symbol': '.SCRIPT_ARG_INDIRECT_0_VARIABLE', 'indirect': True}


def start(key, handle, script):
    return {**ev(key, 'EVENT_THREAD_START', **({} if handle is None else {'variable': handle})),
            'children': {'true': script}}


def cases():
    indirect = {'argLookup': {'variable': [['V0', ARG]], 'actor': []}}
    return [
        ({}, [start('t', '3', [set_value(4, 7)]), ev('s', 'EVENT_THREAD_STOP', variable='3')]),
        ({}, [start('t', '3', [])]),
        (indirect, [start('t', 'V0', [set_value(4, 1)]), ev('s', 'EVENT_THREAD_STOP', variable='V0')]),
        # The same script twice: one thread script, reused (additionalScriptsCache).
        ({}, [start('t1', '3', [set_value(4, 2)]), start('t2', '5', [set_value(4, 2)])]),
        # A thread that starts and stops another one.
        ({}, [start('outer', '3', [start('inner', '5', [set_value(6, 1)]), ev('s', 'EVENT_THREAD_STOP', variable='5')])]),
    ]


def v3(options, events):
    options = {**OPTIONS, **options, 'additionalScripts': {}, 'additionalScriptsCache': {}, 'symbols': {},
               'maxDepth': 5, 'context': 'entity'}
    if 'argLookup' in options:
        options['argLookup'] = {k: dict(v) for k, v in options['argLookup'].items()}
    out = []
    b = ScriptBuilder(out, options)
    compile_events(b, events, [f'script[{i}]' for i in range(len(events))], [])
    b._pack_locals()
    return out, b.stack_ptr, options['additionalScripts']


def body(text):
    # A thread script runs in a context of its own: NgpCraft compiles it without
    # the calling custom script's argument equates (docs/M2_CUSTOM_EVENTS.md);
    # GB Studio writes them, unused. Everything else must be identical.
    return [line for line in text.splitlines() if not line.startswith('.SCRIPT_ARG_')]


class ThreadBuilderTests(unittest.TestCase):
    def test_missing_handle_variable_is_refused(self):
        # GB Studio compiles an absent variable as VAR_VARIABLE_0 (the editor
        # always writes its default T0); NgpCraft refuses it, as every event.
        from ngpcraft.compiler import CompileError
        for events in ([start('t', None, [])], [ev('s', 'EVENT_THREAD_STOP')]):
            with self.assertRaisesRegex(CompileError, 'variable reference None'):
                v3({}, events)

    def test_bytecode_text(self):
        out, stack, extra = v3({}, cases()[0][1])
        self.assertEqual([s.strip() for s in out if s.strip()],
                         ['; Thread Start', 'VM_UNLOCK',
                          'VM_BEGINTHREAD          ___bank_script_thread, _script_thread, VAR_VARIABLE_3, 0',
                          '; Thread Stop', 'VM_TERMINATE            VAR_VARIABLE_3'])
        self.assertEqual(stack, 0)
        self.assertIn('VM_SET_CONST            VAR_VARIABLE_4, 7', extra['script_thread']['compiledScript'])

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_whole_released_handlers_and_compiler(self):
        rows = [{'options': {**OPTIONS, **o}, 'events': e, 'eventFiles': FILES, 'subScripts': True} for o, e in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        original = json.loads(proc.stdout)['results']
        self.assertEqual(len(original), len(rows))
        clean = lambda lines: [s for s in lines if s.strip() and not s.lstrip().startswith(';')]
        for (options, events), reference in zip(cases(), original):
            with self.subTest(events=events):
                out, stack, extra = v3(options, events)
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(stack, reference['stack'])
                self.assertEqual({k: body(v['compiledScript']) for k, v in extra.items()},
                                 {k: body(v['compiledScript']) for k, v in reference['additionalScripts'].items()})


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class ThreadRomTests(unittest.TestCase):
    def test_start_stop_end_and_handles_in_rom(self):
        from tools.lab_threads import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'threads', *ROM_ENV)
        self.assertEqual(result['mismatches'], {})

    def test_witness_without_terminate_is_detected(self):
        from tools.lab_threads import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'threads', *ROM_ENV, witness=True)
        self.assertIn(84, result['mismatches'])


if __name__ == '__main__':
    unittest.main()
