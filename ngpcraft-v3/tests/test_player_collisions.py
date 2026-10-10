"""Player collision callback contracts, original-C equivalence and native ROMs."""
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
from ngpcraft.project import ProjectError
from ngpcraft.scene_collisions import collision_source
from tools.demo_player_collisions import project_case,build_case,ANGRY
from tools.demo_topdown import project_case as plain_project
from tools.demo_gbs_scene import write_json
from tools.demo_actor_moves import ev,num
from tools.gbvm_player_collisions import PlayerCollisions
from tools.reference import REFERENCE

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]

class CollisionProjectTests(unittest.TestCase):
    def test_callbacks_are_unlocked_parameterized_and_selected(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r);m=export_project(p,r/'out')
            self.assertIn('player_collisions',m['features']);self.assertNotIn('actor_interactions',m['features'])
            self.assertEqual(m['scene_engine']['collision_contexts'],11)
            hit=[v['source'] for k,v in m['scripts'].items() if k.endswith('_p_hit1') or k.endswith('_interact')]
            self.assertEqual(len(hit),2)
            for script in hit:self.assertNotIn('VM_LOCK',script);self.assertIn('VM_GET_TLOCAL',script)
            game=(r/'out/src/game.c').read_text();self.assertLess(game.index('    ngc_actors_render();'),game.index('    ngc_collision_update();'))
            self.assertLess(game.index('    ngc_collision_update();'),game.index('    ++ngc_game_time;'))

    def test_three_groups_and_actor_self_context(self):
        for group,mask in [('1',2),('2',4),('3',8)]:
            with self.subTest(group=group),tempfile.TemporaryDirectory() as d:
                r=Path(d);p=project_case(r,group=group)
                path=next(path for path in (p/'project/scenes').rglob('actors/*.gbsres') if json.loads(path.read_text())['id']==ANGRY)
                a=json.loads(path.read_text());a['script'].insert(0,ev('local','EVENT_SET_VALUE',variable='L0',value=num(3)));write_json(path,a)
                m=export_project(p,r/'out');script=next(v['source'] for k,v in m['scripts'].items() if k.endswith('_p_hit1'))
                self.assertIn(f'VALUE, {mask}, 1$',script);self.assertIn('VAR_S0A4_LOCAL_0',m['variables'])
                self.assertEqual(len(plan_project(p)['collision_scripts']),2)

    def test_absent_when_no_callbacks_requested(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=plain_project(r);m=export_project(p,r/'out')
            self.assertNotIn('player_collisions',m['features'])
            self.assertNotIn('collision_iframes',(r/'out/src/scene_actors.c').read_text())
            self.assertNotIn('ngc_vm_hit',(r/'out/src/gbvm.c').read_text())

    def test_longer_wait_reserves_overlapping_generations(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r,pause=120,repeat=True);m=export_project(p,r/'out')
            self.assertEqual(m['scene_engine']['collision_contexts'],17)

    def test_unbounded_callbacks_use_the_largest_pool_and_unknown_group_is_refused(self):
        for kind in ('loop','dynamic_wait'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                r=Path(d);p=project_case(r);path=next(path for path in (p/'project/scenes').rglob('actors/*.gbsres') if json.loads(path.read_text())['id']==ANGRY);a=json.loads(path.read_text())
                if kind=='loop':a['script']=[{'id':'loop','command':'EVENT_LOOP','children':{'true':[]}}];pattern=r'actors/.*:script\[0\]: EVENT_LOOP: loop: .*unbounded'
                else:a['script']=[ev('wait','EVENT_WAIT',units='frames',frames={'type':'variable','value':'0'})];pattern=r'actors/.*:script\[0\]: EVENT_WAIT: wait duration given by a variable.*unbounded'
                write_json(path,a)
                m=export_project(p,r/'out')
                self.assertTrue(any(re.search(pattern,w) for w in m['warnings']),m['warnings'])
                budget=m['scene_engine']['context_budget']
                self.assertEqual(budget['contexts'],min(budget['limits'].values()))
                self.assertGreaterEqual(budget['contexts'],16)
                self.assertIn('NULL',budget['on_exhaustion'])
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r);path=next(path for path in (p/'project/scenes').rglob('actors/*.gbsres') if json.loads(path.read_text())['id']==ANGRY);a=json.loads(path.read_text())
            a['collisionGroup']='4';write_json(path,a)
            with self.assertRaisesRegex((CompileError,ProjectError),'collisionGroup'):export_project(p,r/'out')
            self.assertFalse((r/'out').exists())

    def test_hostile_script_blocks_A_lookup_before_friendly_behind_it(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=project_case(r)
            path=next(path for path in (p/'project/scenes').rglob('actors/*.gbsres') if json.loads(path.read_text())['x']==18)
            a=json.loads(path.read_text());a['script']=[ev('hello','EVENT_WAIT',units='frames',frames=num(5))];write_json(path,a)
            m=export_project(p,r/'out');self.assertIn('actor_interactions',m['features'])
            c=(r/'out/src/scene_actors.c').read_text()
            self.assertIn('hit != 255u && !(act_collision_group[hit] & 0x0Fu)',c)
            # Both hostile and friendly scripts are present in the front lookup table.
            start=c.index('act_interact_addr');table=c[start:c.index(';',start)]
            self.assertGreaterEqual(sum(int(v.strip().rstrip('u'),0)>0 for v in table.split('{')[1].split('}')[0].replace('\n','').split(',') if v.strip()),2)

@unittest.skipUnless(os.environ.get('NGC_TEST_CLANG'),'requires Clang and Node')
class CollisionReferenceTests(unittest.TestCase):
    def test_generated_overlap_iframes_and_call_order_against_original_C(self):
        raw=(REFERENCE/'appData/engine/gbvm/src/core/actor.c').read_text()
        start=raw.index('actor_t *actor_overlapping_player_from(');overlap=raw[start:raw.index('actor_t *actor_overlapping_bb(',start)]
        start=raw.index('void actors_handle_player_collision(');handler=raw[start:raw.index('\n}',start)+2]
        config={'groups':[1,0,2,4,8],'actors':{2:102,3:103,4:104},'player':100}
        pre='''#include <stdarg.h>
#define BANKED
#define NULL ((void*)0)
#define COLLISION_GROUP_MASK 15
#define PLAYER_HURT_IFRAMES 20
#define ACTOR_FLAG_COLLISION 8
#define CHK_FLAG(v,m) ((v)&(m))
typedef unsigned char UBYTE;typedef unsigned short UWORD;
typedef struct actor_t {struct actor_t *prev;struct {UWORD x,y;} pos;struct {short left,right,top,bottom;} bounds;UBYTE flags,collision_group;struct {UBYTE bank;UBYTE *ptr;} script;} actor_t;
static actor_t actors[5];
#define PLAYER actors[0]
static actor_t *player_collision_actor;static UBYTE player_iframes;
static UWORD act_x[5],act_y[5];static short act_bounds[20];static UBYTE act_flags[5],act_prev[5];
static int calls[8],count;
void ngc_vm_hit(UWORD addr,UWORD arg){calls[count++]=addr;calls[count++]=arg;}
void script_execute(UBYTE bank,UBYTE *ptr,int handle,int nargs,...){va_list a;va_start(a,nargs);ngc_vm_hit((UWORD)(unsigned long)ptr,va_arg(a,int));va_end(a);}
'''
        source=pre+overlap+handler+'\n'+'\n'.join(collision_source(config))+'''
void reset(void){player_iframes=collision_iframes=0;player_collision_actor=NULL;collision_pending=255;}
int step(int generated,int x,int y,int mask,int pending){int i;actor_t *a;count=0;
for(i=0;i<5;i++){act_x[i]=i?((3+i)*256):x;act_y[i]=i?1024:y;act_flags[i]=(mask&(1<<i))?8:0;act_prev[i]=i?(i==1?255:i-1):4;
act_bounds[i*4]=0;act_bounds[i*4+1]=511;act_bounds[i*4+2]=0;act_bounds[i*4+3]=255;
actors[i].prev=act_prev[i]==255?NULL:&actors[act_prev[i]];actors[i].pos.x=act_x[i];actors[i].pos.y=act_y[i];actors[i].bounds.left=0;actors[i].bounds.right=511;actors[i].bounds.top=0;actors[i].bounds.bottom=255;actors[i].flags=act_flags[i];actors[i].collision_group=act_collision_group[i];actors[i].script.bank=(i!=1);actors[i].script.ptr=(UBYTE*)(unsigned long)(100+i);}
if(generated){i=player_overlap();collision_pending=pending;ngc_collision_update();return i;}
a=actor_overlapping_player_from(NULL);player_collision_actor=pending==255?NULL:&actors[pending];actors_handle_player_collision();return a?(int)(a-actors):255;}
int get(int generated,int index){if(index==0)return generated?collision_iframes:player_iframes;if(index==1)return count;return calls[index-2];}
'''
        rng=random.Random(432);steps=[[1280,1024,31,2]]*70+[[1280,1024,31,255]]*25
        steps += [[rng.choice([0,1024,1280,1536,1792,65535]),rng.choice([1024,1280,65535]),rng.randrange(32),rng.choice([1,2,3,4,255])] for _ in range(1000)]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(source);write_json(p/'steps.json',steps)
            proc=subprocess.run([os.environ['NGC_TEST_CLANG'],'--target=wasm32','-nostdlib','-O2','-Wl,--no-entry','-Wl,--export-all',str(p/'test.c'),'-o',str(p/'test.wasm')],capture_output=True,text=True);self.assertEqual(proc.returncode,0,proc.stderr)
            (p/'run.js').write_text("const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;const steps=JSON.parse(fs.readFileSync(process.argv[3]));let all=[];for(let g=0;g<2;g++){e.reset();let rows=[];for(const s of steps){let hit=e.step(g,...s),n=e.get(g,1);rows.push([hit,e.get(g,0),...Array.from({length:n},(_,i)=>e.get(g,i+2))]);}all.push(rows);}process.stdout.write(JSON.stringify(all));")
            out=subprocess.run(['node',str(p/'run.js'),str(p/'test.wasm'),str(p/'steps.json')],capture_output=True,text=True,check=True)
            reference,generated=json.loads(out.stdout);self.assertEqual(reference,generated)
            model=PlayerCollisions(config['groups'],player_factory=lambda arg:[],actor_factories={i:lambda:[] for i in (2,3,4)})
            for tick,(row,step) in enumerate(zip(reference,steps)):
                x,y,mask,pending=step;actors=[SimpleNamespace(index=i,x=(3+i)*256 if i else x,y=1024 if i else y,flags=8 if mask&(1<<i) else 0,bounds=(0,511,0,255),prev=None) for i in range(5)]
                actors[0].prev=actors[4]
                for i in range(2,5):actors[i].prev=actors[i-1]
                found=model.overlap(SimpleNamespace(actors=actors));before=len(model.calls);model.pending=None if pending==255 else pending;model.finish(tick)
                calls=[v for _,i,arg in model.calls[before:] for v in (100+i,arg)]
                self.assertEqual([found.index if found else 255,model.iframes,*calls],row,(tick,step))
            self.assertEqual([t for t,i,a in model.calls if i==0 and t<70],[0,21,42,63])

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator and BIOS')
class CollisionRomTests(unittest.TestCase):
    def test_while_and_for_loops_in_a_collision_script_run_in_rom(self):
        from tools.lab_collision_loops import run
        for witness in (False,True):
            with self.subTest(witness=witness),tempfile.TemporaryDirectory() as d:
                result=run(Path(d)/'lab',*ENV,witness=witness)
                self.assertTrue(result['passed'],result)

    def run_case(self,**options):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);build_case(root,ENV[0],**options)
            proc=subprocess.run([sys.executable,'-B','-m','tools.probe_stream',str(root),'--emulator',ENV[1],'--bios',ENV[2],'--out',str(root/'probe')],capture_output=True,text=True,timeout=180)
            result=json.loads((root/'probe/probe.json').read_text());source=json.loads((root/'source.json').read_text())
            if options.get('mutate'):
                self.assertNotEqual(proc.returncode,0);self.assertTrue(result['frames_with_mismatch'])
            else:
                self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr);self.assertEqual(result['frames_with_mismatch'],[]);self.assertEqual(result['dropped_frames'],[])
            return source
    def test_contact_callbacks_do_not_lock_player(self):
        s=self.run_case();self.assertEqual(s['collision_calls'][:2],[[31,0,2],[31,5,0]])
        trace={r[0]:r for r in s['player_trace']};self.assertNotEqual(trace[40][1:3],trace[60][1:3])
    def test_group_three_and_initial_overlap(self):
        s=self.run_case(group='3',overlap=True);self.assertEqual(s['collision_calls'][:2],[[13,0,8],[13,5,0]])
    def test_repeated_contacts_keep_twenty_protected_frames(self):
        s=self.run_case(repeat=True,schedule=[(20,180,8)]);self.assertEqual([r[0] for r in s['collision_calls'] if r[1]==0][:4],[31,52,73,94])
    def test_on_update_and_collision_callbacks_run_together(self):
        self.run_case(updates=True)
    def test_mutation_no_invulnerability(self):
        self.run_case(repeat=True,schedule=[(20,180,8)],mutate=('src/scene_actors.c','collision_iframes = 20u;','collision_iframes = 0u;'))
    def test_mutation_missing_overlap(self):
        self.run_case(overlap=True,mutate=('src/scene_actors.c','hit = player_overlap();','hit = 255u;'))
    def test_mutation_callback_lock(self):
        self.run_case(mutate=('src/gbvm.c','if (ctx) *(ctx->stack_ptr++) = argument;','if (ctx) { *(ctx->stack_ptr++) = argument; vm_lock(ctx); }'))

if __name__=='__main__':unittest.main()
