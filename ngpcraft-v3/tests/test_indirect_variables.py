"""Indirect arguments: reference compiler output, aliasing, ROM and selection."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.custom_events import compile_custom_event_script
from ngpcraft.pipeline import export_project
from tools.demo_custom_events import build_case, custom_scripts, project_case
from tools.reference import REFERENCE

ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]
ROOT = Path(__file__).resolve().parents[1]
ARG = {'type': 'argument', 'indirect': True, 'symbol': '.SCRIPT_ARG_INDIRECT_0_VARIABLE'}


class IndirectBuilderTests(unittest.TestCase):
    def test_compile_indirect_custom_event_matches_reference_test(self):
        source = (REFERENCE / 'test/compiler/scriptBuilder.test.ts').read_text(encoding='utf-8')
        case = source.split('test("Should compile a custom event script with indirect variable arg"', 1)[1]
        expected = re.search(r'\.toEqual\(`(.*?)`\);', case, re.S)[1]
        script = {'id': 'script1', 'name': 'Test Script', 'description': '', 'actors': {},
                  'symbol': 'script1', 'variables': {'V0': {'id': 'V0', 'name': 'Variable A',
                                                           'passByReference': True}},
                  'script': [{'command': 'EVENT_INC_VALUE', 'args': {'variable': 'V0'}, 'id': 'event1'}]}
        sb = ScriptBuilder([], {'customEvents': [script], 'scene': {'id': 's', 'actors': []}})
        self.assertEqual(compile_custom_event_script(sb, 'script1'), {'argsLen': 1, 'scriptRef': 'script1'})
        self.assertEqual(sb.options['additionalScripts']['script1']['compiledScript'], expected)

    def test_indirect_argument_alias_matches_reference_test(self):
        sb = ScriptBuilder([], {'argLookup': {'actor': {}, 'variable': {'V0': ARG}}})
        self.assertEqual(sb.get_variable_alias('V0'), ARG['symbol'])

    def test_assignment_all_four_combinations_preserves_stack(self):
        for dest in ('1', ARG):
            for source in ('2', ARG):
                with self.subTest(dest=dest, source=source):
                    sb = ScriptBuilder([], {})
                    sb._set_variable_to_variable(dest, source)
                    self.assertEqual(sb.stack_ptr, 0)
                    text = '\n'.join(sb.output)
                    self.assertEqual('VM_PUSH_VALUE_IND' in text, source == ARG)
                    self.assertEqual('VM_SET_INDIRECT' in text, dest == ARG)
                    if dest == ARG and source == ARG:
                        self.assertIn('^/(.SCRIPT_ARG_INDIRECT_0_VARIABLE - 1)/, .ARG0', text)

    def test_indirect_condition_consumes_dereferenced_value(self):
        sb = ScriptBuilder([], {})
        sb._if_variable_const('.NE', ARG, 0, '1', 0)
        self.assertEqual(sb.stack_ptr, 0)
        self.assertEqual(sb.output, [
            '        VM_PUSH_VALUE_IND       .SCRIPT_ARG_INDIRECT_0_VARIABLE',
            '        VM_IF_CONST             .NE, .ARG0, 0, 1$, 1'])

    def test_constant_assignment_uses_local_not_argument_address(self):
        sb = ScriptBuilder([], {})
        sb._set_variable_const(ARG, -7)
        self.assertEqual(sb.output, [
            '        VM_SET_CONST            .LOCAL_TMP0_VALUE_TMP, -7',
            '        VM_SET_INDIRECT         .SCRIPT_ARG_INDIRECT_0_VARIABLE, .LOCAL_TMP0_VALUE_TMP'])

    def test_forwarding_reference_or_value(self):
        # The inner call must forward the address for a reference parameter,
        # and dereference it for a by-value parameter.
        for by_ref in (False, True):
            place, twice = copy.deepcopy(custom_scripts())
            place['variables']['V0']['passByReference'] = by_ref
            twice['variables']['V0']['passByReference'] = True
            sb = ScriptBuilder([], {'customEvents': [place, twice], 'scene': {'id': 's', 'actors': []}})
            sb.call_script(twice['id'], {'$actor[0]$': 'player', '$variable[V0]$': {'type': 'variable', 'value': '4'}})
            text = sb.options['additionalScripts']['script_twice']['compiledScript']
            expected = 'VM_PUSH_VALUE           ' if by_ref else 'VM_PUSH_VALUE_IND       '
            self.assertIn(expected + '^/(.SCRIPT_ARG_INDIRECT_0_VARIABLE - 2)/', text)

    def test_unused_indirect_runtime_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            export_project(project_case(root), root / 'export')
            text = (root / 'export/src/gbvm.c').read_text()
        for name in ('vm_push_value_ind', 'vm_set_indirect', 'vm_get_indirect', 'case VM_OP_REF_IND',
                     'case VM_OP_REF_SET_IND'):
            self.assertNotIn(name, text)


@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class IndirectRomTests(unittest.TestCase):
    EXPECTED = {'VAR_QUEST3': [36, 28], 'VAR_QUEST4': [11], 'VAR_QUEST5': [22], 'VAR_QUEST6': [14, 15]}

    def run_case(self, mutate=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, _, changes = build_case(root, *ENV, by_reference=True, mutate=mutate)
            proc = subprocess.run([sys.executable, '-B', '-m', 'tools.probe_stream', str(root),
                                   '--emulator', ENV[1], '--bios', ENV[2], '--out', str(root / 'probe')],
                                  capture_output=True, text=True, timeout=600)
            result = json.loads((root / 'probe/probe.json').read_text())
            values = {k: [c['value'] for c in changes.get(k, [])[1:]] for k in self.EXPECTED}
            return proc, result, values

    def test_reference_updates_caller_and_nested_temporary_pixel_exact(self):
        proc, result, values = self.run_case()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(result['frames_with_mismatch'], [])
        self.assertEqual(result['dropped_frames'], [])
        self.assertEqual(values, self.EXPECTED)

    def test_mutation_indirect_read_becomes_direct(self):
        _, result, values = self.run_case(('src/gbvm.c', 'vm_push_value_ind(ctx, (INT16)a);',
                                          'vm_push_value(ctx, (INT16)a);'))
        self.assertTrue(result['frames_with_mismatch'] or values != self.EXPECTED)

    def test_assignment_conditions_wait_and_aliased_arguments_on_rom(self):
        from tools.demo_actor_events import ev, num, wait
        from tools.demo_custom_events import var
        from tools.demo_gbs_scene import write_json
        from ngpcraft.toolchain import build_rom
        def setv(name, value):
            return ev('set-' + name, 'EVENT_SET_VALUE', variable=name, value=value)
        def condition(name, yes, no):
            return {'id': 'if-' + name, 'command': 'EVENT_IF', 'args': {'condition': var(name)},
                    'children': {'true': yes, 'false': no}}
        fn = {'_resourceType': 'script', 'id': 'refs', 'name': 'Refs', 'symbol': 'script_refs',
              'description': '', 'actors': {}, 'variables': {
                  f'V{i}': {'id': f'V{i}', 'name': f'V{i}', 'passByReference': i < 2} for i in range(3)},
              'script': [condition('V0', [setv('2', num(1))], [setv('2', num(-1))]),
                         setv('V0', num(-3)), setv('V1', var('V0')), setv('V0', var('V2')),
                         setv('V2', var('V1')),
                         setv('V1', {'type': 'add', 'valueA': var('V0'), 'valueB': var('V2')}),
                         setv('3', var('V1')), setv('V1', num(0)),
                         condition('V1', [setv('2', num(-2))], [setv('2', num(2))]),
                         ev('wait-ref', 'EVENT_WAIT', units='frames', frames=var('V0')),
                         ev('dec-ref', 'EVENT_DEC_VALUE', variable='V0'), setv('4', var('V2'))]}
        alias = copy.deepcopy(fn)
        alias.update(id='alias', name='Alias', symbol='script_alias')
        alias['variables'].pop('V2')
        alias['script'] = [ev('inc-alias', 'EVENT_INC_VALUE', variable='V0'), setv('1', var('V1'))]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = project_case(root)
            scene_file = next((project / 'project/scenes').rglob('scene.gbsres'))
            scene = json.loads(scene_file.read_text(encoding='utf-8'))
            scene['script'] = [setv('5', num(7)), setv('6', num(-9)),
                ev('call-refs', 'EVENT_CALL_CUSTOM_EVENT', customEventId='refs', **{
                    '$variable[V0]$': var('5'), '$variable[V1]$': var('6'), '$variable[V2]$': var('5')}),
                ev('call-alias', 'EVENT_CALL_CUSTOM_EVENT', customEventId='alias', **{
                    '$variable[V0]$': var('5'), '$variable[V1]$': var('5')})]
            write_json(scene_file, scene)
            for script in (fn, alias):
                write_json(project / 'project/scripts/proof' / (script['symbol'] + '.gbsres'), script)
            export_project(project, root / 'export')
            build_rom(root / 'export', ENV[0])
            subprocess.run([sys.executable, '-B', str(ROOT / 'tools/probe_gbvm.py'), str(root / 'export'),
                            '--emulator', ENV[1], '--bios', ENV[2], '--out', str(root / 'probe'),
                            '--frames', '40'], capture_output=True, check=True, timeout=120)
            changes = json.loads((root / 'probe/probe.json').read_text())['changes']
        self.assertEqual({k: rows[-1]['value'] for k, rows in changes.items()}, {
            'VAR_QUEST2': 7, 'VAR_QUEST3': 2, 'VAR_QUEST4': 4, 'VAR_QUEST5': -3,
            'VAR_QUEST6': 7, 'VAR_QUEST7': 0})
        # The indirect wait must really yield before the decrement and return.
        self.assertGreaterEqual(changes['VAR_QUEST5'][-1]['frame'] - changes['VAR_QUEST3'][-1]['frame'], 7)


if __name__ == '__main__':
    unittest.main()
