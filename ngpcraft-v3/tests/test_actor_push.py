"""Published Actor Push builder, original GBVM C state machine and native ROM."""
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.gbvm.emit import move_features
from ngpcraft.gbvm.assembler import load_model
from ngpcraft.pipeline import export_project
from ngpcraft.scene_actors import _push_source
from ngpcraft.toolchain import build_rom
from tools.extract_gbvm_runtime import cut
from tools.lab_actor_push import project_case, run
from tools.reference import REFERENCE

ROOT=Path(__file__).resolve().parents[1]
ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]

class PushBuilderTests(unittest.TestCase):
    def test_published_event_tests_actor_only(self):
        source=(REFERENCE/'test/events/eventActorPush.test.js').read_text(encoding='utf-8')
        self.assertIn('expect(mockActorPush).toBeCalledWith(false)',source)
        self.assertIn('expect(mockActorPush).not.toBeCalled()',source)
        for kind,entity,expected in [('actor',{'id':'abc'},[('active','abc'),('push',False)]),
                                     ('trigger',{'id':'abc'},[]),('scene',{'id':'abc'},[]),('actor',{},[])]:
            calls=[]
            class Mock:
                options={'entityType':kind,'entity':entity}
                def actor_set_active(self,v):calls.append(('active',v))
                def actor_push(self,v):calls.append(('push',v))
            HANDLERS['EVENT_ACTOR_PUSH']({'continue':False},Mock())
            self.assertEqual(calls,expected)

    def test_direction_branch_order_distances_stack_and_legacy_opcode(self):
        for slide,offset in ((False,512),(True,25600)):
            out=[];b=ScriptBuilder(out,{'scene':{'actors':[{'id':'a'}]},'entityType':'actor','entity':{'id':'a'}})
            HANDLERS['EVENT_ACTOR_PUSH']({'continue':slide},b)
            text='\n'.join(out)
            self.assertEqual(text.count(f'.R_INT16    {offset}'),4)
            self.assertEqual(text.count('.R_OPERATOR .MAX'),2)
            self.assertEqual(text.count('VM_ACTOR_GET_DIR'),1)
            self.assertEqual(text.count('VM_ACTOR_GET_POS'),1)
            self.assertIn('VM_ACTOR_MOVE_TO        .LOCAL_ACTOR',text)
            self.assertNotIn('VM_ACTOR_MOVE_TO_INIT',text)
            for a,b_ in zip(('.DIR_UP','.DIR_LEFT'),('.DIR_LEFT','.DIR_RIGHT')):
                self.assertLess(text.index(a),text.index(b_))
            self.assertEqual(b.stack_ptr,0)
            self.assertEqual(move_features(text,load_model()),('actors','walls'))

    def test_export_selects_only_requested_push_and_reader(self):
        from tools.platform_oracle import project_case as base_case
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            base=root/'base';base.mkdir()
            m=export_project(base_case(base),base/'export')
            self.assertNotIn('actor_push',m['features'])
            self.assertNotIn('ngc_actor_push_step',(base/'export/src/scene_actors.c').read_text())
            self.assertNotIn('ngc_actor_get_dir',(base/'export/src/scene_actors.c').read_text())
            pushed=root/'push'
            m=export_project(project_case(pushed),pushed/'export')
            self.assertIn('actor_push',m['features']);self.assertIn('actor_directions',m['features'])
            self.assertNotIn('actor_moves',m['features'])
            c=(pushed/'export/src/scene_actors.c').read_text()
            self.assertNotIn('ngc_actor_move_xy(',c)
            self.assertLess(m['gbvm']['stack_budget']['words_per_context'],64)
            if ENV[0]:
                built=build_rom(pushed/'export',ENV[0])
                self.assertIn('_ngc_actor_push_step',built['link_audit']['public_symbols'])
                self.assertNotIn('_ngc_actor_move_xy',built['link_audit']['public_symbols'])

    def test_push_and_split_moves_dispatch_in_multiple_scene_export(self):
        from tools.lab_scene_cuts import project_case as cuts
        from tools.demo_actor_moves import ev,num
        from tools.demo_gbs_scene import write_json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            project,_=cuts(root)
            path=next((project/'project/scenes').rglob('actors/*.gbsres'))
            actor=json.loads(path.read_text(encoding='utf-8'))
            actor['startScript']=[ev('push','EVENT_ACTOR_PUSH',**{'continue':False}),
                                  ev('move','EVENT_ACTOR_MOVE_TO',actorId='$self$',x=num(12),y=num(12),
                                     units='tiles',collideWith=['actors'],moveType='horizontal',lockDirection=[])]
            write_json(path,actor)
            m=export_project(project,root/'export')
            self.assertIn('actor_push',m['features']);self.assertIn('actor_moves',m['features'])
            c=(root/'export/src/scene_actors.c').read_text()
            self.assertIn('ngc_actor_push_step(',c)
            self.assertIn('s0_ngc_actor_push_step' if 's0_ngc_actor_push_step' in c else 's1_ngc_actor_push_step',c)
            if ENV[0]:build_rom(root/'export',ENV[0])

