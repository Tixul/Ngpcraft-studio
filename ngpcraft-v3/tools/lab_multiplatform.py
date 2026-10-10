"""Scene loads plus platform callbacks, measured at native function boundaries."""
from pathlib import Path
from array import array
import argparse,json,hashlib,shutil,sys,subprocess
from tools.lab_platform_callbacks import project_case as single
from tools.demo_gbs_scene import write_json
from tools.demo_actor_moves import ev,num
from tools.demo_scene_switch import switch
from tools.probe_rom import verified_tick,power_on_ram,write_frame
from tools import platform_oracle as oracle
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_assets import background_source,sprite_palettes
from ngpcraft.background_stream import view_of
from ngpcraft.sprites import editor_frame_origin
from ngpcraft.toolchain import build_rom
from tools.sprite_png import load_sheets,editor_frame

def project_case(root,mixed=False):
    path=single(root)
    sp=next((path/'project/scenes').rglob('scene.gbsres'))
    first=json.loads(sp.read_text(encoding='utf-8'));second_folder=sp.parent.with_name('second_platform')
    shutil.copytree(sp.parent,second_folder)
    second=json.loads((second_folder/'scene.gbsres').read_text(encoding='utf-8'))
    second.update(id='m3-second-platform',name='Second Platform',symbol='scene_m3_second_platform')
    ap=next(second_folder.rglob('actors/*.gbsres'));a=json.loads(ap.read_text(encoding='utf-8'));old=a['id']
    a.update(id='m3-second-turnip',symbol='actor_m3_second_turnip');write_json(ap,a)
    second['script']=json.loads(json.dumps(second['script']).replace(old,a['id']))
    if mixed:
        second['type']='TOPDOWN';second['script']=[]
    for s,target,x in ((first,second['id'],12),(second,first['id'],4)):
        # Each load reattaches its callbacks and START event, then releases INIT.
        e=ev('change-'+s['id'],'EVENT_SET_INPUT_SCRIPT',input=['start'],override=True)
        e['children']={'true':[switch('go-'+s['id'],target,(x,10,'right'),'0')]}
        s['script'].append(e)
    write_json(sp,first);write_json(second_folder/'scene.gbsres',second)
    return path

