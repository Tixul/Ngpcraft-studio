"""Distinct Platformer/top-down scenes vs original GBVM C and PNG backgrounds."""
import argparse,hashlib,json,re,shutil,subprocess,sys
from array import array
from pathlib import Path
from tools.demo_scene_switch import derive_scenes,switch
from tools.demo_actor_moves import ev,num
from tools.demo_gbs_scene import write_json
from tools.probe_rom import verified_tick,power_on_ram,write_frame
from tools import platform_oracle as oracle
from tools.reference import REFERENCE
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_assets import background_source
from ngpcraft.background_stream import view_of
from ngpcraft.sprites import editor_frame_origin
from tools.sprite_png import load_sheets

FOLDERS=('path_to_sample_town','sample_town')
ARRIVALS=((4,10,'right'),(13,20,'down'))
SCHEDULE=oracle.SCHEDULE+[[202,203,64],[402,403,64],[602,603,64],[802,803,64]]

def project_case(root,start=0,grid=8):
    path=root/'project';keep,_=derive_scenes(REFERENCE/'appData/templates/gbs2',path,FOLDERS,FOLDERS[start],ARRIVALS[start][:2])
    for k,folder in enumerate(FOLDERS):
        p=keep[folder]/'scene.gbsres';s=json.loads(p.read_text(encoding='utf-8'))
        s.update(id='m3-mixed-'+('platform' if k==0 else 'topdown'),symbol='scene_m3_mixed_'+str(k),type='PLATFORM' if k==0 else 'TOPDOWN',autoFadeSpeed=None)
        for sub in ('actors','triggers'):
            if (p.parent/sub).exists():shutil.rmtree(p.parent/sub)
        script=[ev('visit-'+str(k),'EVENT_INC_VALUE',variable=str(k))]
        if k==0:
            e=ev('on-jump','EVENT_SET_PLATFORMER_CALLBACK_SCRIPT',event='jumpStart')
            e['children']={'script':[ev('jump-count','EVENT_INC_VALUE',variable='2')]};script.append(e)
        e=ev('change-'+str(k),'EVENT_SET_INPUT_SCRIPT',input=['start'],override=True)
        e['children']={'true':[switch('go-'+str(k),'m3-mixed-'+('topdown' if k==0 else 'platform'),ARRIVALS[1-k],'0')]};script.append(e)
        s['script']=script;write_json(p,s)
    p=path/'project/settings.gbsres';s=json.loads(p.read_text(encoding='utf-8'))
    s.update(startSceneId='m3-mixed-'+('platform' if start==0 else 'topdown'),startDirection=ARRIVALS[start][2]);write_json(p,s)
    p=path/'project/engine_field_values.gbsres';s=json.loads(p.read_text(encoding='utf-8'))
    s['engineFieldValues']=[f for f in s['engineFieldValues'] if f['id']!='topdown_grid']+[{'id':'topdown_grid','value':grid}];write_json(p,s)
    return path

