"""M4 spritesheet changes: original GBVM C, author PNGs and native NGPC ROM.

Reference runs use the published vm_actor_set_spritesheet, load_animations,
load_bounds and actor_set_frames. Hardware tile uploads are represented by
a recorder in wasm; on NGPC all reachable sheets reside in VRAM at scene load.
"""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_source, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_platform import states_order
from ngpcraft.background_stream import view_of
from ngpcraft.sprites import animation_map, to_engine_order, editor_frame_origin
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.demo_backgrounds import write_new
from tools.lab_actor_push import project_case as arena
from tools.lab_actor_states import extract_function
from tools.probe_rom import power_on_ram, verified_tick, write_frame
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets, editor_frame

PHASES = [('turnip','Explode',False),('player',None,None),('turnip',None,None),
          ('turnip',None,None),('turnip','Explode',True),('cat',None,None),
          ('chest',None,None),('turnip','Hurt',True)]

def project_case(root, pause=32):
    project = arena(root)
    scene_path = next((project/'project/scenes').rglob('scene.gbsres'))
    actor_path = scene_path.parent/'actors/push.gbsres'
    obstacle = scene_path.parent/'actors/obstacle.gbsres'
    assert obstacle.resolve().is_relative_to(project.resolve())
    obstacle.unlink()  # disposable derived project only
    ids = {s['name']:s['id'] for _,s in load_project(project).by_type['sprite']}
    actor = json.loads(actor_path.read_text(encoding='utf-8'))
    actor.update(name='Spritesheet transitions',spriteSheetId=ids['turnip'],animSpeed=3,direction='right')
    script = [ev('hide-player','EVENT_ACTOR_HIDE',actorId='player'),
              ev('disable-player','EVENT_ACTOR_COLLISIONS_DISABLE',actorId='player')]
    for phase,(sheet,state,loop) in enumerate(PHASES,1):
        script += [ev(f'sheet-{phase}','EVENT_ACTOR_SET_SPRITE',actorId='$self$',spriteSheetId=ids[sheet])]
        if state is not None:
            script += [ev(f'state-{phase}','EVENT_ACTOR_SET_STATE',actorId='$self$',spriteStateId=state,loopAnim=loop)]
        script += [ev(f'phase-{phase}','EVENT_SET_VALUE',variable='20',value=num(phase)),
                   ev(f'pause-{phase}','EVENT_WAIT',units='frames',frames=num(pause))]
    actor.update(startScript=script,updateScript=[])
    write_json(actor_path,actor)
    return project