def run(out,toolchain,emulator,bios,clang,frames=900,witness=False):
    if out.exists():raise ValueError('new output required')
    out.mkdir(parents=True);path=project_case(out);m=export_project(path,out/'export')
    if witness:
        p=out/'export/src/game.c';s=p.read_text(encoding='utf-8');old='    ngc_platform_init();';assert s.count(old)==1
        temp=p.with_suffix('.tmp');temp.write_text(s.replace(old,'    /* witness: state init omitted */'),encoding='utf-8');temp.replace(p)
        m['sha256']['src/game.c']=hashlib.sha256(p.read_bytes()).hexdigest();write_json(out/'export/export.json',m)
    b=build_rom(out/'export',toolchain);symbols=b['link_audit']['public_symbols'];addr=lambda n:symbols['_'+n]['address']
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    sys.path.insert(0,str(Path(emulator).resolve()));from core import native
    machine=native.NativeMachine(rom,bios=bios_data);machine.set_timing_silicon(word_wait=10,bios_wait=8)
    machine.reset(bios_handoff=True);power_on_ram(machine,'noise');machine.write(0xA000,bytes(8192))
    tick=verified_tick(rom,b);platform=addr('ngc_platform_update');init=addr('ngc_platform_init')
    machine.set_breakpoints([tick,platform,init]);rows=[];pics=[];scrolls=[];ops=[];previous=None;dropped=[];observed_scene=0
    def read(n,size=2,signed=False):return int.from_bytes(machine.read(addr(n),size),'little',signed=signed)
    for k in range(frames+1):
        while True:
            stop,_=machine.run(3_000_000,record=False)
            if stop.stop_status!=40:raise ValueError('native breakpoint not reached')
            if stop.stop_pc==tick:break
            scene=read('ngc_scene',1)
            if stop.stop_pc==init:
                # Independently expect the SWITCH_SCENE's explicit position.
                ops.append({'op':'load','scene':scene,'tick':k,'x':(12 if scene==1 else 4)*256,'y':10*256,'dir':1})
                observed_scene=scene
            else:
                # The negative witness omits native init; the reference still
                # loads the scene prescribed by SWITCH_SCENE before physics.
                if scene!=observed_scene:
                    ops.append({'op':'load','scene':scene,'tick':k,'x':(12 if scene==1 else 4)*256,'y':10*256,'dir':1})
                    observed_scene=scene
                ops.append({'op':'step','scene':scene,'tick':k,'joy':read('ngc_joy',1),'pressed':read('ngc_joy_pressed',1)})
        rows.append({'scene':read('ngc_scene',1),'x':read('act_x'),'y':read('act_y'),
                     'plat_state':read('plat_state',1),'vx':read('plat_vel_x',2,True),'vy':read('plat_vel_y',2,True),
                     'grounded':read('plat_grounded',1),'vars':[int.from_bytes(machine.read(addr('script_memory')+2*i,2),'little') for i in range(21)],'ops':len(ops)})
        pics.append(array('H',(v&4095 for v in machine.framebuffer())));scrolls.append([int.from_bytes(machine.read(addr('ngc_scroll')+2*i,2),'little') for i in range(2)])
        if k>3 and stop.frame_count-previous!=1:dropped.append({'tick':k,'frames':stop.frame_count-previous})
        previous=stop.frame_count
        held=0
        for start,end,mask in oracle.SCHEDULE:
            if start<=k<end:held|=mask
        if k in (200,400,600,800):held|=64  # Option tap -> GB START on release
        machine.write(0xB0,bytes([held]))
    project=load_project(path)
    # Reference needs the scene's raw tables, not the linked symbol spelling.
    shadow=out/'reference-export/src';shadow.mkdir(parents=True)
    linked=(out/'export/src/scene_actors.c').read_text(encoding='utf-8')
    (shadow/'scene_actors.c').write_text(linked.replace('s0_',''),encoding='utf-8')
    c,raw=oracle.reference_source(shadow.parent,project)
    # Original platform_init is called at the original load boundary. Preserve
    # engine fields; scroll_reset resets time, then the scene script sets position/direction.
    c+='\nvoid load(unsigned x,unsigned y,unsigned d){PLAYER.pos.x=x;PLAYER.pos.y=y;PLAYER.dir=d;game_time=0;platform_init();}\nvoid attach_all(void){unsigned i;for(i=0;i<21;i++){plat_events[i].script_bank=0;plat_events[i].script_addr=(const UBYTE *)(unsigned long)(300+i);}}\n'
    (out/'reference.c').write_text(c,encoding='utf-8')
    proc=subprocess.run([str(clang),'--target=wasm32','-nostdlib','-ffreestanding','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',str(out/'reference.c'),'-o',str(out/'reference.wasm')],capture_output=True,text=True)
    if proc.returncode:raise ValueError(proc.stderr)
    initargs=[4*256,10*256,*oracle.c_values(raw,'act_bounds')[:4],oracle.c_values(raw,'act_init_dir')[0]]
    # Boot's state init arrives before the first tick: reference reset covers it.
    operations=ops[1:] if ops and ops[0]['op']=='load' else ops
    js="""const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const c=JSON.parse(fs.readFileSync(process.argv[3]));e.reset(...c.init);e.attach_all();const rows=[Array.from({length:12},(_,i)=>e.get(i))],events=[];
for(const o of c.ops){const before=e.log_count();if(o.op==='load'){e.load(o.x,o.y,o.dir);e.attach_all();}else e.step(o.joy,o.pressed);
for(let i=before;i<e.log_count();i++)events.push([o.tick,e.log_get(i,1)-300]);rows.push(Array.from({length:12},(_,i)=>e.get(i)));}
process.stdout.write(JSON.stringify({rows,events}));"""
    (out/'run.js').write_text(js,encoding='utf-8');write_json(out/'ops.json',{'init':initargs,'ops':operations})
    proc=subprocess.run(['node',str(out/'run.js'),str(out/'reference.wasm'),str(out/'ops.json')],capture_output=True,text=True)
    if proc.returncode:raise ValueError(proc.stderr)
    reference=json.loads(proc.stdout);refs=reference['rows'];events=reference['events']
    offset=1 if operations is not ops else 0;mismatches=[]
    for k,row in enumerate(rows):
        ref=refs[row['ops']-offset]
        actual=[row['x'],row['y'],row['plat_state'],row['vx'],row['vy'],row['grounded']]
        expected=[ref[0],ref[1],ref[3],ref[4],ref[5],ref[6]]
        if actual!=expected:mismatches.append({'tick':k,'scene':row['scene'],'actual':actual,'expected':expected})
    transitions=[k for k in range(1,len(rows)) if rows[k]['scene']!=rows[k-1]['scene']]
    # Check every scene visit independently: its original state machine's
    # launches must all be visible before the next input-triggered change.
    # Two ticks at each edge are scheduler latency; final counters are global.
    callback_mismatches=[];segments=[]
    for start,end in zip([0,*transitions],[*transitions,frames]):
        lo=min(start+8,end);hi=max(lo,end-3)
        counts=[0]*21
        for t,slot in events:
            if lo<t<=hi:counts[slot]+=1
        actual=[(a-b)&65535 for a,b in zip(rows[hi+1]['vars'],rows[lo+1]['vars'])]
        if actual!=counts:callback_mismatches.append({'range':[lo,hi],'actual':actual,'expected':counts})
        segments.append([lo,hi])
    # All scenery pixels and the navet's reference-driven pose; animated player
    # is excluded as in the single-scene proof. Exclude load/scheduler edges.
    scene=next(s for s in project.scenes if s['id']==project.settings['startSceneId'])
    engine=build_engine_scene(project,scene);bg=background_source(project,scene);vh,top=view_of(bg)
    sheets=load_sheets(path);raw,image=sheets['turnip'];player=sheets[engine.sheets[0].name][0];pal=sprite_palettes(project,scene)
    sprite=editor_frame(raw,image,raw['states'][0]['animations'][0]['frames'][0],raw.get('spriteMode') or project.settings['spriteMode'],False)
    pixel_mismatches=[];checked=0
    loads=[o['tick'] for o in operations if o['op']=='load']
    for k in range(3,len(rows)):
        if not any(lo+3<=k<=hi for lo,hi in segments):continue
        row=rows[k-1];sx,sy=scrolls[k];expected=[0]*(160*152);checked+=1
        for y in range(vh):expected[(y+top)*160:(y+top+1)*160]=bg.pixels[(sy+y)*bg.width+sx:(sy+y)*bg.width+sx+160]
        last_load=max([0,*[t for t in loads if t<k]])
        pose=104
        for t,slot in events:
            if last_load<t<=k-2 and slot in (0,2,4):pose={0:64,2:104,4:32}[slot]
        ox,oy=editor_frame_origin(raw,112-sx,pose-sy);oy+=top
        for (x,y),(v,pal_index) in sprite.items():
            x+=ox;y+=oy
            if 0<=x<160 and top<=y<top+vh:expected[y*160+x]=pal[pal_index][v-1]
        px,py=editor_frame_origin(player,(row['x']>>5)-sx,(row['y']>>5)-sy);py+=top
        n=sum(a!=e for j,(a,e) in enumerate(zip(pics[k],expected)) if not(px-2<=j%160<px+player['canvasWidth']+2 and py-2<=j//160<py+player['canvasHeight']+2))
        if n:pixel_mismatches.append({'tick':k,'pixels':n})
    result={'ticks':frames,'transitions':transitions,'scenes_seen':sorted(set(r['scene'] for r in rows)),
            'physics_mismatch_ticks':len(mismatches),'physics_first':mismatches[:3],
            'callback_mismatch_segments':len(callback_mismatches),'callback_first':callback_mismatches[:3],
            'state_events':len(events),'pixel_checked_frames':checked,'pixel_mismatch_frames':len(pixel_mismatches),'pixel_first':pixel_mismatches[:3],
            'transition_frames':dropped,'dropped_gameplay_frames':[v for v in dropped if v['tick'] not in transitions],
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'bios_sha256':hashlib.sha256(bios_data).hexdigest(),'silicon_timing':{'word_wait':10,'bios_wait':8}}
    write_json(out/'comparison.json',result);write_json(out/'rows.json',rows)
    for scene in (0,1):
        indices=[k for k in range(5,len(rows)) if rows[k]['scene']==scene]
        if indices:write_frame(out/f'scene-{scene}.ppm',pics[indices[len(indices)//2]])
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('out','toolchain','emulator','bios','clang'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--frames',type=int,default=900)
    a=p.parse_args();result=run(a.out,a.toolchain,a.emulator,a.bios,a.clang,frames=a.frames)
    print(json.dumps(result,indent=2))
    return int(any(result[k] for k in ('physics_mismatch_ticks','callback_mismatch_segments','pixel_mismatch_frames','dropped_gameplay_frames')) or len(result['transitions'])!=4)

if __name__=='__main__':raise SystemExit(main())
