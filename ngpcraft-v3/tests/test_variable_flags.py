"""Released whole handlers/compiler, indirect bits and native RAM observations."""
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from ngpcraft.pipeline import export_project
from ngpcraft.scene_collisions import collision_capacity
from tools.demo_actor_moves import ev
from tools.lab_variable_flags import FILES, flags, project_case, run
from tools.lab_event_gaps import branch, set_value
from tools.reference import REFERENCE
ROOT=Path(__file__).resolve().parents[1]
ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]

class VariableFlagTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'),'local TypeScript dependencies required')
    def test_whole_released_handlers_and_compiler_normal_and_indirect(self):
        rng=random.Random(43216)
        masks=[0,65535,0xAAAA,0x5555]+[1<<i for i in range(16)]+[rng.randrange(65536) for _ in range(50)]
        cases=[]
        arg={'type':'argument','symbol':'.SCRIPT_ARG_0_VARIABLE','indirect':True}
        for indirect in (False,True):
            options=({'argLookup':{'variable':[['V0',arg]],'actor':[]}} if indirect else {})
            variable='V0' if indirect else '12'
            for mask in masks:
                for command in ('EVENT_SET_FLAGS','EVENT_ADD_FLAGS','EVENT_CLEAR_FLAGS'):
                    cases.append({'options':options,'events':[ev('flags',command,variable=variable,**flags(mask))]})
            for bit in range(16):
                for disabled in (False,True):
                    event=branch(ev('check','EVENT_IF_FLAGS_COMPARE',variable=variable,flag=bit,__disableElse=disabled),
                                 [set_value(1,11)],[set_value(1,22)])
                    cases.append({'options':options,'events':[event]})
        unusual={f'flag{i+1}':v for i,v in enumerate((False,None,0,'',True,1,'0',[],{},-1))}
        for command in ('EVENT_SET_FLAGS','EVENT_ADD_FLAGS','EVENT_CLEAR_FLAGS'):
            cases.append({'options':{},'events':[ev('truthiness',command,variable='12',**unusual)]})
        for case in cases:case['eventFiles']={**FILES,'EVENT_SET_VALUE':'eventVariableSetToValue.js'}
        proc=subprocess.run(['node',str(ROOT/'tools/actor_coordinates_oracle.cjs'),str(REFERENCE),
            os.environ['NGC_TEST_NODE_DEPS']],input=json.dumps(cases),capture_output=True,text=True,timeout=90)
        self.assertEqual(proc.returncode,0,proc.stderr)
        original=json.loads(proc.stdout)['results']
        clean=lambda lines:[s for s in lines if s.strip() and not s.lstrip().startswith(';')]
        for case,reference in zip(cases,original):
            with self.subTest(events=case['events'],options=case['options']):
                options=dict(case['options'])
                if 'argLookup' in options:options['argLookup']={k:dict(v) for k,v in options['argLookup'].items()}
                out=[];b=ScriptBuilder(out,options)
                compile_events(b,case['events'],['script[0]'],[]);b._pack_locals()
                self.assertEqual(clean(out),clean(reference['output']))
                self.assertEqual(b.stack_ptr,reference['stack'])
        self.assertEqual(len(original),487)

    def test_invalid_flag_index_refused_with_event_location(self):
        for index in (-1,16,1.5,'0',None,True,False,[],{},float('nan'),float('inf')):
            with self.subTest(index=index),self.assertRaisesRegex(CompileError,r'script\[0\]: EVENT_IF_FLAGS_COMPARE: flag index'):
                compile_events(ScriptBuilder([],{}),[ev('bad','EVENT_IF_FLAGS_COMPARE',variable='1',flag=index)],['script[0]'],[])

    def test_collision_bounds_and_disabled_else(self):
        events=[ev('set','EVENT_SET_FLAGS',variable='0',**flags(65535)),
                ev('clear','EVENT_CLEAR_FLAGS',variable='0',**flags(32768)),
                ev('add','EVENT_ADD_FLAGS',variable='0',**flags(32768)),
                branch(ev('check','EVENT_IF_FLAGS_COMPARE',variable='0',flag=15),[],[])]
        script=SimpleNamespace(entity={'hit1Script':events},branches=[(1,'hit1Script')],path='actors/a.gbsres')
        self.assertEqual(collision_capacity([script]),4)
        disabled=branch(ev('check','EVENT_IF_FLAGS_COMPARE',variable='0',flag=0,__disableElse=True),[],
                        [ev('unknown','EVENT_NOT_REAL')])
        compile_events(ScriptBuilder([],{}),[disabled],['script[0]'],[])
        disabled['args']['__disableElse']=False
        with self.assertRaisesRegex(CompileError,r'script\[0\].children.false\[0\]'):
            compile_events(ScriptBuilder([],{}),[disabled],['script[0]'],[])

    def test_no_new_runtime_or_actor_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project,_=project_case(root)
            report=export_project(project,root/'export')
            for feature in ('actor_positions','actor_spritesheets','actor_anim_states','save'):
                self.assertNotIn(feature,report['features'])
            self.assertLess(report['gbvm']['stack_budget']['words_per_context'],64)
            self.assertNotIn('ngc_actor_set_spritesheet',(root/'export/src/scene_actors.c').read_text())

@unittest.skipUnless(all(ENV),'requires native emulator, BIOS and Toshiba')
class VariableFlagRomTests(unittest.TestCase):
    def test_all_sixteen_bits_mixed_masks_and_indirect_custom_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=run(Path(tmp)/'proof',*ENV)
            self.assertEqual(report['mismatches'],{})
            self.assertEqual(report['actual'][137],32770)

    def test_neutralized_add_mask_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=run(Path(tmp)/'witness',*ENV,witness=True)
            self.assertGreater(len(report['mismatches']),16)

if __name__=='__main__':unittest.main()
