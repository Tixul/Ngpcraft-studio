"""Pinned genre extraction, specialization, original-C differential ROMs."""
import contextlib, io, json, os, tempfile, unittest
from pathlib import Path
from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project
from ngpcraft.save_state import classify, SKIP
from tools import extract_genres
from tools.genre_oracle import project_case, run
from tools.lab_genres import CASES, unsigned_sine
from tools.lab_genre_switch import run as run_mixed, project_case as mixed_project

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]

class GenreExportTests(unittest.TestCase):
    def test_frozen_sources_match_published_gbvm_and_have_no_unadapted_names(self):
        for state,path in extract_genres.OUT.items():
            current=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(current,json.loads(json.dumps(extract_genres.extract(state))))
            self.assertEqual(current['unadapted'],[])
            for name in current['public_variables']:
                self.assertEqual(classify(None,name)[0],SKIP,name)

    def test_only_requested_state_is_emitted_and_boot_clear_uses_bytes(self):
        for kind,state in (('shmup','shmup'),('pnc','pointnclick')):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);p,_=project_case(root,kind,keep_actors=False)
                m=export_project(p,root/'out');c=(root/'out/src/scene_actors.c').read_text(encoding='utf-8')
                self.assertIn(state,m['features'])
                for other in ('topdown','platform','shmup','pointnclick'):
                    if other!=state:self.assertNotIn(other,m['features'])
                self.assertEqual(c.count('state_sine[256]'),1)
                if kind=='shmup':
                    self.assertIn('genre_clear(unsigned char *p',c)
                    self.assertNotIn('genre_clear((UBYTE *)&',c)
                else:
                    self.assertNotIn('ngc_shmup_boot',c)
                    self.assertNotIn('coll_map[',c)
                    self.assertNotIn('col_range_x(',c)

    def test_invalid_shmup_define_is_located(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p,_=project_case(root,'shmup',fields={'SHOOTER_MOVEMENT_TYPE':'broken'})
            with self.assertRaisesRegex(CompileError,'SHOOTER_MOVEMENT_TYPE'):export_project(p,root/'out')

    def test_projectiles_share_the_genre_sine_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p,_=project_case(root,'shmup',keep_actors=False,shots=True)
            m=export_project(p,root/'out');c=(root/'out/src/scene_actors.c').read_text(encoding='utf-8')
            self.assertIn('projectiles',m['features'])
            self.assertEqual(c.count('state_sine[256]'),1)
            self.assertNotIn('proj_sine[',c)

    def test_group_contact_bookkeeping_selected_without_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p,_=project_case(root,'shmup',keep_actors=False,actors=[(0,72,'1',None)])
            m=export_project(p,root/'out')
            self.assertIn('collision_update',m['features']);self.assertNotIn('player_collisions',m['features'])
            self.assertNotRegex((root/'out/src/scene_actors.c').read_text(encoding='utf-8'),r'    ngc_vm_hit\(')

    def test_four_scene_states_link_without_unused_callbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=mixed_project(root)
            m=export_project(p,root/'out')
            for state in ('topdown','platform','shmup','pointnclick'):self.assertIn(state,m['features'])
            self.assertNotIn('platform_callbacks',m['features'])
            self.assertNotIn('native_audio',m['features'])

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS and Clang')
class GenreNativeTests(unittest.TestCase):
    def test_rom_positions_camera_contacts_and_clicks_match_original_c(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            for name,case in CASES.items():
                with self.subTest(name=name):
                    r=run(Path(tmp)/name,toolchain=ENV[0],emulator=ENV[1],bios=ENV[2],clang=ENV[3],**case)
                    self.assertTrue(r['passed'],r)
                    if name in ('pnc-trigger','pnc-actor-priority'):self.assertEqual(r['script_starts'],3)
                    if name=='pnc-combined-actor':self.assertEqual(r['script_starts'],2)
                    if name=='pnc-leave-only':self.assertEqual(r['script_starts'],0)
                    if name=='shmup-end':self.assertTrue(r['reached_end'])

    def test_native_unsigned_diagonal_witness_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            r=run(Path(tmp)/'witness',toolchain=ENV[0],emulator=ENV[1],bios=ENV[2],clang=ENV[3],
                  mutate=unsigned_sine,**CASES['pnc-diagonal'])
            self.assertFalse(r['passed']);self.assertGreater(r['mismatches'],100)

    def test_all_four_states_switch_from_topdown_and_from_pnc(self):
        with tempfile.TemporaryDirectory() as tmp:
            for start in (0,3):
                with self.subTest(start=start):
                    r=run_mixed(Path(tmp)/str(start),*ENV[:3],start=start)
                    self.assertTrue(r['passed'],r['visits'])

    def test_genres_save_load_keep_native_music_and_vm_state(self):
        from tools.lab_genre_save import run as run_save
        from test_music_streams import OVER_REV
        with tempfile.TemporaryDirectory() as tmp:
            r=run_save(Path(tmp)/'save',*ENV[:3],OVER_REV)
            self.assertTrue(r['passed'],r)
            self.assertEqual([a[2] for a in r['restores']],[0])

    def test_r_sh_load_reports_oam_overflow_and_records_external_cadence(self):
        from tools.lab_genre_load import run as run_load
        with tempfile.TemporaryDirectory() as tmp:
            r=run_load(Path(tmp)/'load',*ENV[:3])
            self.assertEqual(r['launch_calls'],16)
            self.assertEqual(r['hardware_sprite_demand'],116)
            self.assertFalse(r['qualified_r_sh'])
            self.assertTrue(r['ticks_with_interval_above_one'])

if __name__=='__main__':unittest.main()
