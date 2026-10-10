"""EVENT_CLEAR_DATA / EVENT_PEEK_DATA (docs/M5_SAVE.md): bytecode against the
released ScriptBuilder, then the flash journal in a native ROM (two power-ons)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.demo_actor_moves import ev
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_CLEAR_DATA': 'eventDataClear.js', 'EVENT_PEEK_DATA': 'eventDataPeek.js'}
OPTIONS = {'scene': {'id': 's', 'actors': [{'id': 'a'}]}}
ARG = {'type': 'argument', 'symbol': '.SCRIPT_ARG_INDIRECT_0_VARIABLE', 'indirect': True}


def cases():
    out = []
    for slot in (None, 0, 1, 2):
        args = {} if slot is None else {'saveSlot': slot}
        out.append(({}, [ev('c', 'EVENT_CLEAR_DATA', **args)]))
        out.append(({}, [ev('p', 'EVENT_PEEK_DATA', variableSource='5', variableDest='7', **args)]))
    indirect = {'argLookup': {'variable': [['V0', ARG]], 'actor': []}}
    out += [(indirect, [ev('p', 'EVENT_PEEK_DATA', variableSource='V0', variableDest='7')]),
            (indirect, [ev('p', 'EVENT_PEEK_DATA', variableSource='5', variableDest='V0')]),
            ({}, [ev('p', 'EVENT_PEEK_DATA', variableSource='L0', variableDest='T1')])]
    return out


def v3(options, events):
    options = {**OPTIONS, **options}
    if 'argLookup' in options:
        options['argLookup'] = {k: dict(v) for k, v in options['argLookup'].items()}
    out = []
    b = ScriptBuilder(out, options)
    compile_events(b, events, [f'script[{i}]' for i in range(len(events))], [])
    b._pack_locals()
    return out, b.stack_ptr


class ClearPeekBuilderTests(unittest.TestCase):
    def test_clear_text_and_slot_refused(self):
        out, _ = v3({}, [ev('c', 'EVENT_CLEAR_DATA', saveSlot=2)])
        self.assertEqual([s.strip() for s in out if s.strip()], ['; Clear Data in Slot 2', 'VM_SAVE_CLEAR           2'])
        for command in ('EVENT_CLEAR_DATA', 'EVENT_PEEK_DATA'):
            with self.subTest(command=command), self.assertRaisesRegex(CompileError, 'save slot 3'):
                v3({}, [ev('x', command, saveSlot=3, variableSource='1', variableDest='2')])

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_whole_released_handlers_and_compiler(self):
        rows = [{'options': {**OPTIONS, **o}, 'events': e, 'eventFiles': FILES} for o, e in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        original = json.loads(proc.stdout)['results']
        self.assertEqual(len(original), len(rows))
        clean = lambda lines: [s for s in lines if s.strip() and not s.lstrip().startswith(';')]
        for (options, events), reference in zip(cases(), original):
            with self.subTest(events=events):
                out, stack = v3(options, events)
                self.assertEqual(clean(out), clean(reference['output']))
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class ClearPeekRomTests(unittest.TestCase):
    def test_clear_and_peek_across_two_power_ons(self):
        from tools.lab_clear_peek import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertEqual(result['mismatches'], {})

    def test_witness_without_clear_is_detected(self):
        from tools.lab_clear_peek import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, witness=True)
        self.assertIn('boot1:VAR_VARIABLE_34', result['mismatches'])


if __name__ == '__main__':
    unittest.main()
