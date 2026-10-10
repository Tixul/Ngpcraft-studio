"""Selected decoders, integer aiming, real GBVM hit handles and native probes."""
import json,os,tempfile,unittest
from pathlib import Path
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.emit import RUNTIME,gbvm_source
from ngpcraft.gbvm.assembler import load_model
from ngpcraft.gbvm.projectiles import hit_support,atan2_source
from ngpcraft.runtime_units import select_units
from tools.extract_projectile_vm import extract,OUT
from tools.lab_projectile_vm import run,program

CLANG=os.environ.get('NGC_TEST_CLANG');TOSHIBA=os.environ.get('NGC_TEST_TOSHIBA')
EMULATOR=os.environ.get('NGC_TEST_EMULATOR');BIOS=os.environ.get('NGC_TEST_BIOS')


class ProjectileVmTests(unittest.TestCase):
    def test_frozen_math_matches_released_source_and_is_selected_only_on_demand(self):
        self.assertEqual(json.loads(OUT.read_text(encoding='utf-8')),extract())
        source,_=program()
        self.assertIn('ngc_atan2((INT16)*A, (INT16)*B)',source)
        runtime=json.loads(RUNTIME.read_text(encoding='utf-8'))
        absent,_=gbvm_source(runtime,set(),set(),[],[],load_model(),heap_words=0)
        for name in ('ngc_atan2','atan2_table','vm_projectile_launch','vm_actor_get_angle',
                     'ngc_projectile_handles','script_detach_hthread'):
            self.assertNotIn(name,absent)
        self.assertNotIn('gbvm_projectile_hits',[u.name for u in select_units(['gbvm'],'rom')])
        self.assertIn('gbvm_projectile_hits',[u.name for u in select_units(['projectile_hits'],'rom')])

    def test_stack_bounds_cover_all_launch_shapes_and_hit_argument(self):
        _,details=program();stack=details['stack']
        self.assertEqual(stack['words_per_context'],12)
        self.assertEqual(stack['scripts']['hit']['words'],3)
        for i in range(5):self.assertEqual(stack['scripts']['launch_'+str(i)]['words'],7)
        self.assertEqual(stack['scripts']['launch_5']['words'],12)
        self.assertTrue(all(s['method']=='bytecode_cfg' for s in stack['scripts'].values()))
        for count in (0,256,True,None):
            with self.subTest(count=count),self.assertRaises(CompileError):hit_support(count)

    @unittest.skipUnless(CLANG and TOSHIBA,'user-provided clang and Toshiba required')
    def test_original_math_and_real_vm_launches_and_hit_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof=run(Path(tmp)/'proof',CLANG,TOSHIBA)
            self.assertEqual(proof['returncode'],0,proof['stderr'])
            self.assertEqual(proof['math'],1181013);self.assertEqual(proof['launches'],7776)
            self.assertEqual(proof['mismatches'],0);self.assertEqual(proof['hit_handles'],'passed')
            self.assertEqual(proof['toshiba']['returncode'],0,proof['toshiba']['output'])
            self.assertTrue(proof['toshiba']['object_exists'])

    @unittest.skipUnless(CLANG,'user-provided clang required')
    def test_three_mutations_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for witness,diagnostic in (('angle-byte','launch'),('atan2-quadrant','atan2'),('busy-handle','hit handles 6')):
                with self.subTest(witness=witness):
                    proof=run(Path(tmp)/witness,CLANG,witness=witness)
                    self.assertNotEqual(proof['returncode'],0)
                    self.assertIn(diagnostic,proof['stderr'])

    @unittest.skipUnless(all((CLANG,TOSHIBA,EMULATOR,BIOS)),'native core, BIOS and compilers required')
    def test_native_vm_launches_scheduler_and_actor_handles(self):
        from tools.lab_projectile_vm import native_run
        with tempfile.TemporaryDirectory() as tmp:
            proof=native_run(Path(tmp)/'native',TOSHIBA,EMULATOR,BIOS,CLANG)
            self.assertEqual(proof['mismatch_operations'],0,proof['first_errors'])
            self.assertGreater(proof['operations'],100)
            self.assertTrue(proof['real_scheduler'])


if __name__=='__main__':unittest.main()
