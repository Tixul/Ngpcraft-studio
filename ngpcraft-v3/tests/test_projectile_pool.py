"""Original pool/update/contact C, valid NGPC extensions and target syntax."""
from dataclasses import replace
import os,tempfile,unittest
from pathlib import Path
from ngpcraft.compiler import CompileError
from ngpcraft.projectile_runtime import projectile_source,projectile_ram_bytes,projectile_collision_source
from tools.lab_projectile_pool import run,compile_kernel,definitions,FRAMES,measure_pool_ram,native_run

CLANG=os.environ.get('NGC_TEST_CLANG')
TOSHIBA=os.environ.get('NGC_TEST_TOSHIBA')
EMULATOR=os.environ.get('NGC_TEST_EMULATOR')
BIOS=os.environ.get('NGC_TEST_BIOS')


class ProjectilePoolSourceTests(unittest.TestCase):
    def test_resolved_data_has_located_errors_and_never_clamps(self):
        defs=definitions()
        for field,value in (('speed_subpx',256),('speed_subpx',-1),('life_frames',65536),
                            ('initial_offset_subpx',65536),('animation_tick_mask',256),
                            ('collision_mask',False),('loop_animation',1),('bounds_subpx',(0,-1,0,1)),
                            ('animations',((0,256),)*4),('bounds_subpx',None),('animations',None),('animations',(None,)*4)):
            with self.subTest(field=field,value=value):
                changed=(replace(defs[0],**{field:value}),)
                with self.assertRaisesRegex(CompileError,r'projectile probe:definitions\[0\]'):
                    projectile_source(changed,FRAMES,capacity=5,width_tiles=20,height_tiles=19,where='projectile probe')
        for capacity in (0,256,True):
            with self.subTest(capacity=capacity),self.assertRaises(CompileError):projectile_ram_bytes(capacity)

    def test_capacity_is_compiler_input_and_ram_cost_matches_field_sizes(self):
        for capacity in (1,5,17,255):
            with self.subTest(capacity=capacity):
                source='\n'.join(projectile_source(definitions(),FRAMES,capacity=capacity,width_tiles=20,height_tiles=19))
                self.assertIn(f'proj_x[{capacity}]',source)
                self.assertEqual(projectile_ram_bytes(capacity),29*capacity+2)
                self.assertNotIn('ngc_sprites_begin',source);self.assertNotIn('ngc_sprites_end',source)
        # Duration and logical order indexes extend the reference's byte fields.
        d=replace(definitions()[0],life_frames=600,animations=((256,259),)*4)
        source=projectile_source((d,),tuple(range(300)),capacity=17,width_tiles=20,height_tiles=19)
        self.assertIn('        proj_life[i] = 600u;',source)
        with self.assertRaisesRegex(CompileError,'groups\[0\]'):
            projectile_collision_source((256,),'actor bridge')

    @unittest.skipUnless(TOSHIBA,'requires user-provided Toshiba compiler')
    def test_pool_actor_bridge_and_render_compile_with_cc900(self):
        with tempfile.TemporaryDirectory() as tmp:
            for capacity in (5,17,255):
                with self.subTest(capacity=capacity):
                    r=compile_kernel(Path(tmp)/str(capacity),TOSHIBA,capacity)
                    self.assertEqual(r['returncode'],0,r['output']);self.assertTrue(r['object_exists'])

    @unittest.skipUnless(TOSHIBA,'requires user-provided Toshiba compiler')
    def test_actual_target_abi_ram_cost_from_generated_field_declarations(self):
        for capacity in (5,17,255):
            with self.subTest(capacity=capacity):
                r=measure_pool_ram(TOSHIBA,capacity)
                self.assertEqual(r['sizes_bytes']['pool'],projectile_ram_bytes(capacity))
                self.assertTrue(r['toolchain_unchanged'])


@unittest.skipUnless(CLANG,'requires Clang and Node')
class ProjectilePoolOracleTests(unittest.TestCase):
    def test_released_five_slot_pool_update_collisions_and_draw_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=run(Path(tmp)/'proof',CLANG)
            self.assertEqual(r['returncode'],0,r['stderr']);self.assertGreater(r['checked'],90000)
            self.assertGreater(r['hitCalls'],30);self.assertGreater(r['rendered'],10000)
            self.assertEqual(r['hitActors'],15)

    def test_larger_ngpc_pool_uses_the_same_list_and_lifetime_algorithm(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=run(Path(tmp)/'proof',CLANG,17)
            self.assertEqual(r['returncode'],0,r['stderr']);self.assertEqual(r['capacity'],17)

    def test_six_semantic_mutations_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for witness in ('duration','spread','first-overlap','strong','nonloop','pool-order'):
                with self.subTest(witness=witness):
                    r=run(Path(tmp)/witness,CLANG,witness=witness)
                    self.assertNotEqual(r['returncode'],0,r);self.assertIn('expected',r['stderr'])


@unittest.skipUnless(all((CLANG,TOSHIBA,EMULATOR,BIOS)),'requires Clang, Toshiba, native emulator and BIOS')
class ProjectilePoolNativeTests(unittest.TestCase):
    def test_real_tlcs900_arithmetic_and_state_with_noisy_ram(self):
        with tempfile.TemporaryDirectory() as tmp:
            for capacity in (5,17):
                with self.subTest(capacity=capacity):
                    r=native_run(Path(tmp)/str(capacity),TOSHIBA,EMULATOR,BIOS,CLANG,capacity)
                    self.assertEqual(r['mismatch_operations'],0,r['first_errors'])
                    self.assertGreater(r['operations'],800)
                    self.assertEqual(r['hit_actors_mask'],15)


if __name__=='__main__':unittest.main()
