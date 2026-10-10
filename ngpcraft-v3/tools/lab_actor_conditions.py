"""M3 actor predicates: Platformer ROM, original VM C, and source sprite pixels."""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_source, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.sprites import editor_frame_origin
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.demo_backgrounds import write_new
from tools.extract_gbvm_runtime import cut
from tools.platform_oracle import project_case as platform_case, rom_trace, reference_run, compare
from tools.probe_rom import verified_tick, power_on_ram, write_frame
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets, editor_frame

SCHEDULE = [[20,110,8],[150,260,4],[300,390,8],[420,530,4],[560,650,8],[680,800,4]]
TURNIP = 'f88eea80-c5b4-4e10-b49f-7a8aa8bd477e'

def value(kind,a,b): return {'type':kind,'valueA':a,'valueB':b}
def var(i): return {'type':'variable','value':str(i)}
def set_var(i,n): return ev(f'set-{i}-{n}','EVENT_SET_VALUE',variable=str(i),value=num(n))
def branch(e,yes,no):
    e['children']={'true':yes,'false':no}
    return e

def project_case(root):
    project=platform_case(root,platform=('platform',96,96,32))
    path=next((project/'project/scenes').rglob('actors/*.gbsres'))
    actor=json.loads(path.read_text(encoding='utf-8'))
    actor.update(name='Turnip actor conditions',spriteSheetId=TURNIP,collisionExtraFlags=[],
                 collisionGroup='',animSpeed=255,script=[])
    script=[ev('counter','EVENT_INC_VALUE',variable='20')]
    for i in (7,13):script.append(set_var(i,96 if i==7 else 7))
    phase=value('lt',value('mod',var(20),num(60)),num(30))
    script.append(branch(ev('height','EVENT_IF',condition=phase),
        [ev('high','EVENT_ACTOR_SET_POSITION',actorId='$self$',x=num(96),y=num(32),units='pixels'),set_var(12,1)],
        [ev('low','EVENT_ACTOR_SET_POSITION',actorId='$self$',x=num(96),y=num(128),units='pixels'),set_var(12,2)]))
    for i,direction in enumerate(('left','right','up','down')):
        script.append(branch(ev(f'rel-{i}','EVENT_IF_ACTOR_RELATIVE_TO_ACTOR',actorId='player',
                               otherActorId='$self$',operation=direction),[set_var(i,1)],[set_var(i,0)]))
    script.append(ev('pose','EVENT_ACTOR_SET_FRAME',actorId='$self$',frame=var(1)))
    script += [set_var(21,1),ev('yield-a','EVENT_WAIT',units='frames',frames=num(1))]
    cases=[(4,'tiles',num(12),num(4)),(5,'pixels',value('add',var(7),num(0)),num(32)),
           (6,'pixels',num(96),num(33)),(14,'tiles',num(13),num(4))]
    for i,units,x,y in cases:
        script.append(branch(ev(f'at-{i}','EVENT_IF_ACTOR_AT_POSITION',actorId='$self$',x=x,y=y,units=units),
                              [set_var(i,1)],[set_var(i,0)]))
    script += [set_var(21,2),ev('yield-b','EVENT_WAIT',units='frames',frames=num(1))]
    for i,direction in enumerate(('left','right','up','down'),8):
        script.append(branch(ev(f'eq-{i}','EVENT_IF_ACTOR_RELATIVE_TO_ACTOR',actorId='$self$',
                               otherActorId='$self$',operation=direction),[set_var(i,1)],[set_var(i,0)]))
    script.append(branch(ev('no-else','EVENT_IF_ACTOR_RELATIVE_TO_ACTOR',actorId='$self$',
                           otherActorId='$self$',operation='left',__disableElse=True),[set_var(13,1)],[set_var(13,99)]))
    script += [set_var(21,3),ev('yield-c','EVENT_WAIT',units='frames',frames=num(1))]
    actor['updateScript']=script
    write_json(path,actor)
    return project

