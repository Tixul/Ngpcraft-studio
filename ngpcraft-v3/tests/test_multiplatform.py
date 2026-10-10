"""Strict scene C linking and native Platformer scene reinitialization."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path
from ngpcraft.compiler import CompileError
from ngpcraft.scene_link import link_file
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.lab_multiplatform import project_case,run
ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]

class MultiPlatformLinkTests(unittest.TestCase):
    def test_scalar_alias_enum_and_pointer_are_shared_with_exact_sizes(self):
        source='''#define WORD short
typedef enum
{
    FIRST = 0,
    SECOND
} State;
static State state;
WORD velocity; // same storage as the engine memory's short
static const unsigned char *animations;
void ngc_go(void) { velocity = SECOND; }
'''
        c,report=link_file('src/x.c',[source,source])
        self.assertEqual(c.count('} State;'),1)
        self.assertEqual(c.count('short velocity;'),1)
        self.assertEqual(c.count('static const unsigned char * animations;'),1)
        self.assertEqual(report['ram_bytes'],8)
        self.assertIn('#define s0_WORD short',c);self.assertIn('#define s1_WORD short',c)
    def test_unknown_directives_and_conditional_storage_are_refused(self):
        for source in ('#include "arbitrary.h"\n','#if 1\nstatic short n;\n#endif\n',
                       '#ifdef FLAG\n#define WORD short\n#endif\n','#else\n',
                       '#if 1\n','#if 1\n#else\n#elif 0\n#endif\n'):
            with self.subTest(source=source),self.assertRaises(CompileError):link_file('src/x.c',[source,source])
    @unittest.skipUnless(ENV[3],'requires Clang and Node')
    def test_scene_macros_and_multiline_arguments_execute_independently(self):
        source='''#define WORD short
#define WIDTH VALUE
#define APPLY(x) \\
    ((x) + WIDTH)
static WORD helper(WORD value) { return APPLY(value); }
short ngc_value(short value)
{
#ifdef WIDTH
    return helper(value);
#else
    return -1;
#endif
}
'''
        c,_=link_file('src/x.c',[source.replace('VALUE','2'),source.replace('VALUE','5')])
        c='unsigned char ngc_scene;\n'+c+'\nint validate(void){ngc_scene=0;if(ngc_value(3)!=5)return 1;ngc_scene=1;if(ngc_value(3)!=8)return 2;return 0;}\n'
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'proof.c').write_text(c,encoding='utf-8')
            p=subprocess.run([ENV[3],'--target=wasm32','-nostdlib','-O2','-Wl,--no-entry','-Wl,--export-all',str(r/'proof.c'),'-o',str(r/'proof.wasm')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            js="const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[1]))).exports;process.stdout.write(String(e.validate()));"
            p=subprocess.run(['node','-e',js,str(r/'proof.wasm')],capture_output=True,text=True)
            self.assertEqual((p.returncode,p.stdout),(0,'0'),p.stderr)
    @unittest.skipUnless(ENV[0],'requires Toshiba')
    def test_platform_topdown_mix_builds_and_callbacks_use_computed_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);p=project_case(r,mixed=True);m=export_project(p,r/'export');build_rom(r/'export',ENV[0])
            self.assertIn('platform',m['features']);self.assertIn('topdown',m['features'])
            c=(r/'export/src/gbvm.c').read_text(encoding='utf-8')
            self.assertIn(f"#define VM_MAX_CONTEXTS {m['gbvm']['stack_budget']['contexts']}",c)
            self.assertEqual(m['scene_engine']['contexts'],45)

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS and Clang')
class MultiPlatformROMTests(unittest.TestCase):
    def test_state_reset_callbacks_pixels_and_loading_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'proof',*ENV,frames=900)
        self.assertEqual(r['scenes_seen'],[0,1]);self.assertEqual(r['transitions'],[205,405,605,805])
        for key in ('physics_mismatch_ticks','callback_mismatch_segments','pixel_mismatch_frames'):self.assertEqual(r[key],0,r)
        self.assertEqual(r['dropped_gameplay_frames'],[])
        self.assertEqual([v['frames'] for v in r['transition_frames']],[3,3,3,3])
        self.assertGreater(r['state_events'],50);self.assertGreater(r['pixel_checked_frames'],800)
    def test_missing_platform_init_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,frames=420,witness=True)
        self.assertGreater(r['physics_mismatch_ticks'],0)

if __name__=='__main__':unittest.main()
