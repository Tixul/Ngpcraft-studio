"""Platformer callback scripts: native variables/poses vs original state machine."""
from array import array
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_assets import background_source,sprite_palettes
from ngpcraft.background_stream import view_of
from ngpcraft.sprites import editor_frame_origin
from ngpcraft.toolchain import build_rom
from ngpcraft.gbvm.platform_callbacks import CALLBACKS
from tools import platform_oracle as oracle
from tools.demo_actor_moves import ev,num
from tools.demo_gbs_scene import write_json
from tools.demo_backgrounds import write_new
from tools.probe_rom import verified_tick,power_on_ram,write_frame
from tools.sprite_png import load_sheets,editor_frame

def project_case(root,callback_wait=0,detach_jump=False):
    path=oracle.project_case(root,platform=('platform',112,112,104))
    ap=next((path/'project/scenes').rglob('actors/*.gbsres'));a=json.loads(ap.read_text(encoding='utf-8'))
    a.update(spriteSheetId='f88eea80-c5b4-4e10-b49f-7a8aa8bd477e',collisionExtraFlags=[],collisionGroup='',animSpeed=255,updateScript=[])
    write_json(ap,a);sp=next((path/'project/scenes').rglob('scene.gbsres'));s=json.loads(sp.read_text(encoding='utf-8'))
    script=[]
    for event,(label,slot) in sorted(CALLBACKS.items(),key=lambda e:e[1][1]):
        e=ev('callback-'+event,'EVENT_SET_PLATFORMER_CALLBACK_SCRIPT',event=event)
        body=[ev('count-'+event,'EVENT_INC_VALUE',variable=str(slot))]
        if event in ('fallStart','jumpStart','groundStart'):
            body.append(ev('pose-'+event,'EVENT_ACTOR_SET_POSITION',actorId=a['id'],units='pixels',x=num(112),y=num({'fallStart':64,'jumpStart':32,'groundStart':104}[event])))
        if callback_wait:body.append(ev('wait-'+event,'EVENT_WAIT',units='frames',frames=num(callback_wait)))
        e['children']={'script':body};script.append(e)
    if detach_jump:script.append(ev('remove-jump','EVENT_REMOVE_PLATFORMER_CALLBACK_SCRIPT',event='jumpStart'))
    s['script']=script;write_json(sp,s);return path

def reference(root,export,project,rows,clang,callback_wait=0,detach_jump=False):
    source,runner=oracle.reference_source,oracle.RUN_JS
    def augmented(export,project):
        c,actors=source(export,project)
        if callback_wait:
            c=c.replace('void *script_execute(', 'static UWORD *callback_handles[21];static unsigned callback_deadlines[21];\nvoid *script_execute(')
            c=c.replace('scripts++; return NULL;', 'if(handle){unsigned slot=(unsigned)(unsigned long)ptr-300;*handle=1;callback_handles[slot]=handle;callback_deadlines[slot]=game_time+'+str(callback_wait+1)+';}scripts++; return NULL;')
            c=c.replace('void step(unsigned j, unsigned p) {', 'void step(unsigned j, unsigned p) {\n    unsigned i;\n    for(i=0;i<21;i++)if(callback_handles[i] && game_time>=callback_deadlines[i]){*callback_handles[i]=SCRIPT_TERMINATED;callback_handles[i]=0;}')
        c='static unsigned ngc_reference_unlocked=1;\n'+c.replace('platform_update(); actors_handle_player_collision();','if(ngc_reference_unlocked){platform_update(); actors_handle_player_collision();}')
        return c+'\nvoid set_locked(unsigned value){ngc_reference_unlocked=!value;}\nvoid attach(unsigned slot,unsigned tag){plat_events[slot].script_bank=0;plat_events[slot].script_addr=(const UBYTE *)(unsigned long)tag;}\n',actors
    oracle.reference_source=augmented
    oracle.RUN_JS=runner.replace('e.reset(...c.init);','e.reset(...c.init);for(let i=0;i<21;i++)'+('if(i!==4)' if detach_jump else '')+'e.attach(i,300+i);')
    oracle.RUN_JS=oracle.RUN_JS.replace('for(const s of c.steps){','let frame=0;const locked='+json.dumps([not row['platform_ran'] for row in rows[1:]])+';for(const s of c.steps){e.set_locked(locked[frame++]);')
    try:return oracle.reference_run(root,export,project,rows,clang)
    finally:oracle.reference_source,oracle.RUN_JS=source,runner

