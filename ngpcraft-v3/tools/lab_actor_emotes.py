"""M4 emotes: released C state machine, author PNGs and native NGPC timings."""
import argparse
from array import array
import hashlib,json,re,subprocess,sys
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_source, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.png import read_png
from ngpcraft.sprites import sprite_index
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev,num
from tools.demo_gbs_scene import write_json
from tools.demo_backgrounds import write_new
from tools.lab_actor_push import project_case as arena
from tools.probe_rom import power_on_ram,verified_tick,write_frame
from tools.extract_gbvm_runtime import cut
from tools.reference import REFERENCE

PHASES=[('push-actor','love'),('push-actor','anger'),('push-actor','question'),
        ('push-actor','shock'),('push-obstacle','sleep'),('player','music')]

def project_case(root):
    p=arena(root);project=load_project(p)
    emotes={e['name']:e['id'] for _,e in project.by_type['emote']}
    sprites={s['name']:s['id'] for _,s in project.by_type['sprite']}
    # Palette 7 differs from slot 0 so the pixel oracle can detect a wrong CP.C.
    palette=dict(project.by_type['palette'][0][1])
    palette.update(id='emote-probe-palette',name='Emote probe slot 7',colors=['FFF0C0','7040D0','2040A0','101020'])
    write_json(p/'project/palettes/emote_probe.gbsres',palette)
    scene_path=next((p/'project/scenes').rglob('scene.gbsres'))
    scene=json.loads(scene_path.read_text(encoding='utf-8'))
    slots=list(scene.get('spritePaletteIds',[]));slots.extend(['']*(8-len(slots)));slots[7]=palette['id']
    scene['spritePaletteIds']=slots;write_json(scene_path,scene)
    actor_path=next((p/'project/scenes').rglob('actors/push.gbsres'))
    actor=json.loads(actor_path.read_text(encoding='utf-8'))
    script=[ev('hide-player','EVENT_ACTOR_HIDE',actorId='player')]
    for phase,(actor_id,name) in enumerate(PHASES,1):
        if phase==2:script += [ev('hide-npc','EVENT_ACTOR_HIDE',actorId=actor_id)]
        if phase==3:script += [ev('disable-npc','EVENT_ACTOR_DEACTIVATE',actorId=actor_id)]
        if phase==4:script += [ev('cat-sheet','EVENT_ACTOR_SET_SPRITE',actorId=actor_id,spriteSheetId=sprites['cat'])]
        script += [ev(f'phase-{phase}','EVENT_SET_VALUE',variable='20',value=num(phase)),
                   ev(f'emote-{phase}','EVENT_ACTOR_EMOTE',actorId=actor_id,emoteId=emotes[name]),
                   ev(f'done-{phase}','EVENT_SET_VALUE',variable='21',value=num(phase)),
                   ev(f'wait-{phase}','EVENT_WAIT',units='frames',frames=num(4))]
    actor.update(name='Emote target',startScript=script,animSpeed=255)
    write_json(actor_path,actor)
    pinned_path=actor_path.parent/'obstacle.gbsres'
    pinned=json.loads(pinned_path.read_text(encoding='utf-8'))
    pinned.update(name='Pinned target',isPinned=True,x=140,y=90,persistent=True,animSpeed=255)
    write_json(pinned_path,pinned)
    return p

def reference_source():
    original=cut((REFERENCE/'appData/engine/gbvm/src/core/vm_actor.c').read_text(encoding='utf-8'),'vm_actor_emote')
    # wasm pointers are 32 bits. The GB far operand is 16 bits; only that
    # opaque argument's type changes, so sizeof() retains the real six bytes.
    original=original.replace('const unsigned char *emote_tiles','UWORD emote_tiles')
    return '''typedef unsigned char UBYTE;typedef unsigned short UWORD;typedef short INT16;
typedef struct { UBYTE id; } actor_t;
typedef struct { UBYTE flags,waitable;short PC; } SCRIPT_CTX;
#define OLDCALL
#define BANKED
#define INSTRUCTION_SIZE 1
#define EMOTE_TOTAL_FRAMES 60
#define NULL ((void*)0)
static actor_t actors[3];static actor_t *emote_actor;
static UBYTE emote_timer,actor_id;static UWORD tiles;
#define VM_REF_TO_PTR(idx) (&actor_id)
void load_emote(UWORD emote,UBYTE bank) {tiles=emote;(void)bank;}
'''+original+'''
static SCRIPT_CTX contexts[3];
void reset(void) {unsigned k;emote_actor=NULL;emote_timer=0;tiles=0;
 for(k=0;k<3;k++){contexts[k].flags=0;contexts[k].waitable=0;contexts[k].PC=100;}}
void call(unsigned context,unsigned actor,unsigned emote) {actor_id=actor;contexts[context].waitable=0;contexts[context].PC=100;
vm_actor_emote(&contexts[context],0,0,emote);}
unsigned get(unsigned i) {return i==0?(emote_actor?(unsigned)(emote_actor-actors):255):i==1?emote_timer:i==2?tiles:
 i<6?contexts[i-3].flags:i<9?contexts[i-6].waitable:(unsigned short)contexts[i-9].PC;}
'''

