"""Camera shake compiler contract, specialized absence, original C and native ROM."""
import os,tempfile,unittest,re,subprocess,json
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.gbvm.builder import ScriptBuilder,NotDelivered
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.compiler import CompileError
from tools.platform_oracle import project_case as plain
from tools.lab_camera_shake import project_case,run,reference_source
ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]
class CameraShakeTests(unittest.TestCase):
    def test_duration_defaults_directions_and_zero_noop(self):
        class B:
            def camera_shake_script_value(self,*args):self.args=args
        for args,expected in [({'units':'frames','frames':12,'shakeDirection':'vertical'},(False,True,12)),
                              ({'units':'frames','frames':{'type':'number','value':2}},(True,False,30)),
                              ({'time':0.21,'shakeDirection':'diagonal'},(True,True,13)),({},(True,False,30))]:
            b=B();HANDLERS['EVENT_CAMERA_SHAKE'](args,b);self.assertEqual(b.args[:3],expected)
        b=B();HANDLERS['EVENT_CAMERA_SHAKE']({'units':'frames','frames':0},b);self.assertFalse(hasattr(b,'args'))
    def test_original_helper_argument_layout(self):
        out=[];b=ScriptBuilder(out,{})
        b.camera_shake_script_value(True,True,30,{'type':'number','value':5})
        text='\n'.join(out);self.assertIn('.CAMERA_SHAKE_X | .CAMERA_SHAKE_Y',text)
        self.assertIn('b_camera_shake_frames, _camera_shake_frames, 0,',text);self.assertEqual(b.stack_ptr,0)
    def test_collision_lifetime_uses_same_numeric_duration(self):
        from types import SimpleNamespace
        from ngpcraft.scene_collisions import collision_capacity
        for args,slots in [({'units':'frames','frames':120},16),({'units':'time','time':2},16),
                           ({'units':'frames','frames':0},4),({},8)]:
            script=SimpleNamespace(path='actor',branches=[(0,'script')],entity={'script':[{'command':'EVENT_CAMERA_SHAKE','args':args}]})
            self.assertEqual(collision_capacity([script]),slots)
    def test_small_map_shake_streams_borders_instead_of_wrapping_loaded_map(self):
        from ngpcraft.background import compile_background
        from ngpcraft.background_stream import stream_source
        asset=compile_background(256,144,(0x123,)*(256*144))
        plain=stream_source(asset);shake=stream_source(asset,camera_shake=True)
        self.assertNotIn('ngc_stream_cell',plain);self.assertIn('ngc_stream_cell',shake)
        self.assertIn('wx < 0 || wy < 0 || wx >= 32 || wy >= 18',shake)
    def test_second_independent_background_has_located_refusal(self):
        from tools.demo_second_background import project_case as second
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);path,_=second(r,text=False,transitions=False)
            scene=next((path/'project/scenes').rglob('scene.gbsres'));data=json.loads(scene.read_text(encoding='utf-8'))
            data['script'].insert(0,{'id':'shake-second','command':'EVENT_CAMERA_SHAKE','args':{'frames':30,'units':'frames','magnitude':{'type':'number','value':5}}})
            scene.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaisesRegex(CompileError,'camera shake.*second background'):export_project(path,r/'out')
    @unittest.skipUnless(os.environ.get('NGC_TEST_TOSHIBA'),'requires Toshiba')
    def test_multi_scene_shake_links_one_shared_clock(self):
        from tools.demo_scene_switch import project_case as multi
        from ngpcraft.toolchain import build_rom
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);path,_=multi(r)
            scene=next((path/'project/scenes').rglob('scene.gbsres'));data=json.loads(scene.read_text(encoding='utf-8'))
            data['script'].insert(0,{'id':'shake-multi','command':'EVENT_CAMERA_SHAKE','args':{'frames':30,'units':'frames','magnitude':{'type':'number','value':5}}})
            scene.write_text(json.dumps(data),encoding='utf-8');export_project(path,r/'out')
            b=build_rom(r/'out',Path(os.environ['NGC_TEST_TOSHIBA']))
            self.assertIn('_ngc_sys_time',b['link_audit']['public_symbols'])
    def test_invalid_and_dynamic_magnitude_are_localized_refusals(self):
        b=ScriptBuilder([],{})
        for n in (0,256,-1,1.5):
            with self.assertRaisesRegex(CompileError,'1..255'):b.camera_shake_script_value(True,False,20,{'type':'number','value':n})
        with self.assertRaisesRegex(NotDelivered,'dynamic magnitudes'):
            b.camera_shake_script_value(True,False,20,{'type':'variable','value':'0'})
    def test_absence_and_native_stack_scratch(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);a=r/'plain';a.mkdir();s=r/'shake';s.mkdir()
            m=export_project(plain(a),a/'export');self.assertNotIn('camera_shake',m['features'])
            self.assertNotIn('ngc_sys_time',(a/'export/src/startup.c').read_text())
            m=export_project(project_case(s),s/'export');self.assertIn('camera_shake',m['features'])
            self.assertLess(m['gbvm']['stack_budget']['words_per_context'],64)
            self.assertIn('ngc_sys_time',(s/'export/src/startup.c').read_text())
    @unittest.skipUnless(os.environ.get('NGC_TEST_CLANG'),'requires Clang and Node')
    def test_generated_rng_all_seeds_and_original_clock_wrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=root/'case';project.mkdir();export_project(project_case(project),root/'export')
            c=(root/'export/src/gbvm.c').read_text()
            # The shared GBDK seed (emit.rand_support): seed, step and rand only.
            rng=re.search(r'static UWORD ngc_rand_seed;.*?static UBYTE ngc_rand\(void\)\n\{.*?\n\}',c,re.S).group()
            self.assertNotIn('ngc_randw',c)          # randw / initrand only when a script uses them
            native=re.search(r'static UBYTE camera_shake_frames\(.*?\n\}',c,re.S).group().replace('camera_shake_frames','generated_shake')
            src=reference_source()+'\n'+rng+'\n'+native+r'''
unsigned validate(void){unsigned i,m,a,t,got,expected;SCRIPT_CTX ctx;UWORD args[3];
for(i=0;i<65536;i++){ngc_rand_seed=i;got=ngc_rand();expected=random_word(i);
if(ngc_rand_seed!=expected||got!=(expected>>8))return 1;}
for(m=1;m<=255;m++)for(a=1;a<=3;a++){reset();ngc_rand_seed=0;args[0]=10;args[1]=a;args[2]=m;
for(t=0;t<=11;t++){expected=step((65530+t)&65535,t==0,10,a,m);
ctx.stack_ptr=&start_word;ctx.waitable=0;sys_time=(65530+t)&65535;
got=generated_shake(&ctx,t==0,args);got=(got<<16)|(unsigned char)scroll_offset_x|((unsigned)(unsigned char)scroll_offset_y<<8);
if(got!=expected||((t<10)!=(ctx.waitable!=0)))return 2;}}
return 0;}
'''
            (root/'proof.c').write_text(src)
            p=subprocess.run([os.environ['NGC_TEST_CLANG'],'--target=wasm32','-nostdlib','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',str(root/'proof.c'),'-o',str(root/'proof.wasm')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            js="const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[1]))).exports;process.stdout.write(String(e.validate()));"
            p=subprocess.run(['node','-e',js,str(root/'proof.wasm')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout,'0')
@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS, Clang and Node')
class ShakeROMTests(unittest.TestCase):
    def test_small_map_has_transparent_borders_in_native_rom(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'small',*ENV,frames=430,small=True)
        self.assertEqual(r['pixel_mismatch_frames'],0,r['pixel_first'])
        self.assertEqual(r['draw_mismatch_ticks'],[]);self.assertEqual(r['dropped_frames'],[])
    def test_original_offsets_draw_border_physics_and_cadence(self):
        for rate in (60,30):
            with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'proof',*ENV,frames=430,rate=rate)
            for key in ('offset_mismatch_ticks','protocol_mismatch_ticks','draw_mismatch_ticks','dropped_frames'):
                self.assertEqual(r[key],[],(key,r[key][:5]))
            self.assertEqual(r['pixel_mismatch_frames'],0,r['pixel_first']);self.assertEqual(r['physics_mismatch_ticks'],0)
            self.assertEqual(r['phases_seen'],list(range(6)))
    def test_witness_missing_draw_offset_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,frames=180,witness=True)
        self.assertGreater(len(r['draw_mismatch_ticks']),0);self.assertGreater(r['pixel_mismatch_frames'],0)
if __name__=='__main__':unittest.main()
