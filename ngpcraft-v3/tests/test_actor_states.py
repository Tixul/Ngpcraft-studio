"""Actor animation states: original helpers, specialized exports and Platformer ROM."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json
from tools.platform_oracle import project_case as plain_platform
from tools.lab_actor_states import project_case, run
from tools.reference import REFERENCE

ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]

class ActorStateTests(unittest.TestCase):
    def test_reference_compiler_helper_order_and_loop_mask(self):
        source=(REFERENCE/'src/lib/events/eventActorSetState.js').read_text()
        self.assertIn('actorSetActive(input.actorId);\n  actorSetState(input.spriteStateId, input.loopAnim);',source)
        for value,flags in [(True,0),(False,4),(None,4)]:
            out=[]
            b=ScriptBuilder(out,{'scene':{'actors':[{'id':'a'}]},'statesOrder':['','Explode'],
                                 'stateReferences':['0','1']})
            HANDLERS['EVENT_ACTOR_SET_STATE']({'actorId':'a','spriteStateId':'Explode','loopAnim':value},b)
            self.assertIn('        VM_ACTOR_SET_ANIM_SET   .LOCAL_ACTOR, 1',out)
            self.assertIn(f'        VM_ACTOR_SET_FLAGS      .LOCAL_ACTOR, {flags}, 4',out)

    def test_unused_has_no_new_unit_or_animation_ram(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            m=export_project(plain_platform(root),root/'out')
            self.assertNotIn('actor_anim_states',m['features'])
            self.assertNotIn('act_state_anims',(root/'out/src/scene_actors.c').read_text())

    def test_project_global_lookup_absent_sheet_state_and_memory_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            m=export_project(project_case(root),root/'out')
            self.assertIn('actor_anim_states',m['features'])
            self.assertEqual(m['scene_engine']['actor_animation_states']['ram_bytes'],32)
            source=(root/'out/src/scene_actors.c').read_text()
            self.assertIn('act_state_anims[32]',source)
            self.assertIn('actor_load_anim_set(0u, set);',source)
            self.assertLess(m['gbvm']['stack_budget']['words_per_context'],64)

    def test_unknown_global_state_has_localized_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); project=project_case(root)
            path=next((project/'project/scenes').rglob('actors/*.gbsres'))
            actor=json.loads(path.read_text()); actor['updateScript'][0]['args']['spriteStateId']='NotAState'
            write_json(path,actor)
            with self.assertRaisesRegex(CompileError,r'updateScript\[0\]: EVENT_ACTOR_SET_STATE: animation state'):
                export_project(project,root/'out')

    def test_topdown_and_multiple_scene_exports_share_the_actor_event(self):
        from tools.lab_scene_cuts import project_case as cuts
        from ngpcraft.toolchain import build_rom
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); project,_=cuts(root)
            for sf in project.rglob('scene.gbsres'):
                scene=json.loads(sf.read_text())
                scene['script'].insert(0,ev('set-state','EVENT_ACTOR_SET_STATE',actorId='player',spriteStateId='',loopAnim=True))
                write_json(sf,scene)
            m=export_project(project,root/'out')
            self.assertIn('actor_anim_states',m['features'])
            if ENV[0]: build_rom(root/'out',ENV[0])

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS, Clang and Node')
class ActorStateRomTests(unittest.TestCase):
    def test_platformer_poses_loop_nonloop_repeat_and_missing_sheet_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run(Path(tmp)/'proof',*ENV,frames=520)
            self.assertEqual(result['pixel_mismatch_frames'],0,result['first'])
            self.assertEqual(result['physics_mismatch_ticks'],0,result['physics_first'])
            self.assertEqual(result['dropped_frames'],[])
            self.assertEqual(result['phases_seen'],list(range(1,8)))
            self.assertGreater(len(result['poses_by_phase']['1']),1)
            self.assertEqual(result['poses_by_phase']['6'],[max(result['poses_by_phase']['5'])])
            self.assertEqual([v for _,v in result['phase_transitions'][:8]],[1,2,3,4,5,6,7,1])

    def test_missing_state_load_witness_is_detected_in_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run(Path(tmp)/'witness',*ENV,frames=300,witness='state')
            self.assertGreater(result['pixel_mismatch_frames'],0)

    def test_missing_nonloop_flag_witness_is_detected_in_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run(Path(tmp)/'witness',*ENV,frames=300,witness='flags')
            self.assertGreater(result['pixel_mismatch_frames'],0)

if __name__=='__main__': unittest.main()