def reference_source(engine, project, actor_index=1):
    core = REFERENCE/'appData/engine/gbvm/src/core'
    actor_c = (core/'actor.c').read_text(encoding='utf-8')
    vm_c = (core/'vm_actor.c').read_text(encoding='utf-8')
    data_c = (core/'data_manager.c').read_text(encoding='utf-8')
    start = actor_c.index('        if ((actor->anim_tick != ANIM_PAUSED)')
    end = actor_c.index('       if (CHK_FLAG(actor_flags, ACTOR_FLAG_PINNED))',start)
    header = '''typedef unsigned char UBYTE;
typedef unsigned short UWORD;
typedef short INT16;
typedef struct { UBYTE start, end; } animation_t;
typedef struct { short left,right,top,bottom; } rect16_t;
typedef struct { const animation_t *animations; const UWORD *animations_lookup; rect16_t bounds; } spritesheet_t;
typedef struct { UBYTE bank; const void *ptr; } sprite_t;
typedef struct { UBYTE animation,frame,frame_start,frame_end,flags,anim_tick,base_tile; sprite_t sprite; animation_t animations[8]; rect16_t bounds; } actor_t;
typedef struct { unsigned unused; } SCRIPT_CTX;
#define OLDCALL
#define BANKED
#define NONBANKED
#define ANIM_PAUSED 255
#define ACTOR_FLAG_ANIM_NOLOOP 4
#define ANIM_SET_DEFAULT 0
#define CHK_FLAG(f,v) ((f)&(v))
#define CURRENT_BANK 0
#define SWITCH_ROM(bank) ((void)(bank))
static actor_t actors[1]; static UBYTE actor_id;
#define VM_REF_TO_PTR(idx) (&actor_id)
void *memcpy(void *dst,const void *src,unsigned n) { unsigned char *d=dst; const unsigned char *s=src; while(n--) *d++=*s++; return dst; }
#define MemcpyBanked(dst,src,n,bank) memcpy(dst,src,n)
static unsigned loaded_sheet;
void load_sprite(UBYTE tile,const spritesheet_t *sprite,UBYTE bank) { (void)tile; (void)bank; loaded_sheet=(unsigned)sprite; }
'''
    tables = []
    order = states_order(project)
    from ngpcraft.scene_assets import sprite_sheets
    from ngpcraft.scene_build import sheet_bounds
    raw = sprite_sheets(project)
    for s,sheet in enumerate(engine.sheets):
        pairs = ','.join('{%d,%d}'%p for p in sheet.animation_offsets)
        lookup = ','.join(str((list(sheet.state_names).index(v) if v in sheet.state_names else 0)*8) for v in order)
        bounds = ','.join(map(str,sheet_bounds(raw[engine.sheet_ids[s]][0],'reference')))
        tables += [f'static const animation_t anim_{s}[] = {{{pairs}}};',
                   f'static const UWORD lookup_{s}[] = {{{lookup}}};',
                   f'static const spritesheet_t sheet_{s} = '+ '{'+f'anim_{s},lookup_{s},'+'{'+bounds+'}};']
    tables += ['static const spritesheet_t *sheets[] = {'+','.join(f'&sheet_{s}' for s in range(len(engine.sheets)))+'};']
    functions = extract_function(actor_c,'actor_set_frames') + '\n' + '''void actor_reset_anim(actor_t *a) { actor_set_frames(a,a->animations[a->animation].start,a->animations[a->animation].end+1); }
'''
    functions += '\n'.join(extract_function(data_c,n) for n in ('load_animations','load_bounds'))
    functions += '\n'+'\n'.join(extract_function(vm_c,n) for n in ('vm_actor_set_spritesheet','vm_actor_set_anim_set','vm_actor_set_flags'))
    a = engine.actors[actor_index]
    tail = f'''void reset(void) {{ SCRIPT_CTX ctx; actor_t *a=actors;
    a->animation={a['dir']};a->anim_tick={a['anim_tick']};a->flags={a['flags'] | 0x20};
    a->frame=a->frame_start=a->frame_end=0;vm_actor_set_spritesheet(&ctx,0,0,sheets[{a['sheet']}]);
}}
void select_sheet(unsigned s) {{ SCRIPT_CTX ctx; vm_actor_set_spritesheet(&ctx,0,0,sheets[s]); }}
void select_state(unsigned s,unsigned loop) {{ SCRIPT_CTX ctx;vm_actor_set_anim_set(&ctx,0,s);vm_actor_set_flags(&ctx,0,loop?0:4,4); }}
void step(unsigned game_time) {{ actor_t *actor=actors; UBYTE actor_flags=actor->flags;
{actor_c[start:end]}
}}
unsigned get(unsigned i) {{ actor_t *a=actors;
    return i==0?a->frame-a->frame_start:i==1?a->frame:i==2?a->flags:i==3?a->frame_end-a->frame_start:
           i==4?(UWORD)a->bounds.left:i==5?(UWORD)a->bounds.right:i==6?(UWORD)a->bounds.top:i==7?(UWORD)a->bounds.bottom:a->animation;
}}
'''
    return header+'\n'.join(tables)+'\n'+functions+tail

