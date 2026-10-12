"""Explicit NGPC actor ceiling and continuous R-TD native regression gate."""
import copy, json, os
from pathlib import Path
import tempfile, unittest
from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project
from ngpcraft.project import ProjectError, load_project, write_project
from tools.demo_gbs_scene import write_json
from tools.lab_rtd import project_fixture, build, observe

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]

class CapacityProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.project=project_fixture(self.root/'case',30)

    def capacity(self,value):
        write_json(self.project/'ngpcraft.json',{'schema':'ngpcraft-project/1','ngpc':{'actor_capacity':value}})

    def test_closed_integer_range_rejects_boolean_float_string_and_null(self):
        for value in (True,False,None,'30',30.0,19,65,[],{}):
            with self.subTest(value=value):
                self.capacity(value)
                with self.assertRaisesRegex(ProjectError,r'ngpcraft.json.ngpc.actor_capacity: expected integer'):
                    load_project(self.project)

    def test_default_twenty_requires_explicit_ngpc_opt_in(self):
        write_json(self.project/'ngpcraft.json',{'schema':'ngpcraft-project/1'})
        with self.assertRaisesRegex(CompileError,r'30 actors; scene capacity is 20'):
            export_project(self.project,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def test_configured_limit_is_enforced_with_scene_location(self):
        self.capacity(29)
        with self.assertRaisesRegex(CompileError,r'scene.gbsres: 30 actors; scene capacity is 29'):
            export_project(self.project,self.root/'refused')

    def test_thirty_actors_preserve_ids_scripts_and_instance_arrays(self):
        manifest=export_project(self.project,self.root/'export')
        scene=manifest['scene_engine']
        self.assertEqual(len(scene['actors']),31)
        c=(self.root/'export/src/scene_actors.c').read_text(encoding='utf-8')
        self.assertIn('unsigned short act_x[31];',c)
        self.assertIn('static unsigned char act_flags[31];',c)
        self.assertEqual(len(load_project(self.project).scenes[0]['actors']),30)
        written=write_project(load_project(self.project),self.root/'roundtrip')
        self.assertEqual(load_project(written).ngpc['ngpc']['actor_capacity'],30)

    def test_thirty_one_npcs_are_refused_before_export(self):
        path=next((self.project/'project/scenes').rglob('rtd_actor_29.gbsres'))
        data=json.loads(path.read_text(encoding='utf-8'))
        data.update(id='rtd-too-many',_index=30,name='Too many',symbol='actor_too_many')
        write_json(path.parent/'rtd_actor_30.gbsres',data)
        with self.assertRaisesRegex(CompileError,r'31 actors; scene capacity is 30'):
            export_project(self.project,self.root/'refused')

    def test_unused_higher_capacity_emits_identical_c_and_no_additional_unit(self):
        project=project_fixture(self.root/'small',10)
        first=export_project(project,self.root/'default')
        write_json(project/'ngpcraft.json',{'schema':'ngpcraft-project/1','ngpc':{'actor_capacity':30}})
        second=export_project(project,self.root/'higher')
        self.assertEqual(first['features'],second['features'])
        for path in (self.root/'default/src').glob('*'):
            self.assertEqual(path.read_bytes(),(self.root/'higher/src'/path.name).read_bytes(),path.name)

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator and BIOS')
class CapacityRomTests(unittest.TestCase):
    def test_rtd_ten_moving_workers_and_thirty_with_twenty_offscreen(self):
        for count in (10,30):
            with self.subTest(count=count),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'case'
                build(root,count,Path(ENV[0]))
                result=observe(root,Path(ENV[1]),Path(ENV[2]),frames=360)
                self.assertTrue(result['pixels_passed'],result['pixel_errors'][:3])
                # Until the VM optimisations of 2026-10-05 (M2_PERF_PISTES.md)
                # the next-blank wait skipped one overloaded tick here (frame 66).
                # Now one physical frame per tick on the whole route.
                self.assertEqual(result['missed_entries'],[])
                self.assertEqual(result['physical_frames'],result['steady_entries'])
                self.assertEqual(result['commits_over_vblank'],0)
                self.assertEqual(result['commits_started_outside_vblank'],0)
                self.assertGreaterEqual(len(result['camera_positions']),3)
                self.assertEqual(result['activated_script_indices'],list(range(1,11)))

    def test_source_pixel_oracle_detects_wrong_actor_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'case'
            build(root,10,Path(ENV[0]),witness=True)
            result=observe(root,Path(ENV[1]),Path(ENV[2]),frames=90)
            self.assertFalse(result['pixels_passed'])
            self.assertTrue(result['pixel_errors'])
