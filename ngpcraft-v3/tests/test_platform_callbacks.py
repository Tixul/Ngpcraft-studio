"""Platform state script contracts, original native guards and real ROMs."""
import json,os,re,subprocess,tempfile,unittest
from pathlib import Path
from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.platform_callbacks import CALLBACKS,CONSTANTS,label,support,bodies
from tools.lab_platform_callbacks import project_case,run
from tools.platform_oracle import project_case as plain
from tools.reference import REFERENCE
ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]

class CallbackCompilerTests(unittest.TestCase):
    def test_all_labels_follow_original_map_and_default(self):
        src=(REFERENCE/'src/lib/events/eventPlatformerCallbackScriptSet.js').read_text(encoding='utf-8')
        values=src.split('const valuesMap = {',1)[1].split('};',1)[0]
        pairs=re.findall(r'(\w+): "(PLATFORM_\w+)"',values)
        self.assertEqual(len(pairs),21)
        for event,original in pairs:self.assertEqual(label(event),original)
        self.assertEqual(label(None),'PLATFORM_FALL_INIT');self.assertEqual(label('unknown'),'PLATFORM_FALL_INIT')
        b=ScriptBuilder([],{});b.platform_callback(None,remove=True);self.assertEqual(b.stack_ptr,0)
    def test_original_argument_layout_sparse_stack_and_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);p=r/'plain';p.mkdir();s=r/'scripts';s.mkdir()
            m=export_project(plain(p),p/'out');self.assertNotIn('platform_callbacks',m['features'])
            self.assertNotIn('plat_events',(p/'out/src/scene_actors.c').read_text())
            m=export_project(project_case(s),s/'out');self.assertIn('platform_callbacks',m['features'])
            text='\n'.join(v['source'] for v in m['scripts'].values())
            self.assertIn('VM_CALL_NATIVE',text);self.assertIn('b_plat_callback_attach, _plat_callback_attach',text)
            c=(s/'out/src/gbvm.c').read_text();self.assertIn('ngc_plat_events[21]',c)
            self.assertLess(m['gbvm']['stack_budget']['words_per_context'],64)
            self.assertIn(f"#define VM_MAX_CONTEXTS {m['gbvm']['stack_budget']['contexts']}",c)
    def test_nonplatform_callback_has_explicit_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);path=project_case(r);scene=next((path/'project/scenes').rglob('scene.gbsres'))
            d=json.loads(scene.read_text(encoding='utf-8'));d['type']='TOPDOWN';scene.write_text(json.dumps(d),encoding='utf-8')
            with self.assertRaisesRegex(CompileError,'require a PLATFORM scene'):export_project(path,r/'out')
    def test_multi_scene_callback_exports_with_shared_storage(self):
        from tools.demo_scene_switch import project_case as multi
        from ngpcraft.toolchain import build_rom
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);path,_=multi(r)
            for scene in (path/'project/scenes').rglob('scene.gbsres'):
                d=json.loads(scene.read_text(encoding='utf-8'));d['type']='PLATFORM'
                d['script'].insert(0,{'id':'remove','command':'EVENT_REMOVE_PLATFORMER_CALLBACK_SCRIPT','args':{'event':'jumpStart'}})
                scene.write_text(json.dumps(d),encoding='utf-8')
            m=export_project(path,r/'out')
            self.assertIn('platform_callbacks',m['features'])
            self.assertEqual(len(m['scene_engine']['scenes']),2)

