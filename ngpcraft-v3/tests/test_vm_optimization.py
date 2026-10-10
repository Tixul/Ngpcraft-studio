"""Closed RPN specialization and exact scratch/PC/stack equivalence."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path
from ngpcraft.gbvm.emit import RUNTIME,constant_rpn_pairs,constant_rpn_source,rpn_source,runner_source
from ngpcraft.compiler import CompileError

def block(pairs):
    return '        VM_RPN\n'+''.join('            .R_INT16 123\n            .R_REF_SET -1\n' for _ in range(pairs))+'            .R_STOP\n'

class RPNShapeTests(unittest.TestCase):
    def test_all_reachable_blocks_must_have_the_same_small_shape(self):
        self.assertEqual(constant_rpn_pairs([block(2),block(2)]),2)
        self.assertEqual(constant_rpn_pairs([block(1),block(2)]),0)
        self.assertEqual(constant_rpn_pairs([block(5)]),0)
    def test_other_ops_and_live_stack_expressions_keep_original_interpreter(self):
        self.assertEqual(constant_rpn_pairs([block(2).replace('.R_INT16 123','.R_REF VAR_0')]),0)
        self.assertEqual(constant_rpn_pairs([block(2).replace('.R_REF_SET -1','.R_OPERATOR .ADD')]),0)
        self.assertEqual(constant_rpn_pairs([block(2).replace('.R_REF_SET -1','')]),0)
    def test_empty_unclosed_and_labelled_blocks_do_not_specialize(self):
        for text in ('',block(0),block(1).replace('.R_STOP',''),block(1).replace('.R_REF_SET','1$:\n            .R_REF_SET')):
            self.assertEqual(constant_rpn_pairs([text]),0)
    def test_comments_and_multiple_scripts_preserve_shape(self):
        self.assertEqual(constant_rpn_pairs(['; unused VM_RPN\n'+block(3).replace('.R_STOP','.R_STOP ; end'),block(3)]),3)
    def test_composite_memory_assignment_does_not_specialize(self):
        self.assertEqual(constant_rpn_pairs([block(2)+'        VM_SET_CONST_UINT8 _text_draw_speed, 1\n']),0)
        self.assertEqual(constant_rpn_pairs([block(2)],rpn={'VM_OP_INT16','VM_OP_REF_SET','VM_OP_INT8','VM_OP_REF_MEM_SET'}),0)
    def test_nested_composite_with_same_cases_and_different_shape_is_rejected(self):
        model={'composite_macros':{'OUTER':{'lines':['INNER']},'INNER':{'lines':['VM_RPN','.R_INT16 3','.R_REF_SET -1','.R_STOP']}}}
        self.assertEqual(constant_rpn_pairs([block(2)+'        OUTER\n'],model,{'VM_OP_INT16','VM_OP_REF_SET'}),0)
        model['composite_macros']['INNER']['lines']=['VM_IDLE']
        self.assertEqual(constant_rpn_pairs([block(2)+'        OUTER\n'],model,{'VM_OP_INT16','VM_OP_REF_SET'}),2)
    def test_fallback_is_the_original_selected_code(self):
        runtime=json.loads(RUNTIME.read_text());cases={'VM_OP_INT16','VM_OP_REF_SET'}
        expected=''.join(p['text'] for p in runtime['functions']['vm_rpn'] if 'case' not in p or p['case'] in cases)
        self.assertEqual(rpn_source(runtime['functions'],cases),expected)
        self.assertNotEqual(rpn_source(runtime['functions'],cases,2),expected)
    def test_runner_transform_refuses_a_changed_quantum(self):
        runtime=json.loads(RUNTIME.read_text());original=runtime['functions']['script_runner_update']
        result=runner_source(original)
        self.assertIn('counter--',result)
        self.assertIn('goto ngc_runner_instruction',result)
        with self.assertRaises(CompileError):runner_source(original.replace('counter--','--counter'))

@unittest.skipUnless(os.environ.get('NGC_TEST_CLANG'),'requires Clang and Node')
class RPNMemoryTests(unittest.TestCase):
    def test_entire_memory_pc_stack_and_scratch_witness_against_original_c(self):
        runtime=json.loads(RUNTIME.read_text());cases={'VM_OP_INT16','VM_OP_REF_SET'}
        original=rpn_source(runtime['functions'],cases).replace('vm_rpn(','vm_rpn_reference(')
        # The vendored references are signed-word pointers on both branches
        # (tools/extract_gbvm_runtime.py, "C89 conditional references"): no
        # pointer spelling to align any more.
        header='''typedef unsigned char UBYTE; typedef unsigned char UINT8; typedef signed char INT8;
typedef unsigned short UWORD; typedef signed short INT16;
typedef struct { const UBYTE *PC; UWORD *stack_ptr; UBYTE bank; } SCRIPT_CTX;
#define TRUE 1
#define SWITCH_ROM(bank) ((void)(bank))
#define NGC_LE16(p) ((INT16)((UWORD)(p)[0] | ((UWORD)(p)[1] << 8)))
UWORD script_memory[256];
'''
        header+=''.join(f'#define {k} {v}\n' for k,v in runtime['c_defines'].items() if k.startswith('VM_OP_'))
        versions=''.join(constant_rpn_source(n).replace('vm_rpn(',f'vm_rpn_{n}(') for n in range(1,5))
        bad=constant_rpn_source(2).replace('vm_rpn(','vm_rpn_bad(').replace('    INT16 idx;','    INT16 idx; UWORD scratch;').replace('*sp = (UWORD)','scratch = (UWORD)').replace(' = *sp;',' = scratch;')
        harness='''int run(unsigned seed, unsigned pairs, unsigned mode, unsigned witness) {
 UBYTE code[25]; UWORD expected[256]; unsigned i,k,pc,sp; INT16 index; UWORD value; SCRIPT_CTX ctx;
 for(i=0;i<256;i++) script_memory[i]=(UWORD)(i*257u+seed);
 for(k=0;k<pairs;k++) {
  value=(UWORD)(seed*521u+k*16383u);
  index=(INT16)(mode==0 ? -(int)(k+1) : mode==1 ? k : mode==2 ? 160 : 159-k);
  code[k*6]=VM_OP_INT16; code[k*6+1]=(UBYTE)value; code[k*6+2]=(UBYTE)(value>>8);
  code[k*6+3]=VM_OP_REF_SET; code[k*6+4]=(UBYTE)index; code[k*6+5]=(UBYTE)((UWORD)index>>8);
 }
 code[pairs*6]=VM_OP_STOP; ctx.PC=code; ctx.stack_ptr=script_memory+160; ctx.bank=0;
 vm_rpn_reference(&ctx); pc=(unsigned)(ctx.PC-code); sp=(unsigned)(ctx.stack_ptr-script_memory);
 for(i=0;i<256;i++) expected[i]=script_memory[i];
 for(i=0;i<256;i++) script_memory[i]=(UWORD)(i*257u+seed);
 ctx.PC=code; ctx.stack_ptr=script_memory+160;
 if(witness) vm_rpn_bad(&ctx);
 else if(pairs==1) vm_rpn_1(&ctx); else if(pairs==2) vm_rpn_2(&ctx);
 else if(pairs==3) vm_rpn_3(&ctx); else vm_rpn_4(&ctx);
 if((unsigned)(ctx.PC-code)!=pc || (unsigned)(ctx.stack_ptr-script_memory)!=sp) return 1000;
 for(i=0;i<256;i++) if(expected[i]!=script_memory[i]) return (int)i+1;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'proof.c').write_text(header+original+versions+bad+harness)
            proc=subprocess.run([os.environ['NGC_TEST_CLANG'],'--target=wasm32','-O2','-nostdlib',str(root/'proof.c'),
                '-Wl,--no-entry','-Wl,--export=run','-o',str(root/'proof.wasm')],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            js='''const fs=require('fs');(async()=>{const {instance}=await WebAssembly.instantiate(fs.readFileSync(process.argv[2]));
const e=instance.exports;let bad=[],witness=[];for(let n=1;n<=4;n++)for(let m=0;m<4;m++)for(const s of [0,1,63,127]){
let v=e.run(s,n,m,0);if(v)bad.push([s,n,m,v]);}for(const s of [1,63,127])witness.push(e.run(s,2,0,1));
console.log(JSON.stringify({bad,witness}));})();'''
            (root/'proof.js').write_text(js)
            proc=subprocess.run(['node',str(root/'proof.js'),str(root/'proof.wasm')],check=True,capture_output=True,text=True)
            result=json.loads(proc.stdout);self.assertEqual(result['bad'],[])
            self.assertTrue(all(result['witness']),result)
