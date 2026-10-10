"""EVENT_GBVM_SCRIPT, EVENT_IF_DEVICE_SGB, EVENT_IF_DEVICE_GBA (ECARTS §12):
bytecode against the released ScriptBuilder, refusals named at the block
line, then a native ROM (tools/lab_gbvm_script.py)."""
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
from tools.lab_event_gaps import set_value
from tools.lab_gbvm_script import RAW
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_GBVM_SCRIPT': 'eventGBVMScript.js', 'EVENT_IF_DEVICE_SGB': 'eventIfDeviceSGB.js',
         'EVENT_IF_DEVICE_GBA': 'eventIfDeviceGBA.js', 'EVENT_SET_VALUE': 'eventVariableSetToValue.js'}
OPTIONS = {'scene': {'id': 's', 'actors': []}}


def branch(key, command, yes, no, **args):
    return {**ev(key, command, **args), 'children': {'true': yes, 'false': no}}


def cases():
    out = [[ev('g', 'EVENT_GBVM_SCRIPT', script=RAW.replace('\nVM_CALL_FAR             ___bank_script_raw_helper, _script_raw_helper', ''))],
           [ev('g', 'EVENT_GBVM_SCRIPT', script='')], [ev('g', 'EVENT_GBVM_SCRIPT')]]
    for command in ('EVENT_IF_DEVICE_SGB', 'EVENT_IF_DEVICE_GBA'):
        for disabled in (False, True):
            out.append([branch('d', command, [set_value(1, 1)], [set_value(1, 2)], __disableElse=disabled)])
    return out


def v3(events):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, events, [f'script[{i}]' for i in range(len(events))], [])
    b._pack_locals()
    return out, b.stack_ptr


class GbvmScriptBuilderTests(unittest.TestCase):
    def test_refusals_name_the_block_line(self):
        # VM_SIO_SET_MODE was the example until the link was delivered (docs/M6_LINK.md).
        with self.assertRaisesRegex(CompileError, r'GBVM script line 2: VM_SOUND_MASTERVOL is not delivered'):
            v3([ev('g', 'EVENT_GBVM_SCRIPT', script='VM_IDLE\nVM_SOUND_MASTERVOL 0x77')])
        with self.assertRaisesRegex(CompileError, r'GBVM script line 3: unknown instruction VM_NOPE'):
            v3([ev('g', 'EVENT_GBVM_SCRIPT', script='; c\nVM_IDLE\nVM_NOPE 1')])

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_whole_released_handlers_and_compiler(self):
        rows = [{'options': OPTIONS, 'events': e, 'eventFiles': FILES} for e in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        original = json.loads(proc.stdout)['results']
        self.assertEqual(len(original), len(rows))
        for events, reference in zip(cases(), original):
            with self.subTest(events=events):
                out, stack = v3(events)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class GbvmScriptRomTests(unittest.TestCase):
    def test_raw_block_and_device_conditions_in_rom(self):
        from tools.lab_gbvm_script import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertEqual(result['mismatches'], {})


if __name__ == '__main__':
    unittest.main()
