"""M3 camera shake: native Platformer draw offsets against original vm_camera.c."""
from array import array
import argparse,hashlib,json,re,subprocess,sys,struct,zlib
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_assets import background_source,sprite_palettes
from ngpcraft.background_stream import view_of
from ngpcraft.sprites import editor_frame_origin
from ngpcraft.toolchain import build_rom
from tools.platform_oracle import project_case as platform_case,rom_trace,reference_run,compare
from tools.demo_actor_moves import ev,num
from tools.demo_gbs_scene import write_json
from tools.demo_backgrounds import write_new
from tools.probe_rom import verified_tick,power_on_ram,write_frame
from tools.reference import REFERENCE
from tools.extract_gbvm_runtime import cut
from tools.sprite_png import load_sheets,editor_frame

CASES=[(30,1,5),(30,2,5),(30,3,5),(1,3,1),(15,3,5)]

def project_case(root,rate=60,small=False):
    path=platform_case(root,platform=('platform',112,112,104))
    actor_path=next((path/'project/scenes').rglob('actors/*.gbsres'))
    actor=json.loads(actor_path.read_text())
    actor.update(spriteSheetId='f88eea80-c5b4-4e10-b49f-7a8aa8bd477e',name='Camera shake turnip',
                 collisionExtraFlags=[],collisionGroup='',animSpeed=255)
    script=[ev('pre-wait','EVENT_WAIT',units='frames',frames=num(45))]
    for phase,(duration,axes,magnitude) in enumerate(CASES,1):
        args={'units':'frames','frames':duration,'shakeDirection':{1:'horizontal',2:'vertical',3:'diagonal'}[axes],
              'magnitude':num(magnitude)}
        if phase==5:
            args.update(units='time',time=0.25,magnitude={'type':'add','valueA':num(2),'valueB':num(3)})
        script += [ev(f'phase-{phase}','EVENT_SET_VALUE',variable='0',value=num(phase)),
                   ev(f'shake-{phase}','EVENT_CAMERA_SHAKE',**args),
                   ev(f'end-{phase}','EVENT_SET_VALUE',variable='0',value=num(0)),
                   ev(f'pause-{phase}','EVENT_WAIT',units='frames',frames=num(45))]
    script += [ev('zero-noop','EVENT_CAMERA_SHAKE',units='frames',frames=0,magnitude=num(5))]
    actor['updateScript']=script;write_json(actor_path,actor)
    if small:
        from ngpcraft.png import read_png
        from ngpcraft.scene_assets import decompress_tile_colors
        project=load_project(path);scene=project.scenes[0]
        rel,bg=next((rel,bg) for rel,bg in project.by_type['background'] if bg['id']==scene['backgroundId'])
        png=path/Path(rel).with_suffix('');width,height,rgba=read_png(png);tw=width//8
        def cropped(text):
            cells=decompress_tile_colors(text,'small proof')
            return ''.join(f'{cells[y*tw+x] if y*tw+x<len(cells) else 0:02x}!' for y in range(height//8) for x in range(32))
        bg['tileColors']=cropped(bg.get('tileColors') or '');bg['width']=32;write_json(path/rel,bg)
        scene_rel=next(rel for rel,res in project.by_type['scene'] if res['id']==scene['id'])
        scene_raw=json.loads((path/scene_rel).read_text());scene_raw.update(collisions=cropped(scene['collisions']),width=32)
        write_json(path/scene_rel,scene_raw)
        def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
        raw=b''.join(b'\0'+bytes(v for pixel in rgba[y*width:y*width+256] for v in pixel) for y in range(height))
        data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',256,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
        tmp=png.with_suffix('.shake.tmp');write_new(tmp,data);tmp.replace(png)
    if rate==30:write_json(path/'ngpcraft.json',{'schema':'ngpcraft-project/1','ngpc':{'frame_rate':30}})
    return path

def reference_source():
    src=(REFERENCE/'appData/engine/gbvm/src/core/vm_camera.c').read_text()
    body=cut(src,'camera_shake_frames').replace('((UWORD)sys_time - *((SCRIPT_CTX *)THIS)->stack_ptr)',
                                              '((UWORD)((UWORD)sys_time - *((SCRIPT_CTX *)THIS)->stack_ptr))')
    return '''typedef unsigned char UBYTE;typedef unsigned short UWORD;
typedef struct { UWORD *stack_ptr;UBYTE waitable; } SCRIPT_CTX;
#define CAMERA_SHAKE_X 1
#define CAMERA_SHAKE_Y 2
#define TRUE 1
#define FALSE 0
static UWORD sys_time,state,start_word;
static signed char scroll_offset_x,scroll_offset_y;
/* Independent bytewise emulation of GBDK sm83/rand.s ADD/ADC, high byte return. */
unsigned rand(void){unsigned lo=state&255,hi=state>>8,l,h,t;
l=(lo<<4)&255;h=((hi<<4)|(lo>>4))&255;t=l+lo;l=t&255;h=(h+hi+(t>>8))&255;
t=l+0x93;l=t&255;h=(h+0x5c+(t>>8))&255;state=(h<<8)|l;return h;}
'''+body+'''
unsigned step(unsigned time,unsigned start,unsigned duration,unsigned axes,unsigned magnitude){
SCRIPT_CTX ctx;UWORD args[3];UBYTE done;
sys_time=time;ctx.stack_ptr=&start_word;ctx.waitable=0;args[0]=duration;args[1]=axes;args[2]=magnitude;
done=camera_shake_frames(&ctx,start,args);
return (unsigned char)scroll_offset_x|((unsigned)(unsigned char)scroll_offset_y<<8)|((unsigned)done<<16);}
unsigned random_word(unsigned seed){state=seed;rand();return state;}
void reset(void){state=0;scroll_offset_x=scroll_offset_y=0;start_word=0;}
'''

def expected_offsets(out,rows,clang):
    write_new(out/'camera-reference.c',reference_source().encode())
    p=subprocess.run([clang,'--target=wasm32','-nostdlib','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',
                      str(out/'camera-reference.c'),'-o',str(out/'camera-reference.wasm')],capture_output=True,text=True)
    if p.returncode:raise ValueError(p.stderr)
    write_json(out/'camera-inputs.json',{'rows':rows,'cases':CASES})
    js='''const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const d=JSON.parse(fs.readFileSync(process.argv[3]));e.reset();let active=0,result=[],bad=[];
let xy=[0,0];for(let k=0;k<d.rows.length;k++){let r=d.rows[k];for(const logic of r.logic){let phase=logic.phase;
if(phase||active){let p=phase||active;let packed=e.step(logic.clock,active===0,...d.cases[p-1]);
xy=[packed&255,(packed>>8)&255].map(x=>x<128?x:x-256);let done=packed>>16;
if((phase===0)!==Boolean(done))bad.push(k);active=done?0:p;}}
result.push(xy.slice());}process.stdout.write(JSON.stringify({rows:result,protocol:bad}));'''
    write_new(out/'camera-run.js',js.encode())
    p=subprocess.run(['node',str(out/'camera-run.js'),str(out/'camera-reference.wasm'),str(out/'camera-inputs.json')],capture_output=True,text=True)
    if p.returncode:raise ValueError(p.stderr)
    return json.loads(p.stdout)

def run(out,toolchain,emulator,bios,clang,frames=540,rate=60,witness=False,small=False):
    if out.exists():raise ValueError('output must be new')
    out.mkdir(parents=True);path=project_case(out,rate,small);m=export_project(path,out/'export')
    if witness:
        f=out/'export/src/scene_stream.c';s=f.read_text();old='    x += (short)ngc_scroll_offset_x;';assert s.count(old)==1
        tmp=f.with_suffix('.tmp');write_new(tmp,s.replace(old,'    /* witness: ignore X draw offset */').encode());tmp.replace(f)
        m['sha256']['src/scene_stream.c']=hashlib.sha256(f.read_bytes()).hexdigest();write_json(out/'export/export.json',m)
    build=build_rom(out/'export',toolchain);symbols=build['link_audit']['public_symbols']
    def addr(n):return symbols['_'+n]['address']
    sys.path.insert(0,str(Path(emulator).resolve()));from core import native
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    machine=native.NativeMachine(rom,bios=bios_data);machine.set_timing_silicon(word_wait=10,bios_wait=8)
    machine.reset(bios_handoff=True);power_on_ram(machine,'noise');machine.write(0xA000,bytes(8192))
    tick=verified_tick(rom,build);camera=addr('ngc_camera_update')
    machine.set_breakpoints([tick])
    def word(a,signed=False):return int.from_bytes(machine.read(a,2),'little',signed=signed)
    rows,pictures=[],[];previous=None
    for k in range(frames+1):
        logic=[]
        while True:
            stop,_=machine.run(3_000_000,record=False)
            if stop.stop_status!=40:raise ValueError('tick not reached')
            if stop.stop_pc==tick:break
            logic.append({'phase':word(addr('script_memory')),'clock':word(addr('ngc_sys_time'))})
        machine.set_breakpoints([tick,camera])
        row={'phase':word(addr('script_memory')),'clock':word(addr('ngc_sys_time')),
             'offset':[int.from_bytes(machine.read(addr('ngc_scroll_offset_'+axis),1),'little',signed=True) for axis in ('x','y')],
             'scroll':[word(addr('ngc_scroll'),True),word(addr('ngc_scroll')+2,True)],
             'base':[word(addr('ngc_scroll_base_next')),word(addr('ngc_scroll_base_next')+2)],
             'actors':[[word(addr('act_x')+2*i),word(addr('act_y')+2*i)] for i in range(2)],
             'physical_frames':None if previous is None else stop.frame_count-previous,'logic':logic}
        rows.append(row);pictures.append(array('H',(v&4095 for v in machine.framebuffer())));previous=stop.frame_count
        machine.write(0xB0,bytes([0]))
    reference=expected_offsets(out,rows,clang)
    offsets=[k for k,(r,e) in enumerate(zip(rows,reference['rows'])) if r['offset']!=e]
    draw=[k for k in range(2,len(rows)) if rows[k]['scroll']!=[rows[k-1]['base'][i]+reference['rows'][k-1][i] for i in range(2)]]
    project=load_project(path);engine=build_engine_scene(project,project.scenes[0])
    bg=background_source(project,project.scenes[0]);view_h,top=view_of(bg);palettes=sprite_palettes(project,project.scenes[0])
    sheets=load_sheets(path);player_raw=sheets[engine.sheets[0].name][0]
    raw,image=sheets['turnip'];sprite=editor_frame(raw,image,raw['states'][0]['animations'][0]['frames'][0],
                                                          raw.get('spriteMode') or project.settings['spriteMode'],False)
    differences=[]
    for k in range(3,len(rows)):
        row=rows[k-1];sx,sy=[rows[k-1]['base'][i]+reference['rows'][k-1][i] for i in range(2)]
        expected=[0]*(160*152)
        for y in range(top,top+view_h):
            wy=sy+y-top
            for x in range(160):
                wx=sx+x;expected[y*160+x]=bg.pixels[wy*bg.width+wx] if 0<=wx<bg.width and 0<=wy<bg.height else engine.background.backdrop
        ox,oy=editor_frame_origin(raw,(row['actors'][1][0]>>5)-sx,(row['actors'][1][1]>>5)-sy);oy+=top
        for (x,y),(v,pal) in sprite.items():
            x+=ox;y+=oy
            if 0<=x<160 and top<=y<top+view_h:expected[y*160+x]=palettes[pal][v-1]
        # Player animation is covered by the platform proof; exclude its canvas here.
        px,py=editor_frame_origin(player_raw,(row['actors'][0][0]>>5)-sx,(row['actors'][0][1]>>5)-sy);py+=top
        n=sum(a!=e for j,(a,e) in enumerate(zip(pictures[k],expected))
              if not (px-2<=j%160<px+player_raw['canvasWidth']+2 and py-2<=j//160<py+player_raw['canvasHeight']+2))
        if n:differences.append({'tick':k,'pixels':n})
    physics_rows=rom_trace(out/'export',emulator,bios,frames,[])
    physical_steps=physics_rows if rate==60 else [physics_rows[0]]+[r for r in physics_rows[1:] for _ in range(2)]
    physics=reference_run(out,out/'export',project,physical_steps,clang)
    pd=compare(physics_rows,physics['rows'] if rate==60 else physics['rows'][1::2])
    result={'ticks':frames,'rate':rate,'offset_mismatch_ticks':offsets,'protocol_mismatch_ticks':reference['protocol'],
            'draw_mismatch_ticks':draw,'pixel_checked_frames':frames-2,'pixel_mismatch_frames':len(differences),'pixel_first':differences[:5],
            'physics_mismatch_ticks':len(pd),'dropped_frames':[k for k,r in enumerate(rows) if k>3 and r['physical_frames']!=60//rate],
            'phases_seen':sorted({r['phase'] for r in rows}),'rom_sha256':hashlib.sha256(rom).hexdigest(),
            'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'bios_sha256':hashlib.sha256(bios_data).hexdigest(),'silicon_timing':{'word_wait':10,'bios_wait':8},'features':m['features']}
    write_json(out/'comparison.json',result);write_json(out/'rows.json',rows)
    for phase in range(6):
        candidates=[k for k,r in enumerate(rows) if r['phase']==phase and k>5]
        if candidates:write_frame(out/f'phase-{phase}.ppm',pictures[candidates[len(candidates)//2]])
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('out','toolchain','emulator','bios'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--clang',required=True);p.add_argument('--frames',type=int,default=540);p.add_argument('--rate',type=int,default=60,choices=(60,30))
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios,a.clang,a.frames,a.rate);print(json.dumps(r,indent=2))
    return int(bool(r['offset_mismatch_ticks'] or r['protocol_mismatch_ticks'] or r['draw_mismatch_ticks'] or r['pixel_mismatch_frames'] or r['physics_mismatch_ticks'] or r['dropped_frames']))
if __name__=='__main__':raise SystemExit(main())
