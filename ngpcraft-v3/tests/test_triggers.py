"""Trigger project contracts, GBVM C equivalence, and ROM mutation witnesses."""
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project,plan_project
from ngpcraft.project import load_project
from ngpcraft.scene_triggers import build_triggers,trigger_source
from tools.demo_triggers import project_case,build_case
from tools.demo_gbs_scene import write_json
from tools.demo_actor_moves import ev,num
from tools.gbvm_triggers import Triggers
from tools.reference import REFERENCE

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]

class TriggerProjectTests(unittest.TestCase):
    def test_parameter_branches_local_context_and_runtime_selection(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r)
            path=next(p.rglob('proof_0.gbsres'));t=json.loads(path.read_text())
            t['script'].insert(0,ev('local','EVENT_SET_VALUE',variable='L0',value=num(7)));write_json(path,t)
            m=export_project(p,r/'out')
            self.assertIn('triggers',m['features']);self.assertNotIn('actor_interactions',m['features'])
            self.assertNotIn('actor_updates',m['features']);self.assertEqual(m['scene_engine']['trigger_contexts'],5)
            asm=m['scripts']['trigger_proof_0_interact']['source']
            self.assertIn('VM_GET_TLOCAL',asm);self.assertIn('VAR_S0T0_LOCAL_0',m['variables'])
            self.assertIn('VM_LOCK',asm)
            self.assertEqual(len(plan_project(p)['triggers']),2)
            c=(r/'out/src/gbvm.c').read_text();self.assertIn('*(ctx->stack_ptr++) = argument',c)
            self.assertNotIn('ngc_vm_interact(',c)

    def test_unused_and_end_only_triggers_do_not_emit_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r)
            for path in (p/'project/scenes').rglob('triggers/*.gbsres'):
                t=json.loads(path.read_text());t['script']=[ev('end','EVENT_END')];t['leaveScript']=[];write_json(path,t)
            m=export_project(p,r/'out');self.assertNotIn('triggers',m['features'])
            self.assertNotIn('trigger_update',(r/'out/src/scene_actors.c').read_text())
            self.assertNotIn('ngc_vm_trigger',(r/'out/src/gbvm.c').read_text())

    def test_single_leave_script_collapses_without_parameter_opcode(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r)
            for path in p.rglob('proof_*.gbsres'):
                t=json.loads(path.read_text());t['script']=[];write_json(path,t)
            m=export_project(p,r/'out')
            self.assertNotIn('VM_GET_TLOCAL',m['gbvm']['opcodes'])
            self.assertIn('VM_LOCK',m['scripts']['trigger_proof_0_interact']['source'])

    def test_refusals_are_located_and_no_export_is_written(self):
        for field,value,pattern in [('script',[ev('v','EVENT_SET_VALUE',variable='3',value={'type':'add','valueA':{'type':'number','value':1},'valueB':{'type':'property','target':'player','property':'frame'}})],r'proof_0.*:script\[0\]'),('width',0,'bounds'),('x',256,'bounds'),('prefabId','missing','prefabId')]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
                r=Path(d);p=project_case(r);path=next(p.rglob('proof_0.gbsres'));t=json.loads(path.read_text());t[field]=value;write_json(path,t)
                with self.assertRaisesRegex(CompileError,pattern):export_project(p,r/'out')
                self.assertFalse((r/'out').exists())

    def test_prefab_scripts_and_argument_override(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r);path=next(p.rglob('proof_0.gbsres'));t=json.loads(path.read_text())
            # GB Studio trigger prefab fields are a subset of the instance.
            prefab={k:t[k] for k in ('name','script','leaveScript')}
            prefab.update({'_resourceType':'triggerPrefab','id':'proof-prefab'})
            prefab['script']=[ev('prefab-local','EVENT_SET_VALUE',variable='L0',value=num(1))]
            (p/'project/prefabs/triggers').mkdir(parents=True,exist_ok=True)
            write_json(p/'project/prefabs/triggers/proof.gbsres',prefab)
            t['prefabId']='proof-prefab';t['prefabScriptOverrides']={'prefab-local':{'id':'prefab-local','args':{'value':num(9)}}};write_json(path,t)
            tr=next(x for x in build_triggers(load_project(p)) if x.trigger['id']==t['id'])
            self.assertEqual(tr.trigger['script'][0]['args']['value'],num(9));self.assertIn('prefab of',tr.path)
            export_project(p,r/'out')

    def test_many_regions_share_the_bounded_locked_context_pool(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r);path=next(p.rglob('proof_0.gbsres'));t=json.loads(path.read_text())
            for i in range(12):
                write_json(path.parent/f'extra_{i}.gbsres',{**t,'id':f'extra{i}','symbol':f'extra{i}'})
            m=export_project(p,r/'out')
            self.assertEqual(m['scene_engine']['triggers'],14)
            self.assertEqual(m['scene_engine']['trigger_contexts'],5)

    def test_maximum_trigger_count_is_not_silently_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r);path=next(p.rglob('proof_0.gbsres'));t=json.loads(path.read_text())
            for i in range(31):write_json(path.parent/f'excess_{i}.gbsres',{**t,'id':f'excess{i}','symbol':f'excess{i}'})
            with self.assertRaisesRegex(CompileError,'limit 30'):plan_project(p)