def reference_frames(out, engine, project, rows, clang, actor_index=1):
    write_new(out/'reference.c',reference_source(engine,project,actor_index).encode())
    proc = subprocess.run([clang,'--target=wasm32','-nostdlib','-O2','-Wno-everything','-Wl,--no-entry',
                           '-Wl,--export-all',str(out/'reference.c'),'-o',str(out/'reference.wasm')],capture_output=True,text=True)
    if proc.returncode: raise ValueError(proc.stderr)
    order = states_order(project)
    phases = [[next(i for i,s in enumerate(engine.sheets) if s.name==name),
               None if state is None else order.index(state),loop] for name,state,loop in PHASES]
    write_json(out/'steps.json',{'rows':rows,'phases':phases})
    js = '''const fs=require('fs'),e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const data=JSON.parse(fs.readFileSync(process.argv[3]));e.reset();e.step(0);let phase=0,result=[];
for(const r of data.rows){if(r.time>0){if(r.phase!==phase){phase=r.phase;if(phase){const [s,st,loop]=data.phases[phase-1];e.select_sheet(s);if(st!==null)e.select_state(st,loop?1:0);}}e.step(r.time-1);}result.push(Array.from({length:9},(_,i)=>e.get(i)));}
process.stdout.write(JSON.stringify(result));'''
    write_new(out/'reference.js',js.encode())
    proc = subprocess.run(['node',str(out/'reference.js'),str(out/'reference.wasm'),str(out/'steps.json')],capture_output=True,text=True)
    if proc.returncode: raise ValueError(proc.stderr)
    return json.loads(proc.stdout)