def wasm_compile(out,name,source,clang):
    write_new(out/(name+'.c'),source.encode())
    proc=subprocess.run([clang,'--target=wasm32','-nostdlib','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',
        str(out/(name+'.c')),'-o',str(out/(name+'.wasm'))],capture_output=True,text=True)
    if proc.returncode:raise ValueError(proc.stderr)

def reference_rows(out,rows,plans,clang):
    wasm_compile(out,'reference',reference_source(),clang)
    write_json(out/'replay.json',{'rows':rows,'plans':plans})
    js='''const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const data=JSON.parse(fs.readFileSync(process.argv[3]));e.reset();let phase=0,done=false,result=[];
for(const r of data.rows){if(r.phase!==phase){if(phase&&!done)throw Error('phase changed before original C completed');phase=r.phase;done=false;}
if(phase&&!done){e.call(0,...data.plans[phase-1]);done=e.get(3)===0;}
result.push(Array.from({length:12},(_,i)=>e.get(i)));}process.stdout.write(JSON.stringify(result));'''
    write_new(out/'replay.js',js.encode())
    proc=subprocess.run(['node',str(out/'replay.js'),str(out/'reference.wasm'),str(out/'replay.json')],capture_output=True,text=True)
    if proc.returncode:raise ValueError(proc.stderr)
    return json.loads(proc.stdout)