@unittest.skipUnless(os.environ.get('NGC_TEST_CLANG'),'requires Clang and Node')
class TriggerReferenceTests(unittest.TestCase):
    def test_generated_and_oracle_against_original_C(self):
        regions=[(3,4,2,2,True,True),(4,4,4,2,True,True),(9,4,1,1,False,True),(11,4,1,1,True,False),(255,255,1,1,True,True)]
        data=[({'x':x,'y':y,'width':w,'height':h,'script':[1] if enter else [],'leaveScript':[1] if leave else []},i+1) for i,(x,y,w,h,enter,leave) in enumerate(regions)]
        core=REFERENCE/'appData/engine/gbvm'
        original=re.sub(r'^#(?:include|pragma).*$', '',(core/'src/core/trigger.c').read_text(),flags=re.M)
        header=(core/'include/trigger.h').read_text();intersection=header[header.index('inline UBYTE trigger_at_intersection'):header.rindex('#endif')].replace('inline UBYTE','UBYTE')
        pre='''#include <stdarg.h>
typedef unsigned char UBYTE; typedef unsigned short UWORD;
typedef struct {short left,right,top,bottom;} rect16_t;
typedef struct {UWORD x,y;} upoint16_t;
typedef struct {UBYTE left,right,top,bottom; struct {UBYTE bank; UBYTE *ptr;} script; UBYTE script_flags;} trigger_t;
#define BANKED
#define MAX_TRIGGERS 31
#define NO_TRIGGER_COLLISON 255
#define TRIGGER_HAS_ENTER_SCRIPT 1
#define TRIGGER_HAS_LEAVE_SCRIPT 2
#define FALSE 0
#define TRUE 1
#define SUBPX_TO_TILE(x) ((UWORD)(x)>>8)
extern trigger_t triggers[]; extern UBYTE triggers_len;
UBYTE trigger_at_tile(UBYTE,UBYTE);
static unsigned short act_x[1],act_y[1];static short act_bounds[4];
static int calls[8],count;
void ngc_vm_trigger(unsigned short addr,unsigned short arg){calls[count++]=addr;calls[count++]=arg;}
void script_execute(UBYTE bank,UBYTE *ptr,int handle,int nargs,...){va_list a;va_start(a,nargs);ngc_vm_trigger((unsigned short)(unsigned long)ptr,va_arg(a,int));va_end(a);}
'''
        source=pre+intersection+original+'\n'+'\n'.join(trigger_source(data))
        source+='\nvoid reset(void){trigger_reset();trigger_last=255;triggers_len=5;\n'
        for i,(x,y,w,h,enter,leave) in enumerate(regions):
            source+=f'triggers[{i}]=(trigger_t){{{x},{x+w-1},{y},{y+h-1},{{1,(UBYTE*){i+1}}},{int(enter)+2*int(leave)}}};\n'
        source+='''}
int step(int generated,int x,int y,int l,int r,int t,int b){rect16_t box={l,r,t,b};upoint16_t pos={x,y};count=0;act_x[0]=x;act_y[0]=y;act_bounds[0]=l;act_bounds[1]=r;act_bounds[2]=t;act_bounds[3]=b;return generated?trigger_update():trigger_activate_at_intersection(&box,&pos,0);}
int get(int i){return i==0?count:calls[i-1];}
'''
        rng=random.Random(432)
        steps=[[x*256,4*256,0,511,0,255] for x in [0,2,2,3,4,6,8,9,10,11,12,255,0]*3]
        steps += [[rng.randrange(0,14)*256,rng.choice([3,4,5,6])*256,rng.choice([-32,0]),rng.choice([255,511]),0,255] for _ in range(1000)]
        steps += [[65535,65535,-32,511,-32,255],[0,0,-32,511,-32,255]]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'test.c').write_text(source);write_json(root/'steps.json',steps)
            subprocess.run([os.environ['NGC_TEST_CLANG'],'--target=wasm32','-nostdlib','-O2','-Wl,--no-entry','-Wl,--export-all',str(root/'test.c'),'-o',str(root/'test.wasm')],capture_output=True,check=True)
            (root/'run.js').write_text("const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;const steps=JSON.parse(fs.readFileSync(process.argv[3]));let all=[];for(let g=0;g<2;g++){e.reset();let rows=[];for(const s of steps){const ret=e.step(g,...s),n=e.get(0);rows.push([ret,...Array.from({length:n},(_,i)=>e.get(i+1))]);}all.push(rows);}process.stdout.write(JSON.stringify(all));")
            out=subprocess.run(['node',str(root/'run.js'),str(root/'test.wasm'),str(root/'steps.json')],capture_output=True,text=True,check=True)
            original,generated=json.loads(out.stdout);self.assertEqual(original,generated)
            model=Triggers(regions)
            for tick,(step,row) in enumerate(zip(steps,original)):
                n=len(model.calls);x,y,*bounds=step;ret=model.update(SimpleNamespace(x=x,y=y,bounds=bounds),tick)
                calls=[v for _,i,arg in model.calls[n:] for v in (i+1,arg)]
                self.assertEqual([int(ret),*calls],row,(tick,step))

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator and BIOS')
class TriggerRomTests(unittest.TestCase):
    def run_case(self,mutate=None):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);build_case(root,ENV[0],mutate=mutate)
            proc=subprocess.run([sys.executable,'-B','-m','tools.probe_stream',str(root),'--emulator',ENV[1],'--bios',ENV[2],'--out',str(root/'probe')],capture_output=True,text=True,timeout=180)
            result=json.loads((root/'probe/probe.json').read_text());source=json.loads((root/'source.json').read_text())
            if mutate:
                self.assertNotEqual(proc.returncode,0);self.assertTrue(result['frames_with_mismatch'])
            else:
                self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr);self.assertEqual(result['frames_with_mismatch'],[]);self.assertEqual(result['dropped_frames'],[])
            return source
    def test_enter_leave_return_and_two_scripts_on_same_boundary(self):
        s=self.run_case();self.assertEqual(s['trigger_calls'][:4],[[47,0,1],[84,1,1],[84,0,2],[141,1,2]])
    def test_mutation_missing_exit(self):
        self.run_case(('src/scene_actors.c','switch (trigger_last) {','switch (255u) {'))
    def test_mutation_repeated_entry(self):
        self.run_case(('src/scene_actors.c','if (hit == trigger_last) return 0u;','/* broken repeat guard */'))
    def test_mutation_wrong_thread_parameter(self):
        self.run_case(('src/gbvm.c','*(ctx->stack_ptr++) = argument','*(ctx->stack_ptr++) = 1u'))

if __name__=='__main__':unittest.main()
