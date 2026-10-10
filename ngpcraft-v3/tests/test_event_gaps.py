"""Missing events: released handlers/compiler, localized refusal and native probes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS, compile_events
from ngpcraft.pipeline import export_project
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_event_gaps import branch, flow_project, player_project, run_flow, run_player, run_control, var, value
from tools.reference import REFERENCE

ROOT=Path(__file__).resolve().parents[1]
FILES={'EVENT_PLAYER_SET_SPRITE':'eventPlayerSetSprite.js',
       'EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR':'eventIfActorDistanceFromActor.js',
       'EVENT_LOOP_WHILE':'eventLoopWhile.js','EVENT_LOOP_FOR':'eventLoopFor.js',
       'EVENT_SCRIPT_LOCK':'eventScriptLock.js','EVENT_SCRIPT_UNLOCK':'eventScriptUnlock.js','EVENT_IDLE':'eventIdle.js',
       'EVENT_INC_VALUE':'eventVariableInc.js','EVENT_SET_VALUE':'eventVariableSetToValue.js'}
ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]

def clean(lines):
    return [line for line in lines if line.strip() and not line.lstrip().startswith(';')]

class EventGapCompilerTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'),'requires local TypeScript dependencies')
    def test_released_handlers_and_complete_compiler(self):
        cases=[]
        scene={'actors':[{'id':'a'},{'id':'b'}]}
        options={'scene':scene,'sprites':[{'id':'s','symbol':'sprite_s'}]}
        cases.append({'options':options,'events':[ev('player','EVENT_PLAYER_SET_SPRITE',spriteSheetId='s')]})
        values=[num(0),num(5),num(-181),num(32767),var(12),
                value('add',var(12),num(1)),{'type':'property','target':'a','property':'xpos'}]
        for op in ('==','!=','<','>','<=','>='):
            for distance in values:
                for actor,other in (('player','a'),('b','player'),('a','a')):
                    event=branch(ev('distance','EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR',actorId=actor,
                        otherActorId=other,distance=distance,operator=op),
                        [ev('true','EVENT_INC_VALUE',variable='1')],[ev('false','EVENT_INC_VALUE',variable='2')])
                    cases.append({'options':options,'events':[event]})
        for condition in (num(0),num(1),var(12),value('lt',var(12),num(8)),
                          {'type':'expression','value':'$12$<3'}):
            event=branch(ev('while','EVENT_LOOP_WHILE',condition=condition),[ev('inc','EVENT_INC_VALUE',variable='12')])
            cases.append({'options':options,'events':[event]})
        for comparison in ('==','!=','<','>','<=','>='):
            for operation in ('+=','-=','*=','/=','%='):
                for bound in (num(4),var(5),value('add',var(5),num(1))):
                    event=branch(ev('for','EVENT_LOOP_FOR',variable='12',
                        **{'from':num(1),'to':bound,'comparison':comparison,'operation':operation,'value':var(6)}),
                        [ev('inc','EVENT_INC_VALUE',variable='1')])
                    cases.append({'options':options,'events':[event]})
        # A nested while/for exercises label ordering and lifetimes.
        nested=branch(ev('outer','EVENT_LOOP_WHILE',condition=var(12)),
            [branch(ev('inner','EVENT_LOOP_FOR',variable='1',
                **{'from':num(0),'to':num(2),'comparison':'<','operation':'+=','value':num(1)}),
                [ev('inc','EVENT_INC_VALUE',variable='2')])])
        cases.append({'options':options,'events':[nested]})
        for command in ('EVENT_SCRIPT_LOCK','EVENT_SCRIPT_UNLOCK','EVENT_IDLE'):
            cases.append({'options':options,'events':[ev(command,command)]})
        cases.append({'options':options,'events':[ev(str(i),command) for i,command in enumerate(
            ('EVENT_SCRIPT_LOCK','EVENT_SCRIPT_LOCK','EVENT_IDLE','EVENT_SCRIPT_UNLOCK','EVENT_IDLE',
             'EVENT_SCRIPT_UNLOCK','EVENT_SCRIPT_UNLOCK'))]})
        for case in cases: case['eventFiles']=FILES
        proc=subprocess.run(['node',str(ROOT/'tools/actor_coordinates_oracle.cjs'),str(REFERENCE),
                             os.environ['NGC_TEST_NODE_DEPS']],input=json.dumps(cases),capture_output=True,text=True,timeout=90)
        self.assertEqual(proc.returncode,0,proc.stderr)
        results=json.loads(proc.stdout)['results']
        for case,reference in zip(cases,results):
            with self.subTest(events=case['events']):
                output=[];b=ScriptBuilder(output,case['options'])
                compile_events(b,case['events'],[f'script[{i}]' for i in range(len(case['events']))],[])
                b._pack_locals()
                self.assertEqual(clean(output),clean(reference['output']))
                self.assertEqual(b.stack_ptr,reference['stack'])
        self.assertEqual(len(results),227)

    def test_player_event_selects_zero_even_from_actor_context(self):
        output=[];b=ScriptBuilder(output,{'scene':{'actors':[{'id':'a'}]},'sprites':[{'id':'s','symbol':'sprite_s'}]})
        b.actor_set_active('a')
        HANDLERS['EVENT_PLAYER_SET_SPRITE']({'spriteSheetId':'s'},b)
        text='\n'.join(output)
        self.assertIn('VM_SET_CONST            .LOCAL_ACTOR, 0',text)
        self.assertIn('VM_ACTOR_SET_SPRITESHEET .LOCAL_ACTOR, ___bank_sprite_s, _sprite_s',text)

    def test_disabled_else_and_locations_inside_each_new_branch(self):
        events=[branch(ev('distance','EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR',actorId='player',
                    otherActorId='player',distance=num(0),operator='==',__disableElse=True),[],
                    [ev('unsupported','EVENT_NOT_REAL')]),
                branch(ev('while','EVENT_LOOP_WHILE',condition=num(0)),[ev('unsupported','EVENT_NOT_REAL')]),
                branch(ev('for','EVENT_LOOP_FOR',variable='1',
                    **{'from':num(0),'to':num(1),'comparison':'<','operation':'+=','value':num(1)}),
                    [ev('unsupported','EVENT_NOT_REAL')])]
        compile_events(ScriptBuilder([],{'scene':{'actors':[]}}),events[:1],['script[0]'],[])
        for event in events[1:]:
            with self.assertRaisesRegex(CompileError,r'script\[0\].children.true\[0\].*EVENT_NOT_REAL'):
                compile_events(ScriptBuilder([],{'scene':{'actors':[]}}),[event],['script[0]'],[])

    def test_invalid_comparisons_steps_and_missing_asset_are_localized(self):
        cases=[ev('bad-distance','EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR',operator='near'),
               ev('bad-for','EVENT_LOOP_FOR',comparison='<',operation='pow'),
               ev('bad-compare','EVENT_LOOP_FOR',comparison='between',operation='+='),
               ev('bad-sprite','EVENT_PLAYER_SET_SPRITE',spriteSheetId='missing')]
        for event in cases:
            with self.assertRaisesRegex(CompileError,r'script\[0\]: '+event['command']):
                compile_events(ScriptBuilder([],{'scene':{'actors':[]}}),[event],['script[0]'],[])

    def test_asset_and_position_runtime_use_existing_reachability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            player=export_project(player_project(root/'player'),root/'player-export')
            flow,_=flow_project(root/'flow')
            manifest=export_project(flow,root/'flow-export')
            self.assertIn('actor_spritesheets',player['features'])
            self.assertNotIn('actor_spritesheets',manifest['features'])
            self.assertIn('actor_positions',manifest['features'])
            self.assertLess(manifest['gbvm']['stack_budget']['words_per_context'],64)
            self.assertNotIn('ngc_actor_set_spritesheet',(root/'flow-export/src/scene_actors.c').read_text())

    def test_collision_capacity_accepts_bounded_new_events_and_sizes_loops_to_the_maximum(self):
        from ngpcraft.scene_collisions import collision_capacity
        events=[ev('sprite','EVENT_PLAYER_SET_SPRITE',spriteSheetId='s'),
                ev('lock','EVENT_SCRIPT_LOCK'),ev('unlock','EVENT_SCRIPT_UNLOCK'),
                branch(ev('distance','EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR',actorId='player',
                          otherActorId='a',distance=num(5),operator='<'),
                       [ev('idle','EVENT_IDLE')],[ev('idle-else','EVENT_IDLE')])]
        from ngpcraft.scene_collisions import UNBOUNDED_CONTEXTS
        def capacity(script, warnings=None):
            return collision_capacity([SimpleNamespace(entity={'hit1Script':script},
                branches=[(1,'hit1Script')],path='actors/a.gbsres')], None, warnings)
        self.assertEqual(capacity(events),6)
        # A loop has no static lifetime: the VM maximum (fit_contexts keeps what the
        # RAM allows, GBVM's behaviour when full), with a located warning.
        for command in ('EVENT_LOOP','EVENT_LOOP_WHILE','EVENT_LOOP_FOR'):
            warnings=[]
            self.assertEqual(capacity([ev('loop',command)],warnings),UNBOUNDED_CONTEXTS)
            self.assertRegex(warnings[0],r'actors/a\.gbsres:hit1Script\[0\]: '+command+': loop: .*unbounded')

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS and Clang')
class EventGapRomTests(unittest.TestCase):
    def test_idle_nested_locks_extra_unlock_and_automatic_release_in_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run_control(Path(tmp)/'control',*ENV[:3])
            self.assertEqual(result['mismatches'],[])
            self.assertEqual(result['dropped_frames'],[])

    def test_distance_and_live_loops_including_indirect_variable_in_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run_flow(Path(tmp)/'flow',*ENV[:3])
            self.assertEqual(result['mismatches'],{})

    def test_player_sheet_transitions_match_original_c_and_pngs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run_player(Path(tmp)/'player',*ENV)
            self.assertEqual(result['pixel_mismatch_frames'],0,result['first'])
            self.assertEqual(result['phases_seen'],list(range(1,9)))
            self.assertEqual(result['dropped_frames'],[])

    def test_player_sheet_noop_is_detected_by_screen_witness(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run_player(Path(tmp)/'witness',*ENV,frames=180,witness='sheet')
            self.assertGreater(result['pixel_mismatch_frames'],0)

if __name__=='__main__': unittest.main()