def run(out,toolchain,emulator,bios,clang,frames=420,witness=None):
    if out.exists():raise ValueError('output must be new')
    p=project_case(out);manifest=export_project(p,out/'export')
    if witness:
        rel='src/scene_sprites.c' if witness=='palette' else 'src/scene_actors.c'
        path=out/'export'/rel;text=path.read_text(encoding='utf-8')
        if witness=='palette':
            match=re.search(r'static const unsigned char spr_cpc\[\d+\] = \{.*?\n\};',text,re.S)
            assert match and re.search(r'\b7\b',match[0])
            text=text[:match.start()]+re.sub(r'\b7\b','0',match[0])+text[match.end():]
        else:
            old,new=('sy += act_emote_offsets[ngc_emote_timer];','sy += 0;') if witness=='bounce' else ('ngc_emote_timer == 60u','ngc_emote_timer == 30u')
            assert text.count(old)==1;text=text.replace(old,new)
        temp=path.with_suffix('.tmp');write_new(temp,text.encode());temp.replace(path)
        manifest['sha256'][rel]=hashlib.sha256(path.read_bytes()).hexdigest();write_json(out/'export/export.json',manifest)
    build=build_rom(out/'export',toolchain);project=load_project(p)
    engine=build_engine_scene(project,project.scenes[0])
    background=background_source(project,project.scenes[0]);palettes=sprite_palettes(project,project.scenes[0])
    from ngpcraft.background_stream import view_of
    view_h,top=view_of(engine.background)
    symbols=build['link_audit']['public_symbols']
    def addr(n):return symbols['_'+n]['address']
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    m=native.NativeMachine(rom,bios=bios_data);m.set_timing_silicon(word_wait=10,bios_wait=8)
    m.reset(bios_handoff=True);power_on_ram(m,'noise');m.write(0xA000,bytes(8192));m.set_breakpoints([verified_tick(rom,build)])
    def word(a):return int.from_bytes(m.read(a,2),'little')
    mem=addr('script_memory');markers={v:manifest['variables'][f'VAR_VARIABLE_{v}'] for v in (20,21)}
    rows=[];pictures=[];previous=None
    for _ in range(frames+1):
        stop,_=m.run(3_000_000,record=False)
        if stop.stop_status!=40:raise ValueError('tick not reached')
        rows.append({'time':word(addr('ngc_game_time')),'phase':word(mem+2*markers[20]),'done':word(mem+2*markers[21]),
          'actor':m.read(addr('ngc_emote_actor'),1)[0],'timer':m.read(addr('ngc_emote_timer'),1)[0],
          'positions':[[word(addr('act_x')+2*i),word(addr('act_y')+2*i)] for i in range(3)],
          'scroll':[word(addr('ngc_scroll')),word(addr('ngc_scroll')+2)],
          'frames_between':None if previous is None else stop.frame_count-previous})
        previous=stop.frame_count;pictures.append(array('H',(v&4095 for v in m.framebuffer())))
    by_name={e['name']:(i,path,e) for i,(path,e) in enumerate(project.by_type['emote'])}
    actor_indices={'player':0,'push-actor':1,'push-obstacle':2}
    plans=[[actor_indices[a],by_name[e][0]] for a,e in PHASES]
    # A duration witness deliberately changes phases before the reference
    # finishes; retain that as a detected scheduling failure, not an exception.
    reference_error=None
    try:expected=reference_rows(out,rows,plans,clang)
    except ValueError as exc:
        if not witness:raise
        reference_error=str(exc);expected=[]
    state_errors=[];pixel_errors=[];checked=0;seen=set();cache={}
    for k,(row,ref) in enumerate(zip(rows,expected)):
        if row['phase'] and [row['actor'],row['timer']]!=ref[:2]:state_errors.append({'time':row['time'],'actual':[row['actor'],row['timer']],'expected':ref[:2]})
        if row['phase'] and row['done']==row['phase'] and ref[0]!=255:
            state_errors.append({'time':row['time'],'error':'script continued before C completion'})
        if not k:continue
        row,ref=rows[k-1],expected[k-1]
        if not row['phase']:continue
        phase=row['phase'];seen.add(phase)
        actor_id,name=PHASES[phase-1];actor=actor_indices[actor_id]
        # The current actor sheet changed to cat in phase 4; the pinned actor
        # still subtracts logical scroll for its emote, as actor.c does.
        if name not in cache:
            _,path,_=by_name[name];width,height,pixels=read_png(project.root/Path(path).with_suffix(''))
            cache[name]=[sprite_index(*pixels[y*width+x]) for y in range(16) for x in range(16)]
        sx,sy=row['scroll'];px,py=row['positions'][actor];x=(px>>5)-sx
        raw_sheet = next(s for _,s in project.by_type['sprite'] if s['name']=='cat') if phase==4 else next(s for _,s in project.by_type['sprite'] if s['id']==engine.sheet_ids[engine.actors[actor]['sheet']])
        y=(py>>5)-sy-raw_sheet['canvasHeight']-8+top
        if ref[0]!=255 and ref[1]<15:y += [2,1,0,-1,-2,-3,-4,-5,-6,-5,-4,-3,-2,-1,0][ref[1]]
        diff=0
        for dy in range(16):
            for dx in range(16):
                xx,yy=x+dx,y+dy
                if not (0<=xx<160 and top<=yy<top+view_h):continue
                value=background.pixels[(sy+yy-top)*background.width+sx+xx]
                index=cache[name][dy*16+dx]
                if ref[0]!=255 and index:value=palettes[7][index-1]
                if pictures[k][yy*160+xx]!=value:diff+=1
        if diff:pixel_errors.append({'frame':k,'phase':phase,'pixels':diff,'timer':ref[1]})
        checked+=1
    transitions=[]
    for r in rows:
        if r['phase'] and (not transitions or transitions[-1][1]!=r['phase']):transitions.append([r['time'],r['phase']])
    result={'frames':frames,'checked':checked,'state_mismatch_ticks':len(state_errors),'state_first':state_errors[:5],
            'pixel_mismatch_frames':len(pixel_errors),'pixel_first':pixel_errors[:6],'reference_error':reference_error,
            'phases_seen':sorted(seen),'transitions':transitions,'completed_phase':rows[-1]['done'],
            'dropped_frames':[r['time'] for r in rows[4:] if r['frames_between']!=1],
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'bios_sha256':hashlib.sha256(bios_data).hexdigest(),
            'emulator_core_sha256':hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
            'silicon_timing':{'word_wait':10,'bios_wait':8},'features':manifest['features']}
    write_json(out/'comparison.json',result);write_json(out/'rows.json',rows)
    for phase in range(1,7):
        for timer in (2,8,15,60):
            hits=[k for k,r in enumerate(rows[:-1]) if r['phase']==phase and r['timer']==timer and r['actor']!=255]
            if hits:write_frame(out/f'phase-{phase}-timer-{timer}.ppm',pictures[hits[0]+1])
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('out','toolchain','emulator','bios','clang'):p.add_argument('--'+n,required=True)
    a=p.parse_args();r=run(Path(a.out),a.toolchain,a.emulator,a.bios,a.clang);print(json.dumps(r,indent=2))
    return int(bool(r['state_mismatch_ticks'] or r['pixel_mismatch_frames'] or r['reference_error'] or r['dropped_frames']))
if __name__=='__main__':raise SystemExit(main())