def run(out, toolchain, emulator, bios, clang, frames=300, pause=32, witness=None,
        case_builder=project_case, actor_index=1):
    if out.exists(): raise ValueError('output must be new')
    out.mkdir(parents=True)
    project_path = case_builder(out,pause)
    manifest = export_project(project_path,out/'export')
    if witness:
        rel = 'src/gbvm.c'
        path = out/'export'/rel
        text = path.read_text(encoding='utf-8')
        old = '    ngc_actor_set_spritesheet(*n_actor, sheet);'
        assert text.count(old)==1
        new = '    (void)n_actor; (void)sheet;'
        temp = path.with_suffix('.tmp');write_new(temp,text.replace(old,new).encode());temp.replace(path)
        manifest['sha256'][rel]=hashlib.sha256(path.read_bytes()).hexdigest();write_json(out/'export/export.json',manifest)
    build = build_rom(out/'export',toolchain)
    project = load_project(project_path)
    from ngpcraft.gbvm.sprite_assets import sprite_assets
    sprites = [s for _,s in project.by_type['sprite']]
    reachable = tuple((i,s['id']) for i,s in enumerate(sprites) if s['name'] in {p[0] for p in PHASES})
    engine = build_engine_scene(project,project.scenes[0],sprite_switches=reachable)
    background = background_source(project,project.scenes[0]);view_h,top=view_of(background)
    symbols = build['link_audit']['public_symbols']
    def addr(name): return symbols['_'+name]['address']
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    rom = (out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    machine = native.NativeMachine(rom,bios=bios_data)
    machine.set_timing_silicon(word_wait=10,bios_wait=8);machine.reset(bios_handoff=True)
    power_on_ram(machine,'noise');machine.write(0xA000,bytes(8192))
    machine.set_breakpoints([verified_tick(rom,build)])
    def word(address): return int.from_bytes(machine.read(address,2),'little')
    rows,pictures=[],[];previous=None
    # Resolve the marker through the exported variable table, never a fixed slot.
    var_index = manifest['variables']['VAR_VARIABLE_20']
    for _ in range(frames+1):
        stop,_=machine.run(3_000_000,record=False)
        if stop.stop_status!=40: raise ValueError('game tick not reached')
        rows.append({'time':word(addr('ngc_game_time')),'phase':word(addr('script_memory')+2*var_index),
                     'actor':[word(addr('act_x')+2*actor_index),word(addr('act_y')+2*actor_index)],
                     'scroll':[word(addr('ngc_scroll')),word(addr('ngc_scroll')+2)],
                     'frames_between':None if previous is None else stop.frame_count-previous})
        previous=stop.frame_count;pictures.append(array('H',(v&4095 for v in machine.framebuffer())))
    ref=reference_frames(out,engine,project,rows,clang,actor_index)
    sheets=load_sheets(project_path);palettes=sprite_palettes(project,project.scenes[0])
    mismatch=[];counts={};poses={};cache={}
    for k,actual in enumerate(pictures):
        row,expected=rows[max(0,k-1)],ref[max(0,k-1)]  # shadow OAM commits preceding tick
        if row['time']<3 or not row['phase']: continue
        phase=row['phase'];name,state,_=PHASES[phase-1]
        state=state or ''
        sheet=next(s for s in engine.sheets if s.name==name)
        state=state if state in sheet.state_names else sheet.state_names[0]
        raw,image=sheets[name];index=list(sheet.state_names).index(state)
        defs=to_engine_order(animation_map(raw['states'][index]['animations'],raw['states'][index]['animationType'],
                                           raw['states'][index]['flipLeft'],lambda a,f:(a,f)))
        animation,mirrored=defs[engine.actors[actor_index]['dir']];pose=expected[0];key=(name,index,pose)
        if key not in cache:
            cache[key]=editor_frame(raw,image,animation['frames'][pose],raw.get('spriteMode') or project.settings['spriteMode'],mirrored)
        sx,sy=row['scroll'];ox,oy=editor_frame_origin(raw,(row['actor'][0]>>5)-sx,(row['actor'][1]>>5)-sy);oy+=top
        wanted={(x,y):background.pixels[(sy+y-top)*background.width+sx+x]
                for y in range(max(top,oy-2),min(top+view_h,oy+raw['canvasHeight']+2))
                for x in range(max(0,ox-2),min(160,ox+raw['canvasWidth']+2))}
        for (x,y),(value,pal) in cache[key].items():
            if (ox+x,oy+y) in wanted: wanted[(ox+x,oy+y)]=palettes[pal][value-1]
        diff=sum(actual[y*160+x]!=v for (x,y),v in wanted.items())
        if diff: mismatch.append({'frame':k,'phase':phase,'pixels':diff,'pose':pose})
        counts[phase]=counts.get(phase,0)+1;poses.setdefault(phase,set()).add(pose)
    result={'frames':frames,'checked':sum(counts.values()),'pixel_mismatch_frames':len(mismatch),'first':mismatch[:8],
            'phases_seen':sorted(counts),'poses':{str(k):sorted(v) for k,v in poses.items()},
            'dropped_frames':[i for i,r in enumerate(rows) if i>3 and r['frames_between']!=1],
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'bios_sha256':hashlib.sha256(bios_data).hexdigest(),
            'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'silicon_timing':{'word_wait':10,'bios_wait':8},'features':manifest['features']}
    write_json(out/'comparison.json',result);write_json(out/'rows.json',rows)
    for phase in sorted(counts):
        candidates=[i for i,r in enumerate(rows) if r['phase']==phase and r['time']>3]
        i=candidates[min(len(candidates)-1,pause//2)];write_frame(out/f'phase-{phase}.ppm',pictures[i])
    return result

def run_collision(out, toolchain, emulator, bios):
    """Reuse the Push probe; replace authored bounds before every wall/contact path."""
    from dataclasses import replace
    from ngpcraft.scene_build import sheet_bounds
    from tools.lab_actor_push import run as push_run, endpoints
    box=[]
    def case(root):
        project_path=arena(root)
        project=load_project(project_path)
        path,sheet=next((path,s) for path,s in project.by_type['sprite'] if s['name']=='cat')
        sheet.update(boundsX=3,boundsY=-5,boundsWidth=9,boundsHeight=13)
        write_json(project_path/path,sheet)
        box.append(sheet_bounds(sheet,'authored collision probe'))
        actor_path=next((project_path/'project/scenes').rglob('actors/push.gbsres'))
        actor=json.loads(actor_path.read_text(encoding='utf-8'))
        actor['startScript'].insert(2,ev('collision-sheet','EVENT_ACTOR_SET_SPRITE',actorId='$self$',spriteSheetId=sheet['id']))
        write_json(actor_path,actor)
        return project_path
    def oracle(engine):
        return endpoints(replace(engine,bounds=(engine.bounds[0],box[0],engine.bounds[2])))
    return push_run(out,toolchain,emulator,bios,case_builder=case,endpoint_oracle=oracle)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('out','toolchain','emulator','bios','clang'): parser.add_argument('--'+name,required=True)
    args=parser.parse_args();result=run(Path(args.out),args.toolchain,args.emulator,args.bios,args.clang)
    print(json.dumps(result,indent=2));return int(bool(result['pixel_mismatch_frames'] or result['dropped_frames']))

if __name__=='__main__':raise SystemExit(main())