@unittest.skipUnless(ENV[3],'requires Clang and Node')
class CallbackOriginalTests(unittest.TestCase):
    def test_original_attach_detach_reset_and_all_handle_values(self):
        raw=(REFERENCE/'appData/engine/gbvm/src/states/platform.c').read_text(encoding='utf-8')
        original=[]
        for fn in ('plat_callback_attach','plat_callback_detach','plat_callback_reset','plat_callback_execute'):
            code=re.search(r'^(?:static |inline )?void '+fn+r'\([^\n]*\)(?: OLDCALL BANKED)?\s*\{.*?^\}',raw,re.M|re.S).group()
            code=code.replace('OLDCALL BANKED','').replace('inline ','').replace(fn,'old_'+fn).replace('script_execute(','old_script_execute(')
            original.append(code)
        header=r'''
typedef unsigned char UBYTE;typedef unsigned short UWORD;typedef short INT16;
typedef struct { UWORD *stack_ptr; } SCRIPT_CTX;
typedef struct { UWORD handle;UBYTE script_bank;const UBYTE *script_addr; } script_event_t;
#define NULL 0
#define SCRIPT_TERMINATED 32768
#define CALLBACK_SIZE 21
#define FN_ARG0 (-1)
#define FN_ARG1 (-2)
#define FN_ARG2 (-3)
#define VM_REF_TO_PTR(idx) ((void *)(THIS->stack_ptr+(idx)))
static script_event_t plat_events[21];static UWORD script_memory[1];
static UBYTE bank_data[65536];static const UBYTE *banks[256];static const UBYTE **ngc_banks=banks;
static unsigned old_calls,new_calls,old_tag,new_tag,old_bank,new_bank;
void *memset(void *dest,int value,unsigned size){unsigned char *p=dest;while(size--)*p++=value;return dest;}
void *old_script_execute(UBYTE bank,const UBYTE *ptr,UWORD *handle,UBYTE nargs,...){old_calls++;old_tag=(unsigned)(unsigned long)ptr;old_bank=bank;*handle=0x1234;return 0;}
void *script_execute(UBYTE bank,const UBYTE *ptr,UWORD *handle,UBYTE nargs,...){new_calls++;new_tag=ptr-bank_data;new_bank=bank;*handle=0x1234;return 0;}
'''
        tail=r'''
unsigned validate(void){unsigned i,j,slot;UWORD args[4];SCRIPT_CTX ctx;
for(i=0;i<256;i++)banks[i]=bank_data;
ctx.stack_ptr=args+3;args[3]=0;
for(i=0;i<65536;i++)for(j=0;j<3;j++){
slot=j==0?0:(j==1?4:17);old_plat_callback_reset();ngc_vm_platform_reset();
plat_events[slot].handle=ngc_plat_events[j].handle=i;
args[0]=slot;args[1]=i;args[2]=(i+1)&65535;
old_plat_callback_attach(&ctx);plat_callback_attach(&ctx);
if((unsigned)(unsigned long)plat_events[slot].script_addr!=ngc_plat_events[j].script_addr || plat_events[slot].script_bank!=ngc_plat_events[j].script_bank || ngc_plat_events[j].handle!=i)return 1;
old_calls=new_calls=0;old_plat_callback_execute(slot);ngc_vm_platform_event(slot);
if(old_calls!=new_calls || plat_events[slot].handle!=ngc_plat_events[j].handle)return 2;
if(old_calls && (old_tag!=new_tag || old_bank!=new_bank))return 3;
args[2]=slot;old_plat_callback_detach(&ctx);plat_callback_detach(&ctx);
if(plat_events[slot].script_addr || ngc_plat_events[j].script_addr || ngc_plat_events[j].script_bank || plat_events[slot].handle!=ngc_plat_events[j].handle)return 4;
old_calls=new_calls=0;old_plat_callback_execute(slot);ngc_vm_platform_event(slot);if(old_calls||new_calls)return 5;
}
ngc_vm_platform_event(20);ngc_vm_platform_reset();
for(j=0;j<3;j++)if(ngc_plat_events[j].handle||ngc_plat_events[j].script_addr||ngc_plat_events[j].script_bank)return 6;
return 0;}
'''
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'proof.c').write_text(header+'\n'.join(original)+support([0,4,17],True,True)+bodies([0,4,17],True,True)+tail)
            p=subprocess.run([ENV[3],'--target=wasm32','-nostdlib','-fno-builtin','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',str(r/'proof.c'),'-o',str(r/'proof.wasm')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            js="const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[1]))).exports;process.stdout.write(String(e.validate()));"
            p=subprocess.run(['node','-e',js,str(r/'proof.wasm')],capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout,'0')

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS and Clang')
class CallbackROMTests(unittest.TestCase):
    def test_native_detach_removes_future_jump_callbacks(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'removed',*ENV,frames=720,detach_jump=True)
        self.assertEqual(r['callback_mismatch_ticks'],0,r['callback_first']);self.assertEqual(r['physics_mismatch_ticks'],0)
        self.assertEqual(r['pixel_mismatch_frames'],0,r['pixel_first']);self.assertNotIn(4,r['slots_fired'])
        self.assertEqual(r['dropped_frames'],[])
    def test_state_transitions_script_variables_pose_pixels_and_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'proof',*ENV,frames=720)
        for key in ('physics_mismatch_ticks','callback_mismatch_ticks','pixel_mismatch_frames'):self.assertEqual(r[key],0,(key,r))
        self.assertEqual(r['dropped_frames'],[]);self.assertGreater(r['state_events'],30)
        self.assertEqual(r['slots_fired'],[0,1,2,3,4,5,17,18])
    def test_running_callback_does_not_restart_until_terminated(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'waiting',*ENV,frames=720,callback_wait=90)
        self.assertEqual(r['callback_mismatch_ticks'],0,r['callback_first']);self.assertEqual(r['physics_mismatch_ticks'],0)
        self.assertEqual(r['pixel_mismatch_frames'],0,r['pixel_first']);self.assertEqual(r['dropped_frames'],[])
        self.assertLess(r['state_events'],62)
    def test_missing_jump_hook_witness_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,frames=220,witness=True)
        self.assertGreater(r['callback_mismatch_ticks'],0)
        self.assertGreater(r['pixel_mismatch_frames'],0)
if __name__=='__main__':unittest.main()
