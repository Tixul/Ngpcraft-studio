"""Variable Data Table: script text (code and the rows after it) against the
released ScriptBuilder, assembly of the table label, then the looked-up
values in a native ROM (tools/lab_data_table.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = {'scene': {'id': 's', 'actors': [{'id': 'a'}]}}
num = lambda v: {'type': 'number', 'value': v}


def table(variables, rows, label=None):
    out = {'variables': variables, 'rows': rows}
    if label is not None:
        out['label'] = label
    return out


def cases():
    return [
        [{'indexVariable': '0', 'data': table(['1'], [{'values': [num(7)]}])}],
        [{'indexVariable': '3', 'data': table(['1', '2', '4'], [
            {'label': 'Slime', 'values': [num(10), num(-3), num(200)]},
            {'values': [num(1), None, num(-32768)]},
            {'label': 'Boss', 'values': [num(65535)]}], label='Enemy Stats!')}],
        [{'indexVariable': 'T1', 'data': table(['T0', '9'], [{'values': [num(4), num(5)]}, {'values': []}])}],
        # Empty tables are skipped, two tables in one script get distinct symbols.
        [{'indexVariable': '0', 'data': table([], [{'values': []}])},
         {'indexVariable': '0', 'data': table(['1'], [])},
         {'indexVariable': '0', 'data': table(['1'], [{'values': [num(1)]}], label='t')},
         {'indexVariable': '1', 'data': table(['2'], [{'values': [num(2)]}], label='t')}],
        [{'indexVariable': '0', 'data': table(['1'], [{'values': [num(3)]}], label='9 lives')}],
    ]


def v3(events):
    out = []
    b = ScriptBuilder(out, dict(OPTIONS))
    compile_events(b, [{'command': 'EVENT_DATA_TABLE', 'args': a} for a in events],
                   [f'script[{i}]' for i in range(len(events))], [])
    b._pack_locals()
    return out, b.stack_ptr, b.to_script_string('script_1', False)


class DataTableTests(unittest.TestCase):
    def test_constant_cell_is_its_symbol(self):
        # getConstantSymbol: unknown id "0" (docs/M6_CONSTANTS.md, tests/test_constants.py).
        _, _, script = v3([{'indexVariable': '0', 'data': table(['1'], [{'values': [{'type': 'constant', 'value': 'c'}]}])}])
        self.assertIn('.dw 0', script)

    def test_table_label_is_a_bank_address(self):
        _, _, script = v3(cases()[1])
        variables = {f'VAR_VARIABLE_{i}': i for i in range(10)}
        _, code, _, rpn = assemble(script, variables, base=0x120)
        rows = [10, -3, 200, 1, 0, -32768, 65535, 0, 0]
        words = b''.join((v & 0xFFFF).to_bytes(2, 'little') for v in rows)
        self.assertTrue(code.endswith(words))
        start = 0x120 + len(code) - len(words)
        # .R_INT16 enemy_stats_ : the first RPN operand after VM_RPN.
        self.assertIn(start.to_bytes(2, 'little'), code[:len(code) - len(words)])
        self.assertIn('VM_OP_REF_MEM_IND', rpn)

    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': OPTIONS, 'events': [{'command': 'EVENT_DATA_TABLE', 'args': a} for a in c],
                 'eventFiles': {'EVENT_DATA_TABLE': 'eventDataTable.js'}, 'scriptString': 'script_1'} for c in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for events, reference in zip(cases(), json.loads(proc.stdout)['results']):
            with self.subTest(events=events):
                out, stack, script = v3(events)
                self.assertEqual(out, reference['output'])
                self.assertEqual(stack, reference['stack'])
                self.assertEqual(script, reference['script'])


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class DataTableRomTests(unittest.TestCase):
    def test_lookup_in_rom(self):
        from tools.lab_data_table import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