def original_reference(out,export,project,engines,ops,start,clang):
    # The existing Platformer harness consumes scene tables. Only strip the
    # linked symbol spelling; reference function bodies come from published C.
    shadow=out/'reference-export/src';shadow.mkdir(parents=True)
    raw=(export/'src/scene_actors.c').read_text(encoding='utf-8').replace('s0_','')
    (shadow/'scene_actors.c').write_text(raw,encoding='utf-8')
    c,_=oracle.reference_source(shadow.parent,project)
    maximum=max(len(e.collisions) for e in engines)
    c=c.replace('static UBYTE collision_map['+str(len(engines[0].collisions))+'];','static UBYTE collision_map['+str(maximum)+'];')
    c=c.replace('UBYTE animation, frame,','UBYTE move_speed; UBYTE animation, frame,')
    core=REFERENCE/'appData/engine/gbvm'
    headers='\n'.join((core/'include'/h).read_text(encoding='utf-8') for h in ('math.h','actor.h','input.h','camera.h'))
    keys=('SUBPX_SNAP_TILE','SUBPX_SNAP_TILE16','ON_8PX_GRID','ON_16PX_GRID','INPUT_RECENT_LEFT','INPUT_RECENT_RIGHT','INPUT_RECENT_UP','INPUT_RECENT_DOWN','FLIPPED_DIR','MOD_4','UWORD_MAX','CAMERA_LOCK_X_MIN_FLAG','CAMERA_LOCK_X_MAX_FLAG','CAMERA_LOCK_Y_MIN_FLAG','CAMERA_LOCK_Y_MAX_FLAG')
    macros=[]
    for key in keys:
        found=re.findall(r'^#define '+key+r'\b.*$',headers,re.M);assert len(found)==1,(key,found);macros+=found
    math=(core/'include/math.h').read_text(encoding='utf-8')
    translate=re.search(r'^inline void point_translate_dir\(.*?^\}',math,re.M|re.S).group().replace('inline void','static void',1)
    topdown=(core/'src/states/topdown.c').read_text(encoding='utf-8')
    topdown=re.sub(r'^#(?:include|pragma).*$', '',topdown,flags=re.M)
    camera=(core/'src/core/camera.c').read_text(encoding='utf-8')
    camera_update=re.search(r'^void camera_update\(.*?^\}',camera,re.M|re.S).group()
    c+='\n'+'\n'.join(macros)+'''
UBYTE recent_joy,player_moving;
UWORD camera_clamp_x,camera_clamp_y;
#define CAMERA_FIXED_OFFSET_X PX_TO_SUBPX(8)
#define CAMERA_FIXED_OFFSET_Y PX_TO_SUBPX(8)
actor_t *actor_in_front_of_player(UBYTE grid,UBYTE scripted){(void)grid;(void)scripted;return NULL;}
'''+translate+'\n'+topdown+'\n'+camera_update+'\n'
    for k,e in enumerate(engines):c+='static const UBYTE mixed_map_'+str(k)+'[]={'+','.join(map(str,e.collisions))+'};\n'
    c+='''void mixed_load(unsigned scene,unsigned x,unsigned y,unsigned direction){
    unsigned i;
    PLAYER.pos.x=x;PLAYER.pos.y=y;PLAYER.dir=direction;game_time=0;
    camera_x=camera_y=camera_clamp_x=camera_clamp_y=0;camera_settings=3;
    camera_offset_x=camera_offset_y=camera_deadzone_x=camera_deadzone_y=0;
'''
    for k,e in enumerate(engines):
        b=e.bounds[0];c+=f'''    if(scene=={k}){{image_tile_width={e.background.width//8};image_tile_height={e.background.height//8};
        for(i=0;i<{len(e.collisions)};i++)collision_map[i]=mixed_map_{k}[i];
        PLAYER.bounds.left={b[0]};PLAYER.bounds.right={b[1]};PLAYER.bounds.top={b[2]};PLAYER.bounds.bottom={b[3]};
        PLAYER.move_speed={e.actors[0]['move_speed']};
    }}
'''
    # Existing V3 binding: platform_init writes camera in pixels whereas the
    # shared camera.c uses subpixels. Convert once at this machine boundary,
    # as declared by extract_platform; original physics bodies stay intact.
    from ngpcraft.gbvm.emit import engine_field
    c+=f'''    if(scene==0){{platform_init();camera_x<<=5;camera_y<<=5;plat_events[JUMP_INIT].script_bank=0;plat_events[JUMP_INIT].script_addr=(const UBYTE *)304;}}
    else{{topdown_grid={engine_field(project,'topdown_grid')};topdown_init();}}
'''+'''
}
void mixed_step(unsigned scene,unsigned j,unsigned p,unsigned recent){joy=j;joy_pressed=p;recent_joy=recent;
    if(scene==0)platform_update();else topdown_update();}
void mixed_clock(void){game_time++;}
int mixed_get(unsigned k){if(k<12)return get(k);if(k==12)return camera_x;if(k==13)return camera_y;return 0;}
'''
    (out/'reference.c').write_text(c,encoding='utf-8')
    proc=subprocess.run([str(clang),'--target=wasm32','-nostdlib','-ffreestanding','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',str(out/'reference.c'),'-o',str(out/'reference.wasm')],capture_output=True,text=True)
    if proc.returncode:raise ValueError(proc.stderr[-4000:])
    js='''const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const c=JSON.parse(fs.readFileSync(process.argv[3]));e.reset(...c.reset);const rows=[],events=[];
for(const o of c.ops){const before=e.log_count();if(o.op==='load')e.mixed_load(o.scene,o.x,o.y,o.dir);
else if(o.op==='step')e.mixed_step(o.scene,o.joy,o.pressed,o.recent);else if(o.op==='camera')e.camera_update();else e.mixed_clock();
for(let i=before;i<e.log_count();i++)events.push([o.tick,e.log_get(i,1)]);rows.push(Array.from({length:14},(_,i)=>e.mixed_get(i)));}
process.stdout.write(JSON.stringify({rows,events}));'''
    write_json(out/'ops.json',{'reset':[ARRIVALS[start][0]*256,ARRIVALS[start][1]*256,*engines[start].bounds[0],1 if start==0 else 0],'ops':ops})
    (out/'run.js').write_text(js,encoding='utf-8')
    proc=subprocess.run(['node',str(out/'run.js'),str(out/'reference.wasm'),str(out/'ops.json')],capture_output=True,text=True)
    if proc.returncode:raise ValueError(proc.stderr[-3000:])
    return json.loads(proc.stdout)

