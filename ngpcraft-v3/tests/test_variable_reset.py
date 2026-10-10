"""Compare released compiler and execute heap reset with live custom arguments."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from ngpcraft.gbvm.assembler import assemble_bank
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from ngpcraft.scene_collisions import collision_capacity
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json
from tools.lab_variable_reset import FILES, project_case, run
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


class VariableResetTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_whole_released_handler_and_compiler(self):
        cases = [{'options': {}, 'eventFiles': FILES,
                  'events': [ev('reset', 'EVENT_RESET_VARIABLES', **args)]}
                 for args in ({}, {'ignored': 5})]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'),
                               str(REFERENCE), os.environ['NGC_TEST_NODE_DEPS']],
                              input=json.dumps(cases), capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for case, reference in zip(cases, json.loads(proc.stdout)['results']):
            out = []
            builder = ScriptBuilder(out, {})
            compile_events(builder, case['events'], ['script[0]'], [])
            self.assertEqual(out, reference['output'])
            self.assertEqual(builder.stack_ptr, reference['stack'])

    def test_heap_count_is_resolved_at_link_for_empty_and_nonempty_heap(self):
        text = '.module reset\n_reset::\n        VM_MEMSET 0, 0, MAX_GLOBAL_VARS\n        VM_STOP\n'
        for count in (0, 1, 257):
            data, _, macros, _ = assemble_bank([text], {f'VAR_{i}': i for i in range(count)})
            self.assertEqual(data[1:7], bytes((count >> 8, count & 255, 0, 0, 0, 0)))
            self.assertIn('VM_MEMSET', macros)

    def test_collision_callback_is_bounded(self):
        script = SimpleNamespace(entity={'hit1Script': [ev('reset', 'EVENT_RESET_VARIABLES')]},
                                 branches=[(1, 'hit1Script')], path='actors/reset.gbsres')
        self.assertEqual(collision_capacity([script]), 4)

    def test_reset_handler_selected_only_when_used_and_stack_bound_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root)
            report = export_project(project, root / 'reset')
            source = (root / 'reset/src/gbvm.c').read_text()
            self.assertIn('static void vm_memset(', source)
            self.assertNotIn('for (INT16', source)
            self.assertLess(report['gbvm']['stack_budget']['words_per_context'], 64)
            custom = project / 'project/scripts/reset_live_args.gbsres'
            res = json.loads(custom.read_text())
            res['script'] = [e for e in res['script'] if e['command'] != 'EVENT_RESET_VARIABLES']
            write_json(custom, res)
            export_project(project, root / 'control')
            self.assertNotIn('vm_memset', (root / 'control/src/gbvm.c').read_text())


@unittest.skipUnless(all(ENV), 'native emulator, BIOS and Toshiba required')
class VariableResetRomTests(unittest.TestCase):
    def test_zero_variable_rom_builds_without_actor_or_ui_support(self):
        from gbs_fixture import make_project
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_project(root / 'project', [ev('reset', 'EVENT_RESET_VARIABLES')])
            report = export_project(project, root / 'export')
            self.assertEqual(report['gbvm']['heap_words'], 0)
            source = (root / 'export/src/gbvm.c').read_text()
            self.assertIn('static void vm_memset', source)
            self.assertNotIn('VM_REF_TO_PTR', source)
            build_rom(root / 'export', ENV[0])

    def test_reset_and_custom_argument_lifetime(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run(Path(tmp) / 'proof', *ENV)
            self.assertEqual(report['mismatches'], {})
            self.assertEqual(report['actual'][105], 123)
            self.assertEqual(report['actual'][137], 9)

    def test_missing_reset_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run(Path(tmp) / 'witness', *ENV, witness=True)
            self.assertGreaterEqual(len(report['mismatches']), 5)


if __name__ == '__main__':
    unittest.main()
