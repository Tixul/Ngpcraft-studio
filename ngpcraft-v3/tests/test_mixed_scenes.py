"""Mixed state engines, distinct maps/views and mutation witnesses."""
import json,os,tempfile,unittest
from pathlib import Path
from ngpcraft.pipeline import export_project
from tools.lab_mixed_scenes import project_case,run

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]
class MixedSelectionTests(unittest.TestCase):
    def test_both_engines_distinct_views_and_only_one_callback_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);p=project_case(r);m=export_project(p,r/'export')
            self.assertIn('platform',m['features']);self.assertIn('topdown',m['features'])
            self.assertEqual([s['view']['height'] for s in m['scene_engine']['scenes']],[144,152])
            c=(r/'export/src/gbvm.c').read_text(encoding='utf-8');self.assertIn('ngc_plat_events[1]',c)
            self.assertEqual(m['scene_engine']['contexts'],5)
            self.assertIn('#define VM_MAX_CONTEXTS 5',c)
            self.assertNotIn('actor_updates',m['features'])
    def test_no_callbacks_means_no_table_in_mixed_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);p=project_case(r)
            for f in (p/'project/scenes').rglob('scene.gbsres'):
                s=json.loads(f.read_text(encoding='utf-8'));s['script']=[e for e in s['script'] if e['command']!='EVENT_SET_PLATFORMER_CALLBACK_SCRIPT']
                f.write_text(json.dumps(s),encoding='utf-8')
            m=export_project(p,r/'export');self.assertNotIn('platform_callbacks',m['features'])
            self.assertNotIn('ngc_plat_events',(r/'export/src/gbvm.c').read_text(encoding='utf-8'))

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS and Clang')
class MixedROMTests(unittest.TestCase):
    def checked(self,start=0,grid=8,frames=900):
        with tempfile.TemporaryDirectory() as tmp:result=run(Path(tmp)/'proof',*ENV,frames=frames,start=start,grid=grid)
        for key in ('physics_mismatch_ticks','camera_mismatch_ticks','jump_callback_mismatch_segments','pixel_mismatch_frames'):self.assertEqual(result[key],0,(key,result))
        self.assertEqual(result['scroll_mismatch_frames'],[]);self.assertEqual(result['dropped_gameplay_frames'],[])
        self.assertEqual(result['views'],[[144,4],[152,0]])
        self.assertEqual([v['frames'] for v in result['transition_frames']],[3]*len(result['transitions']))
        return result
    def test_platform_boot_then_four_mixed_transitions(self):
        r=self.checked();self.assertEqual(r['transitions'],[207,407,607,807]);self.assertEqual(r['scene_visits'],[3,2])
        self.assertGreater(r['jump_events'],0);self.assertGreater(r['pixel_checked_frames'],800)
    def test_topdown_boot_initializes_platform_fields_before_first_visit(self):
        r=self.checked(start=1);self.assertEqual(r['transitions'],[207,407,607,807]);self.assertEqual(r['scene_visits'],[2,3])
        self.assertGreater(r['jump_events'],0)
    def test_sixteen_pixel_snap_and_return_to_platformer(self):
        r=self.checked(grid=16,frames=420);self.assertEqual(r['transitions'],[207,407])
    def test_missing_topdown_init_is_detected_by_position_and_camera(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,frames=260,witness='topdown_init',grid=16)
        self.assertGreater(r['physics_mismatch_ticks'],0);self.assertGreater(r['camera_mismatch_ticks'],0)
    def test_previous_decor_witness_is_detected_by_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,frames=260,witness='decor')
        self.assertEqual(r['physics_mismatch_ticks'],0);self.assertGreater(r['pixel_mismatch_frames'],0)

if __name__=='__main__':unittest.main()
