"""R-PF selection, independent actor motion, source PNG rendering and witness."""
import contextlib,io,json,os,tempfile,unittest
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from tools.lab_rpf import fixture,run,pixel_proof,worker_compare
from ngpcraft.scene_build import build_engine_scene
from tools.demo_gbs_scene import write_json

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]
class RPFSelectionTests(unittest.TestCase):
    def test_workload_has_explicit_platform_flags_and_fixed_pinned_hud(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);project=load_project(fixture(r));actors=project.scenes[0]['actors']
            self.assertEqual(len(actors),15)
            self.assertEqual(sum(a['collisionExtraFlags']==['platform'] for a in actors),8)
            self.assertEqual(sum(bool(a['updateScript']) for a in actors),14)
            self.assertTrue(all(a['persistent'] for a in actors))
            self.assertTrue(actors[-1]['isPinned']);self.assertEqual(actors[-1]['updateScript'],[])
            self.assertTrue(all(a['collisionGroup']=='' for a in actors))
    def test_only_required_modules_and_stacks_are_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);m=export_project(fixture(r),r/'export')
            self.assertEqual(len(m['scene_engine']['actors']),16)
            self.assertEqual(m['scene_engine']['update_contexts'],29)
            self.assertEqual(m['gbvm']['heap_words'],0)
            self.assertEqual(m['gbvm']['stack_budget']['words_per_context'],5)
            self.assertIn('actor_updates',m['features'])
            for feature in ('topdown','platform_callbacks','audio','projectiles','ui','triggers'):
                self.assertNotIn(feature,m['features'])
    def test_removing_updates_removes_the_update_feature(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);p=fixture(r)
            for path in (p/'project/scenes').rglob('actors/*.gbsres'):
                actor=json.loads(path.read_text());actor['updateScript']=[];write_json(path,actor)
            m=export_project(p,r/'export')
            self.assertNotIn('actor_updates',m['features'])
            self.assertNotIn('actor_updates',m['scene_engine'])

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS and Clang')
class RPFNativeTests(unittest.TestCase):
    def test_full_workload_matches_physics_scripts_and_pngs(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            r=Path(tmp)/'proof';result=run(r,*ENV)
            self.assertEqual(result['physics_mismatch_ticks'],0)
            self.assertEqual(result['worker_mismatch_ticks'],0)
            self.assertEqual(result['pixel_mismatch_frames'],0)
            self.assertEqual(result['pixel_checked_frames'],7)
            self.assertEqual(result['activated_update_indices'],list(range(1,15)))
            self.assertEqual(set(result['outside_distinct_positions'].values()),{17})
            self.assertEqual(result['commits_over_vblank'],0)
            self.assertLessEqual(result['max_oam_used'],64)
            self.assertEqual(result['walking_30_60']['grounded_ticks'],30)
            # A renderer defect must fail even if physics is still correct.
            shot=r/'probe/shot-0040.ppm';data=bytearray(shot.read_bytes());data[-3]^=0xFF
            temp=shot.with_suffix('.tmp');temp.write_bytes(data);temp.replace(shot)
            loaded=load_project(r/'project');engine=build_engine_scene(loaded,loaded.scenes[0])
            rows=json.loads((r/'state-proof.json').read_text())['rom']
            plans=json.loads((r/'fixture.json').read_text())['workers']
            _,_,pictures=worker_compare(engine,rows,plans)
            pixels=pixel_proof(r,loaded,engine,rows,pictures,json.loads((r/'probe/probe.json').read_text()))
            self.assertEqual(pixels['errors'],[{'frame':40,'pixels':1}])
    def test_freezing_an_outside_platform_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            result=run(Path(tmp)/'witness',*ENV,frames=120,freeze_outside=True)
            self.assertEqual(result['physics_mismatch_ticks'],0)
            self.assertGreater(result['worker_mismatch_ticks'],100)
            self.assertEqual(result['outside_distinct_positions']['rpf-10'],1)
            self.assertEqual(result['pixel_mismatch_frames'],0)