def reference_source():
    core=REFERENCE/'appData/engine/gbvm'
    vm=(core/'src/core/vm.c').read_text(encoding='utf-8')
    actor=(core/'src/core/vm_actor.c').read_text(encoding='utf-8')
    defines='\n'.join(re.findall(r'^#define VM_OP_.*$',(core/'include/vm.h').read_text(),re.M))
    header='''#include <stdint.h>
#include <stdbool.h>
typedef unsigned char UBYTE; typedef unsigned short UWORD;
typedef short INT16; typedef signed char INT8; typedef unsigned char UINT8;
typedef short WORD; typedef unsigned DUMMY0_t; typedef unsigned DUMMY1_t;
typedef struct { UWORD x,y; } pos_t;
typedef struct { pos_t pos; } actor_t;
typedef struct { UWORD ID,X,Y; } act_set_pos_t;
typedef struct { unsigned char *PC; short *stack_ptr; unsigned char bank; } SCRIPT_CTX;
#define VM_REF_TO_PTR(idx) ((idx)<0?THIS->stack_ptr+(idx):script_memory+(idx))
#define SWITCH_ROM(bank) ((void)0)
#define TRUE 1
#define FALSE 0
static actor_t actors[2]; static INT16 script_memory[32],stack[64];
int abs(int x){return x<0?-x:x;}
unsigned atan2(WORD a,WORD b){return 0;}
unsigned isqrt(UWORD a){return 0;}
unsigned randw(void){return 0;}
'''
    tail='''static UBYTE bytes[128];static unsigned n;
void byte(unsigned v){bytes[n++]=(UBYTE)v;}
void word(unsigned v){byte(v);byte(v>>8);}
void ref(unsigned i){byte(VM_OP_REF);word(i);}
void constant(unsigned v){byte(VM_OP_INT16);word(v);}
unsigned check(unsigned kind,unsigned ax,unsigned ay,unsigned bx,unsigned by,unsigned cx,unsigned cy,unsigned shift){
    SCRIPT_CTX ctx; UBYTE fail;
    actors[0].pos.x=ax;actors[0].pos.y=ay;actors[1].pos.x=bx;actors[1].pos.y=by;
    script_memory[0]=0;script_memory[3]=1;ctx.stack_ptr=stack;ctx.bank=0;
    vm_actor_get_pos(&ctx,0);vm_actor_get_pos(&ctx,3);n=0;
    if(kind<4){ref(kind<2?1:2);ref(kind<2?4:5);byte((kind==0||kind==2)?VM_OP_LT:VM_OP_GT);}
    else if(kind>=8&&kind<12){ref(kind<10?4:5);ref(kind<10?4:5);byte((kind==8||kind==10)?VM_OP_LT:VM_OP_GT);}
    else{ref(4);constant(shift);byte(VM_OP_SHR);constant(cx);byte(VM_OP_EQ);
         ref(5);constant(shift);byte(VM_OP_SHR);constant(cy);byte(VM_OP_EQ);byte(VM_OP_AND);}
    byte(VM_OP_STOP);ctx.PC=bytes;vm_rpn(0,0,&ctx);
    vm_if_const(&ctx,VM_OP_EQ,-1,0,&fail,1);
    return ctx.PC!=&fail;
}
'''
    return header+defines+'\n'+cut(actor,'vm_actor_get_pos')+'\n'+cut(vm,'vm_rpn')+'\n'+cut(vm,'vm_if_const')+'\n'+tail

def reference_run_conditions(root,rows,clang,boundaries=False):
    write_new(root/'conditions-reference.c',reference_source().encode())
    proc=subprocess.run([clang,'--target=wasm32','-nostdlib','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',
                         str(root/'conditions-reference.c'),'-o',str(root/'conditions-reference.wasm')],capture_output=True,text=True)
    if proc.returncode:raise ValueError(proc.stderr)
    write_json(root/'condition-inputs.json',rows)
    js='''const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const rows=JSON.parse(fs.readFileSync(process.argv[3]));let out=[];
for(const r of rows){let v={};for(let k=0;k<12;k++)if(k!==7)v[k]=e.check(k,...r.player,...r.actor,k===4?12:96,k===6?33:k===4?4:32,k===4?8:5);
v[14]=e.check(14,...r.player,...r.actor,13,4,8);v[13]=7;out.push(v);}process.stdout.write(JSON.stringify(out));'''
    write_new(root/'conditions-run.js',js.encode())
    proc=subprocess.run(['node',str(root/'conditions-run.js'),str(root/'conditions-reference.wasm'),str(root/'condition-inputs.json')],capture_output=True,text=True)
    if proc.returncode:raise ValueError(proc.stderr)
    return json.loads(proc.stdout)

