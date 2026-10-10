"""Link cable events (docs/M6_LINK.md): bytecode against the released
ScriptBuilder, the runtime's absence, then two cabled consoles exchanging the
packets in a native ROM (tools/lab_link.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import load_model
from ngpcraft.gbvm.emit import RUNTIME, gbvm_source, link_slot_warnings, macro_opcode
from tools.demo_actor_moves import ev
from tools.reference import REFERENCE
from test_threads import OPTIONS, v3

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_LINK_HOST': 'eventLinkStart.js', 'EVENT_LINK_JOIN': 'eventLinkJoin.js',
         'EVENT_LINK_CLOSE': 'eventLinkClose.js', 'EVENT_LINK_TRANSFER': 'eventLinkTransfer.js'}
ARG = lambda n: {'type': 'argument', 'symbol': f'.SCRIPT_ARG_INDIRECT_{n}_VARIABLE', 'indirect': True}
INDIRECT = {'argLookup': {'variable': [['V0', ARG(0)], ['V1', ARG(1)]], 'actor': []}}


def transfer(send, receive, size=1):
    return ev('t', 'EVENT_LINK_TRANSFER', sendVariable=send, receiveVariable=receive, size=size)


CASES = [({}, [ev('h', 'EVENT_LINK_HOST'), ev('j', 'EVENT_LINK_JOIN'), ev('c', 'EVENT_LINK_CLOSE')]),
         ({}, [transfer('3', '5', 4)]), ({}, [transfer('3', '5', 0)]), ({}, [transfer('3', '5', 16)]),
         (INDIRECT, [transfer('V0', '5', 2)]), (INDIRECT, [transfer('3', 'V1', 2)]),
         (INDIRECT, [transfer('V0', 'V1', 3)])]


class LinkTests(unittest.TestCase):
    def test_bytecode_text(self):
        out, stack, _ = v3(*CASES[0])
        self.assertEqual([s.strip() for s in out if s.strip()],
                         ['VM_SIO_SET_MODE         .SIO_MODE_SLAVE', 'VM_SIO_SET_MODE         .SIO_MODE_MASTER',
                          'VM_SIO_SET_MODE         .SIO_MODE_NONE'])
        self.assertEqual(stack, 0)

    def test_packet_over_32_bytes_is_refused(self):
        # vm_sio.c truncates to LINK_MAX_PACKET_LENGTH = 32 bytes without a word.
        for size in (17, None, 1.5, -1):
            with self.subTest(size=size), self.assertRaisesRegex(CompileError, 'integer 0..16'):
                v3({}, [transfer('3', '5', size)])

    def test_exchange_is_a_real_instruction(self):
        model = load_model()
        self.assertEqual(macro_opcode(model, 'VM_SIO_EXCHANGE'), 0x6D)
        self.assertEqual(macro_opcode(model, 'VM_SIO_SET_MODE'), 0x6C)

    def test_transfer_runtime_only_with_transfer(self):
        runtime = json.loads(RUNTIME.read_text(encoding='utf-8'))
        source, _ = gbvm_source(runtime, {'VM_SIO_SET_MODE'}, set(), [], [], load_model())
        self.assertIn('static void vm_sio_set_mode(', source)
        for absent in ('vm_sio_exchange', 'ngc_com_buf', 'link_packet'):
            self.assertNotIn(absent, source)
        source, _ = gbvm_source(runtime, {'VM_SIO_SET_MODE', 'VM_SIO_EXCHANGE'}, set(), [], [], load_model())
        self.assertIn('static void vm_sio_exchange(', source)
        self.assertIn('ldb rw3, 0x19', source)          # COMCREATEBUFDATA

    def test_non_consecutive_slots_are_reported(self):
        slots = {'VAR_VARIABLE_20': 0, 'VAR_VARIABLE_23': 1, 'VAR_VARIABLE_50': 2, 'VAR_VARIABLE_51': 3,
                 'VAR_VARIABLE_52': 4}
        line = '        VM_SIO_EXCHANGE         VAR_VARIABLE_50, {}, 3'
        self.assertEqual(link_slot_warnings([line.format('VAR_VARIABLE_50')], slots), [])
        warning, = link_slot_warnings([line.format('VAR_VARIABLE_20')], slots)
        self.assertIn('VAR_VARIABLE_20, VAR_VARIABLE_23, VAR_VARIABLE_50', warning)
        warning, = link_slot_warnings(['        VM_SIO_EXCHANGE         VAR_VARIABLE_52, VAR_VARIABLE_50, 2'],
                                      slots)
        self.assertIn('beyond the last variable', warning)

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': {**OPTIONS, **o}, 'events': e, 'eventFiles': FILES, 'subScripts': True} for o, e in CASES]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for (options, events), reference in zip(CASES, json.loads(proc.stdout)['results']):
            with self.subTest(events=events):
                out, stack, _ = v3(options, events)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class LinkRomTests(unittest.TestCase):
    def test_two_consoles_exchange(self):
        from tools.lab_link import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)

    def test_cable_pulled_mid_transfer_recovers(self):
        from tools.lab_link import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, unplug=True)
        self.assertTrue(result['passed'], result)

    def test_alone_waits_without_freezing(self):
        from tools.lab_link import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, alone=True)
        self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