@unittest.skipUnless(ENV[3],'requires Clang wasm and Node')
class PushReferenceCTests(unittest.TestCase):
    def test_state_machine_matches_unmodified_gbvm_every_step(self):
        source=(REFERENCE/'appData/engine/gbvm/src/core/vm_actor.c').read_text(encoding='utf-8')
        original=cut(source,'vm_actor_move_to')
        header=r'''
typedef unsigned char UBYTE;typedef unsigned short UWORD;typedef short INT16;
typedef unsigned short UINT16;typedef short WORD;typedef unsigned char direction_e;
typedef struct {UWORD x,y;} point_t;
typedef struct {short left,right,top,bottom;} bounds_t;
typedef struct {point_t pos;bounds_t bounds;UBYTE flags,move_speed,dir,anim;} actor_t;
typedef struct {INT16 ID;UWORD X,Y;UBYTE ATTR;} act_move_to_t;
typedef struct {UBYTE flags,waitable;short PC;} SCRIPT_CTX;
#define OLDCALL
#define BANKED
#define TRUE 1
#define DIR_DOWN 0
#define DIR_RIGHT 1
#define DIR_UP 2
#define DIR_LEFT 3
#define ACTOR_FLAG_INTERRUPT 128
#define ACTOR_ATTR_H_FIRST 1
#define ACTOR_ATTR_CHECK_COLL_WALLS 2
#define ACTOR_ATTR_DIAGONAL 4
#define ACTOR_ATTR_RELATIVE_SNAP_PX 8
#define ACTOR_ATTR_RELATIVE_SNAP_TILE 16
#define ACTOR_ATTR_RELATIVE 24
#define ACTOR_ATTR_CHECK_COLL_ACTORS 32
#define MOVE_INACTIVE 0
#define MOVE_ALLOW_H 1
#define MOVE_ALLOW_V 2
#define MOVE_DIR_H 4
#define MOVE_DIR_V 8
#define MOVE_ACTIVE_H 16
#define MOVE_ACTIVE_V 32
#define MOVE_NEEDED_H 64
#define MOVE_NEEDED_V 128
#define MOVE_H 65
#define MOVE_V 130
#define CHK_FLAG(f,v) ((f)&(v))
#define SET_FLAG(f,v) ((f)|=(v))
#define CLR_FLAG(f,v) ((f)&=~(v))
#define SUBPX_SNAP_PX(v) ((v)&0xFFE0u)
#define SUBPX_SNAP_TILE(v) ((v)&0xFF00u)
#define INSTRUCTION_SIZE 1
#define VM_REF_TO_PTR(idx) (&global_params)
static actor_t actors[2];static act_move_to_t global_params;static SCRIPT_CTX ctx;
static unsigned short act_x[2],act_y[2],dest[2];
static unsigned char act_dir[2],act_speed[2],act_flags[2],anim[2],flags;
static const short act_bounds[8]={-64,255,-128,255,-96,191,-96,191};
static unsigned forced_hit,clamp_x,clamp_y;
void actor_set_anim_moving(actor_t*a){a->anim=a->dir+4;}
void actor_set_anim_idle(actor_t*a){a->anim=a->dir;}
void actor_set_dir(actor_t*a,unsigned d,unsigned moving){a->dir=d;a->anim=d+4;}
void set_anim(unsigned i,unsigned v){anim[i]=v;}
void ngc_actor_set_dir(unsigned i,unsigned d,unsigned moving){act_dir[i]=d;anim[i]=d+4;}
unsigned short check_collision_horizontal(unsigned x,unsigned y,bounds_t*b,unsigned end){return clamp_x?clamp_x:end;}
unsigned short check_collision_vertical(unsigned x,unsigned y,bounds_t*b,unsigned end){return clamp_y?clamp_y:end;}
unsigned short saturating_add_u16(unsigned short a,short b){return a+b;}
actor_t* actor_overlapping_bb(bounds_t*b,point_t*p,actor_t*a){return forced_hit?actors+1:0;}
unsigned char overlapping(unsigned i){return forced_hit?2:0;}
void ngc_actor_move_init(unsigned i,unsigned short*p,unsigned char attr){
 act_flags[i]&=127;act_x[i]&=0xFFE0;act_y[i]&=0xFFE0;
 if(clamp_y)p[1]=clamp_y;if(clamp_x)p[0]=clamp_x;
}
'''
        tail=r'''
void reset(unsigned x,unsigned y,unsigned dx,unsigned dy,unsigned speed,unsigned dir,unsigned cx,unsigned cy){
 actors[0].pos.x=act_x[0]=x;actors[0].pos.y=act_y[0]=y;
 params.ID=0;params.X=dest[0]=dx;params.Y=dest[1]=dy;params.ATTR=34;
 actors[0].move_speed=act_speed[0]=speed;actors[0].dir=act_dir[0]=dir;
 actors[0].anim=anim[0]=dir;actors[0].flags=act_flags[0]=128;
 actors[0].bounds.left=-64;actors[0].bounds.right=255;
 actors[0].bounds.top=-128;actors[0].bounds.bottom=255;
 actors[1].pos.x=act_x[1]=x+100;actors[1].pos.y=act_y[1]=y+100;
 actors[1].bounds.left=-96;actors[1].bounds.right=191;
 actors[1].bounds.top=-96;actors[1].bounds.bottom=191;
 forced_hit=0;clamp_x=cx;clamp_y=cy;ctx.flags=flags=0;
}
void interrupt(void){actors[0].flags|=128;act_flags[0]|=128;}
void hit(unsigned v){forced_hit=v;}
unsigned step_ref(void){ctx.PC=100;ctx.waitable=0;vm_actor_move_to(&ctx,0);return ctx.PC!=100;}
unsigned step_gen(void){return ngc_actor_push_step(0,dest,&flags);}
unsigned get_ref(unsigned k){switch(k){case 0:return actors[0].pos.x;case 1:return actors[0].pos.y;
case 2:return ctx.flags;case 3:return actors[0].dir;case 4:return actors[0].anim;
case 5:return params.X;case 6:return params.Y;case 7:return actors[0].flags;}return 0;}
unsigned get_gen(unsigned k){switch(k){case 0:return act_x[0];case 1:return act_y[0];
case 2:return flags;case 3:return act_dir[0];case 4:return anim[0];
case 5:return dest[0];case 6:return dest[1];case 7:return act_flags[0];}return 0;}
'''
        tail=tail.replace('params.', 'global_params.')
        cases=[]
        for speed in (0,1,16,32,64,255):
            for direction,(dx,dy) in enumerate(((0,512),(512,0),(0,-512),(-512,0),(0,0),(512,-512))):
                for collision,interrupt in ((0,0),(1,0),(3,0),(0,2)):
                    cases.append([3339,3353,3328+dx,3328+dy,speed,direction%4,0,0,collision,interrupt])
        rng=random.Random(432)
        for _ in range(300):
            x,y,dx,dy=[rng.randrange(65536) for _ in range(4)]
            cases.append([x,y,dx,dy,rng.randrange(256),rng.randrange(4),0,0,0,0])
        cases += [[3339,3353,4096,4096,32,0,3456,3584,0,0]]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'proof.c').write_text(header+original+'\n'+'\n'.join(_push_source(True))+tail,encoding='utf-8')
            p=subprocess.run([ENV[3],'--target=wasm32','-nostdlib','-O2','-Wno-everything',
                              '-Wl,--no-entry','-Wl,--export-all',str(root/'proof.c'),'-o',str(root/'proof.wasm')],
                             capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            (root/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
            js=r'''const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[1]))).exports;
const cases=JSON.parse(fs.readFileSync(process.argv[2]));let steps=0;
for(const c of cases){e.reset(...c.slice(0,8));for(let n=0;n<130;n++){
e.hit(c[8]&&n>=c[8]?1:0);if(c[9]&&n===c[9])e.interrupt();
const a=e.step_ref(),b=e.step_gen();const r=[a],g=[b];
for(let k=0;k<8;k++){r.push(e.get_ref(k));g.push(e.get_gen(k));}
if(JSON.stringify(r)!==JSON.stringify(g))throw Error(JSON.stringify({c,n,r,g}));steps++;if(!a)break;
}}process.stdout.write(String(steps));'''
            p=subprocess.run(['node','-e',js,str(root/'proof.wasm'),str(root/'cases.json')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertGreater(int(p.stdout),10000)

@unittest.skipUnless(all(ENV[:3]),'requires Toshiba, native emulator and BIOS')
class PushRomTests(unittest.TestCase):
    def test_two_tiles_wall_limits_and_actor_contact_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=run(Path(tmp)/'positive',*ENV[:3])
        self.assertEqual(r['mismatches'],[],r)
        self.assertEqual(r['completed_cases'],9)
        self.assertEqual(r['motion_mismatches'],[],r['motion_mismatches'][:1])
        self.assertEqual(r['dropped_frames'],[])

    def test_wrong_direction_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=run(Path(tmp)/'witness',*ENV[:3],witness='direction')
        self.assertTrue(r['mismatches'])

    def test_missing_actor_collision_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=run(Path(tmp)/'witness',*ENV[:3],witness='actors')
        self.assertTrue(any(m['case']==9 for m in r['mismatches']))

if __name__=='__main__':unittest.main()