def run(out,toolchain,emulator,bios,clang,frames=840,witness=None,schedule=SCHEDULE):
    if out.exists():raise ValueError('output must be new')
    out.mkdir(parents=True)
    path=project_case(out)
    manifest=export_project(path,out/'export')
    if witness:
        rel='src/scene_actors.c' if witness=='position' else 'src/gbvm.c'
        old,new=('    p[1] = act_x[i];','    p[1] = (unsigned short)(act_x[i] + 32u);') if witness=='position' else ('*A = (*A <  *B);','*A = (*A <= *B);')
        f=out/'export'/rel;s=f.read_text(encoding='utf-8');assert s.count(old)==1,(old,s.count(old))
        tmp=f.with_suffix('.tmp');write_new(tmp,s.replace(old,new).encode());tmp.replace(f)
        manifest['sha256'][rel]=hashlib.sha256(f.read_bytes()).hexdigest();write_json(out/'export/export.json',manifest)
    build=build_rom(out/'export',toolchain)
    project=load_project(path);engine=build_engine_scene(project,project.scenes[0])
    background=background_source(project,project.scenes[0]);view_h,top=view_of(background)
    symbols=build['link_audit']['public_symbols']
    def addr(n):return symbols['_'+n]['address']
    sys.path.insert(0,str(Path(emulator).resolve()));from core import native
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    machine=native.NativeMachine(rom,bios=bios_data);machine.set_timing_silicon(word_wait=10,bios_wait=8)
    machine.reset(bios_handoff=True);power_on_ram(machine,'noise');machine.write(0xA000,bytes(8192))
    machine.set_breakpoints([verified_tick(rom,build)])
    def word(a):return int.from_bytes(machine.read(a,2),'little')
    rows,pictures=[],[];previous=None
    indexes=list(range(15))+[20,21]
    locations={i:i for i in range(15)}
    locations.update({i:manifest['variables'][f'VAR_VARIABLE_{i}'] for i in (20,21)})
    for k in range(frames+1):
        stop,_=machine.run(3_000_000,record=False)
        if stop.stop_status!=40:raise ValueError('game tick not reached')
        row={'time':word(addr('ngc_game_time')),'player':[word(addr('act_x')),word(addr('act_y'))],
             'actor':[word(addr('act_x')+2),word(addr('act_y')+2)],
             'scroll':[word(addr('ngc_scroll')),word(addr('ngc_scroll')+2)],
             'vars':{str(i):word(addr('script_memory')+2*locations[i]) for i in indexes},
             'frames_between':None if previous is None else stop.frame_count-previous}
        rows.append(row);pictures.append(array('H',(v&4095 for v in machine.framebuffer())));previous=stop.frame_count
        held=0
        for a,b,mask in schedule:
            if a<=k<b:held|=mask
        machine.write(0x00B0,bytes([held]))
    inputs=[{'player':rows[k-1]['player'],'actor':r['actor']} for k,r in enumerate(rows) if k]
    reference=reference_run_conditions(out,inputs,clang)
    differences=[];cached={str(i):0 for i in list(range(7))+list(range(8,12))+[13,14]};snapshots=[]
    for k,(row,expected) in enumerate(zip(rows[1:],reference),1):
        group=row['vars']['21']
        keys=range(4) if group==1 else (4,5,6,14) if group==2 else range(8,12)
        for key in keys:cached[str(key)]=expected[str(key)]
        cached['13']=7
        snapshots.append(dict(cached))
        for key,v in cached.items():
            if row['vars'][key]!=v:differences.append({'tick':k,'variable':key,'actual':row['vars'][key],'expected':v})
    raw,image=load_sheets(path)['turnip'];palettes=sprite_palettes(project,project.scenes[0])
    sprites=[editor_frame(raw,image,f,raw.get('spriteMode') or project.settings['spriteMode'],False)
             for f in raw['states'][0]['animations'][0]['frames']]
    pixel_diff=[]
    for k in range(3,len(rows)):
        row=rows[k-1];pose=snapshots[k-2]['1'];sx,sy=rows[k]['scroll']
        ox,oy=editor_frame_origin(raw,(row['actor'][0]>>5)-sx,(row['actor'][1]>>5)-sy);oy+=top
        expected={(x,y):background.pixels[(sy+y-top)*background.width+sx+x]
                  for y in range(max(top,oy-2),min(top+view_h,oy+raw['canvasHeight']+2))
                  for x in range(max(0,ox-2),min(160,ox+raw['canvasWidth']+2))}
        for (x,y),(v,pal) in sprites[pose].items():
            if (ox+x,oy+y) in expected:expected[(ox+x,oy+y)]=palettes[pal][v-1]
        n=sum(pictures[k][y*160+x]!=v for (x,y),v in expected.items())
        if n:pixel_diff.append({'tick':k,'pixels':n,'pose':pose})
    physics_rows=rom_trace(out/'export',emulator,bios,frames,schedule)
    physics=reference_run(out,out/'export',project,physics_rows,clang)
    physics_diff=compare(physics_rows,physics['rows'])
    result={'ticks':frames,'condition_comparisons':len(inputs)*len(reference[0]),'condition_mismatches':len(differences),
            'condition_first':differences[:5],'pixel_checked_frames':max(0,len(rows)-3),'pixel_mismatch_frames':len(pixel_diff),
            'pixel_first':pixel_diff[:5],'physics_mismatch_ticks':len(physics_diff),'physics_first':physics_diff[:3],
            'dropped_frames':[k for k,r in enumerate(rows) if k>3 and r['frames_between']!=1],
            'values_seen':{str(i):sorted({r['vars'][str(i)] for r in rows[1:]}) for i in indexes if i!=20},
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'bios_sha256':hashlib.sha256(bios_data).hexdigest(),
            'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'silicon_timing':{'word_wait':10,'bios_wait':8},'features':manifest['features']}
    write_json(out/'comparison.json',result);write_json(out/'rows.json',rows)
    for k in (60,140,220,340,460,580,700,820):
        if k<len(pictures):write_frame(out/f'shot-{k}.ppm',pictures[k])
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('out','toolchain','emulator','bios'):p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--clang',required=True);p.add_argument('--frames',type=int,default=840)
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios,a.clang,a.frames);print(json.dumps(r,indent=2))
    return int(bool(r['condition_mismatches'] or r['pixel_mismatch_frames'] or r['physics_mismatch_ticks'] or r['dropped_frames']))
if __name__=='__main__':raise SystemExit(main())