def run(out,toolchain,emulator,bios,clang,frames=900,start=0,witness=None,grid=8):
    from ngpcraft.toolchain import build_rom
    if out.exists():raise ValueError('new output required')
    out.mkdir(parents=True);path=project_case(out,start,grid);m=export_project(path,out/'export')
    if witness:
        rel='src/game.c' if witness=='topdown_init' else 'src/scene_stream.c'
        p=out/'export'/rel;s=p.read_text(encoding='utf-8')
        if witness=='topdown_init':old='    ngc_topdown_init();';new='    /* witness: topdown init omitted */'
        elif witness=='decor':old='case 1u: s1_ngc_stream_load(x, y); break;';new='case 1u: s0_ngc_stream_load(x, y); break;'
        else:raise ValueError('unknown witness')
        assert s.count(old)==1,(old,s.count(old));temp=p.with_suffix('.tmp');temp.write_text(s.replace(old,new),encoding='utf-8');temp.replace(p)
        m['sha256'][rel]=hashlib.sha256(p.read_bytes()).hexdigest();write_json(out/'export/export.json',m)
    b=build_rom(out/'export',toolchain);symbols=b['link_audit']['public_symbols'];addr=lambda n:symbols['_'+n]['address']
    project=load_project(path);scenes=sorted(project.scenes,key=lambda s:s['id']);engines=[build_engine_scene(project,s) for s in scenes]
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    sys.path.insert(0,str(Path(emulator).resolve()));from core import native
    machine=native.NativeMachine(rom,bios=bios_data);machine.set_timing_silicon(word_wait=10,bios_wait=8);machine.reset(bios_handoff=True)
    power_on_ram(machine,'noise');machine.write(0xA000,bytes(8192))
    tick=verified_tick(rom,b);camera=addr('ngc_camera_update')
    init={addr('ngc_platform_init'):0,addr('ngc_topdown_init'):1};updates={addr('ngc_platform_update'):0,addr('ngc_topdown_update'):1}
    machine.set_breakpoints([tick,camera,*init,*updates]);rows=[];pictures=[];scrolls=[];ops=[];dropped=[];previous=None;loaded=None
    def read(n,size=2,signed=False):return int.from_bytes(machine.read(addr(n),size),'little',signed=signed)
    for k in range(frames+1):
        while True:
            stop,_=machine.run(4_000_000,record=False)
            if stop.stop_status!=40:raise ValueError('native breakpoint not reached')
            scene=read('ngc_scene',1)
            if stop.stop_pc==tick:break
            # Active init boundary; inactive dispatchers do not run a state.
            if stop.stop_pc in init and init[stop.stop_pc]==scene:
                x,y,d=ARRIVALS[scene];ops.append({'op':'load','scene':scene,'tick':k,'x':x*256,'y':y*256,'dir':1 if d=='right' else 0});loaded=scene
            elif stop.stop_pc in updates and updates[stop.stop_pc]==scene:
                if loaded!=scene:
                    x,y,d=ARRIVALS[scene];ops.append({'op':'load','scene':scene,'tick':k,'x':x*256,'y':y*256,'dir':1 if d=='right' else 0});loaded=scene
                ops.append({'op':'step','scene':scene,'tick':k,'joy':read('ngc_joy',1),'pressed':read('ngc_joy_pressed',1),'recent':read('ngc_recent_joy',1)})
            elif stop.stop_pc==camera:ops.append({'op':'camera','scene':scene,'tick':k})
        rows.append({'scene':scene,'x':read('act_x'),'y':read('act_y'),'camera':[read('ngc_camera_x'),read('ngc_camera_y')],
                     'state':read('plat_state',1),'vx':read('plat_vel_x',2,True),'vy':read('plat_vel_y',2,True),
                     'vars':[int.from_bytes(machine.read(addr('script_memory')+2*i,2),'little') for i in range(3)],'ops':len(ops)})
        pictures.append(array('H',(v&4095 for v in machine.framebuffer())));scrolls.append([int.from_bytes(machine.read(addr('ngc_scroll')+2*i,2),'little') for i in range(2)])
        if k>3 and stop.frame_count-previous!=1:dropped.append({'tick':k,'frames':stop.frame_count-previous})
        previous=stop.frame_count
        # core.c advances time on every game tick, including locked INIT.
        if k:ops.append({'op':'clock','scene':scene,'tick':k})
        held=0
        for lo,hi,mask in SCHEDULE:
            if lo<=k<hi:held|=mask
        machine.write(0xB0,bytes([held]))
    reference=original_reference(out,out/'export',project,engines,ops,start,clang);refs=reference['rows'];events=reference['events']
    physics=[];camera_errors=[];expected_rows=[]
    for k,row in enumerate(rows):
        ref=refs[row['ops']-1];expected_rows.append(ref)
        actual=[row['x'],row['y']];expected=ref[:2]
        if row['scene']==0:actual += [row['state'],row['vx'],row['vy']];expected += [ref[3],ref[4],ref[5]]
        if actual!=expected:physics.append({'tick':k,'scene':row['scene'],'actual':actual,'expected':expected})
        if row['camera']!=ref[12:14]:camera_errors.append({'tick':k,'actual':row['camera'],'expected':ref[12:14]})
    transitions=[k for k in range(1,len(rows)) if rows[k]['scene']!=rows[k-1]['scene']]
    segments=[(s+8,e-3) for s,e in zip([0,*transitions],[*transitions,frames])]
    jump_errors=[]
    for lo,hi in segments:
        if hi<=lo:continue
        expected=sum(tag==304 and lo<t<=hi for t,tag in events);actual=(rows[hi+1]['vars'][2]-rows[lo+1]['vars'][2])&65535
        if actual!=expected:jump_errors.append({'range':[lo,hi],'actual':actual,'expected':expected})
    backgrounds=[background_source(project,s) for s in scenes];sheets=load_sheets(path)
    pixels=[];scroll_errors=[];checked=0
    for k in range(3,len(rows)):
        if not any(lo+3<=k<=hi for lo,hi in segments):continue
        scene=rows[k]['scene'];bg=backgrounds[scene];vh,top=view_of(bg);ref=expected_rows[k-1]
        sx=max(0,min((ref[12]>>5)-80,bg.width-160));sy=max(0,min((ref[13]>>5)-(vh//2),bg.height-vh))
        if [sx,sy]!=scrolls[k]:scroll_errors.append(k)
        expected=[0]*(160*152);checked+=1
        for y in range(vh):expected[(y+top)*160:(y+top+1)*160]=bg.pixels[(sy+y)*bg.width+sx:(sy+y)*bg.width+sx+160]
        raw=sheets[engines[scene].sheets[0].name][0];px,py=editor_frame_origin(raw,(ref[0]>>5)-sx,(ref[1]>>5)-sy);py+=top
        n=sum(a!=e for j,(a,e) in enumerate(zip(pictures[k],expected)) if not(px-2<=j%160<px+raw['canvasWidth']+2 and py-2<=j//160<py+raw['canvasHeight']+2))
        if n:pixels.append({'tick':k,'scene':scene,'pixels':n})
    result={'ticks':frames,'start_scene':start,'topdown_grid':grid,'transitions':transitions,'scenes_seen':sorted(set(r['scene'] for r in rows)),
            'physics_mismatch_ticks':len(physics),'physics_first':physics[:3],'camera_mismatch_ticks':len(camera_errors),'camera_first':camera_errors[:3],
            'jump_callback_mismatch_segments':len(jump_errors),'jump_first':jump_errors[:3],'jump_events':sum(t==304 for _,t in events),
            'pixel_checked_frames':checked,'pixel_mismatch_frames':len(pixels),'pixel_first':pixels[:3],'scroll_mismatch_frames':scroll_errors[:10],
            'transition_frames':[d for d in dropped if d['tick'] in transitions],'dropped_gameplay_frames':[d for d in dropped if d['tick'] not in transitions],
            'views':[list(view_of(b)) for b in backgrounds],'scene_visits':rows[-1]['vars'][:2],
            'reference_units':{n:hashlib.sha256((REFERENCE/'appData/engine/gbvm/src'/n).read_bytes()).hexdigest() for n in ('states/platform.c','states/topdown.c','core/collision.c','core/camera.c')},
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'bios_sha256':hashlib.sha256(bios_data).hexdigest(),'silicon_timing':{'word_wait':10,'bios_wait':8}}
    write_json(out/'comparison.json',result);write_json(out/'rows.json',rows);write_json(out/'reference-events.json',events)
    for scene in (0,1):
        candidates=[k for k in range(10,len(rows)) if rows[k]['scene']==scene and any(lo+3<=k<=hi for lo,hi in segments)]
        if candidates:write_frame(out/f'scene-{scene}.ppm',pictures[candidates[len(candidates)//2]])
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('out','toolchain','emulator','bios','clang'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--frames',type=int,default=900);p.add_argument('--start',type=int,choices=(0,1),default=0);p.add_argument('--grid',type=int,choices=(8,16),default=8)
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios,a.clang,a.frames,a.start,grid=a.grid);print(json.dumps(r,indent=2))
    return int(any(r[k] for k in ('physics_mismatch_ticks','camera_mismatch_ticks','jump_callback_mismatch_segments','pixel_mismatch_frames','scroll_mismatch_frames','dropped_gameplay_frames')) or len(r['transitions'])!=4)

if __name__=='__main__':raise SystemExit(main())