def run(out,toolchain,emulator,bios,clang,frames=720,witness=False,callback_wait=0,detach_jump=False):
    if out.exists():raise ValueError('output must be new')
    out.mkdir(parents=True);path=project_case(out,callback_wait,detach_jump);manifest=export_project(path,out/'export')
    if witness:
        p=out/'export/src/scene_actors.c';s=p.read_text(encoding='utf-8');old='    ngc_vm_platform_event(JUMP_INIT);';assert old in s
        temp=p.with_suffix('.tmp');write_new(temp,s.replace(old,'    /* witness: missing jump callback */').encode());temp.replace(p)
        manifest['sha256']['src/scene_actors.c']=hashlib.sha256(p.read_bytes()).hexdigest();write_json(out/'export/export.json',manifest)
    build=build_rom(out/'export',toolchain);project=load_project(path)
    rows=oracle.rom_trace(out/'export',emulator,bios,frames,oracle.SCHEDULE,variables=[str(i) for i in range(21)])
    symbols=build['link_audit']['public_symbols'];addr=lambda n:symbols['_'+n]['address']
    sys.path.insert(0,str(Path(emulator).resolve()));from core import native
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes();machine=native.NativeMachine(rom,bios=bios_data)
    machine.set_timing_silicon(word_wait=10,bios_wait=8);machine.reset(bios_handoff=True);power_on_ram(machine,'noise');machine.write(0xA000,bytes(8192))
    tick=verified_tick(rom,build);platform=addr('ngc_platform_update')
    machine.set_breakpoints([tick,platform]);previous=None;dropped=[];pictures=[];scrolls=[]
    for k in range(frames+1):
        ran=False
        while True:
            stop,_=machine.run(3_000_000,record=False)
            if stop.stop_status!=40:raise ValueError('tick not reached')
            if stop.stop_pc==tick:break
            ran=True
        rows[k]['platform_ran']=ran
        if k>3 and stop.frame_count-previous!=1:dropped.append(k)
        previous=stop.frame_count;pictures.append(array('H',(v&4095 for v in machine.framebuffer())))
        scrolls.append([int.from_bytes(machine.read(addr('ngc_scroll')+i*2,2),'little') for i in range(2)])
        held=0
        for start,end,mask in oracle.SCHEDULE:
            if start<=k<end:held|=mask
        machine.write(0xB0,bytes([held]))
    ref=reference(out,out/'export',project,rows,clang,callback_wait,detach_jump);physics=oracle.compare(rows,ref['rows'])
    events=[(tick,tag-300) for tick,tag,arg in ref['log'] if 300<=tag<321]
    counts=[0]*21;pose=104;mismatches=[];cursor=0
    for k,row in enumerate(rows):
        while cursor<len(events) and events[cursor][0]<=k-2:
            slot=events[cursor][1];counts[slot]+=1
            if slot in (0,2,4):pose={0:64,2:104,4:32}[slot]
            cursor+=1
        actual=[row['vars'][str(i)] for i in range(21)]
        row['reference_actor']=[112<<5,pose<<5]
        if actual!=counts or row['actors'][0]!=[112<<5,pose<<5]:
            mismatches.append({'tick':k,'counts':actual,'expected':counts.copy(),'pose':row['actors'][0],'expected_y':pose})
    engine=build_engine_scene(project,project.scenes[0]);bg=background_source(project,project.scenes[0]);view_h,top=view_of(bg)
    sheets=load_sheets(path);raw,image=sheets['turnip'];player=sheets[engine.sheets[0].name][0];pal=sprite_palettes(project,project.scenes[0])
    sprite=editor_frame(raw,image,raw['states'][0]['animations'][0]['frames'][0],raw.get('spriteMode') or project.settings['spriteMode'],False)
    pixels=[]
    for k in range(3,len(rows)):
        row=rows[k-1];sx,sy=scrolls[k];expected=[0]*(160*152)
        for y in range(view_h):expected[(y+top)*160:(y+top+1)*160]=bg.pixels[(sy+y)*bg.width+sx:(sy+y)*bg.width+sx+160]
        ox,oy=editor_frame_origin(raw,(row['reference_actor'][0]>>5)-sx,(row['reference_actor'][1]>>5)-sy);oy+=top
        for (x,y),(v,p) in sprite.items():
            x+=ox;y+=oy
            if 0<=x<160 and top<=y<top+view_h:expected[y*160+x]=pal[p][v-1]
        px,py=editor_frame_origin(player,(row['x']>>5)-sx,(row['y']>>5)-sy);py+=top
        n=sum(a!=e for j,(a,e) in enumerate(zip(pictures[k],expected)) if not(px-2<=j%160<px+player['canvasWidth']+2 and py-2<=j//160<py+player['canvasHeight']+2))
        if n:pixels.append({'tick':k,'pixels':n})
    result={'ticks':frames,'callback_wait':callback_wait,'detach_jump':detach_jump,'physics_mismatch_ticks':len(physics),'callback_mismatch_ticks':len(mismatches),'callback_first':mismatches[:3],
            'state_events':len(events),'slots_fired':sorted(set(s for _,s in events)), 'pixel_checked_frames':frames-2,'pixel_mismatch_frames':len(pixels),'pixel_first':pixels[:3],
            'dropped_frames':dropped,'features':manifest['features'],'rom_sha256':hashlib.sha256(rom).hexdigest(),
            'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),'bios_sha256':hashlib.sha256(bios_data).hexdigest(),
            'silicon_timing':{'word_wait':10,'bios_wait':8}}
    write_json(out/'comparison.json',result);write_json(out/'rows.json',rows);write_json(out/'reference-events.json',events)
    for y in (32,64,104):
        candidates=[k for k in range(4,len(rows)) if rows[k-1]['actors'][0][1]==y<<5 and rows[k]['x']<5000]
        if candidates:write_frame(out/f'pose-{y}.ppm',pictures[candidates[len(candidates)//2]])
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('out','toolchain','emulator','bios'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--clang',required=True);p.add_argument('--frames',type=int,default=720)
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios,a.clang,a.frames);print(json.dumps(r,indent=2))
    return int(bool(r['physics_mismatch_ticks'] or r['callback_mismatch_ticks'] or r['pixel_mismatch_frames'] or r['dropped_frames']))
if __name__=='__main__':raise SystemExit(main())
