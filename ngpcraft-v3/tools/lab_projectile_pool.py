"""Pool/update/actor-contact oracle; scene, VM and native ROM wiring pending."""
import argparse
from dataclasses import asdict
import hashlib,json,os,re,subprocess,sys,tempfile
from pathlib import Path
from ngpcraft.projectile_runtime import ProjectileDefinition as Def, projectile_source, projectile_collision_source, projectile_ram_bytes
from ngpcraft.toolchain import locate, _compiler_lock
from tools.lab_projectile_motion import reference_source as motion_reference
from tools.reference import REFERENCE,GBVM_COMMIT
from tools.probe_actor_layout import declaration
from tools.probe_gbvm_layout import measure_layout
from tools.extract_gbvm_runtime import cut
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json

GBVM=REFERENCE/'appData/engine/gbvm'
GROUPS=(1,2,4,8)
FRAMES=tuple(100+3*i for i in range(256))
# projectiles.c SPREAD_4, SPREAD_2, EVERY_FRAME (engine field PROJECTILES_COLLISION_SPREAD).
SPREAD_DEFINES={'SPREAD_4':3,'SPREAD_2':1,'EVERY_FRAME':0}


def definitions():
    return tuple(Def((-128,127,-128,127),animations,life,speed,offset,tick,loop,destroy,group,mask)
        for animations,life,speed,offset,tick,loop,destroy,group,mask in (
            (((0,2),(3,5),(6,8),(9,11)),60,32,0,3,True,True,8,2),
            (((12,14),)*4,1,0,0,0,False,True,4,1),
            (((15,15),)*4,0,64,255,15,True,True,2,15),
            (((16,19),)*4,240,16,256,3,False,False,8,3),
            (((20,24),)*4,255,255,512,1,True,False,2,15),
            (((25,26),)*4,15,0,0,255,True,True,4,0),
            (((253,255),)*4,30,1,1,0,True,True,8,4),
            (((255,255),)*4,30,0,0,0,False,False,2,1),
            (((27,31),)*4,2,128,8192,7,True,True,4,8)))


def original_function(source,name):
    # Return-type independent cutter, brace-balanced; no algorithm rewrite.
    match=re.search(r'^.*\b'+re.escape(name)+r'\([^\n]*\)(?: BANKED| NONBANKED)?\s*\{',source,re.M)
    if not match:raise ValueError('released function missing: '+name)
    end=match.end();depth=1
    while depth:
        depth += {'{':1,'}':-1}.get(source[end],0);end+=1
    return source[match.start():end].replace('inline ','static ')


def reference_source(capacity,spread='SPREAD_4'):
    source=(GBVM/'src/core/projectiles.c').read_text(encoding='utf-8')
    types=(GBVM/'include/gbs_types.h').read_text(encoding='utf-8')
    helpers=motion_reference();helpers=helpers[helpers.index('static const int8_t sine_wave'):helpers.index('void projectile_launch(')]
    actor=original_function((GBVM/'src/core/actor.c').read_text(encoding='utf-8'),'actor_overlapping_bb')
    # SDCC int is 16 bits; wasm int is 32. Explicit word conversions restore
    # unsigned wrap of actor-bound additions, without changing comparisons.
    for axis,edge in (('x','left'),('x','right'),('y','top'),('y','bottom')):
        old=f'(actor->pos.{axis} + actor->bounds.{edge})'
        assert actor.count(old)==1
        actor=actor.replace(old,'(UWORD)'+old)
    bb=original_function((GBVM/'include/collision.h').read_text(encoding='utf-8'),'bb_intersects')
    for which in ('a','b'):
        for axis,edge in (('x','left'),('x','right'),('y','top'),('y','bottom')):
            old=f'offset_{which}->{axis} + bb_{which}->{edge}'
            assert bb.count(old)==1
            bb=bb.replace(old,'(UWORD)('+old+')')
    prefix=f'''typedef unsigned char UBYTE;typedef unsigned char UINT8;typedef unsigned short UINT16;
typedef signed char INT8;typedef signed char int8_t;typedef short int16_t;
typedef unsigned char uint8_t;typedef unsigned short uint16_t;typedef unsigned short UWORD;
typedef unsigned char FASTUBYTE;typedef unsigned char bool;
typedef struct {{unsigned short x,y;}} upoint16_t;typedef struct {{short x,y;}} point16_t;
typedef struct {{short left,right,top,bottom;}} rect16_t;
typedef struct {{unsigned char start,end;}} animation_t;
typedef struct {{unsigned char bank;const void *ptr;}} far_ptr_t;
#define BANKED
#define NONBANKED
#define NULL ((void*)0)
#define TRUE 1
#define FALSE 0
#define MAX(a,b) ((a)>(b)?(a):(b))
#define CHK_FLAG(a,b) ((a)&(b))
#define ACTOR_FLAG_COLLISION 8
#define SCRIPT_TERMINATED 0x8000
#define COLLISION_GROUP_PLAYER 1
#define PROJECTILES_COLLISION_SPREAD {SPREAD_DEFINES[spread]}
#define EVERY_FRAME 0
#define CLIP_EXT 2u
#define DEVICE_SCREEN_WIDTH 20
#define DEVICE_SCREEN_HEIGHT 18
#define PX_TO_TILE(a) ((UBYTE)((a)>>3))
#define SUBPX_TO_TILE(a) ((UBYTE)((a)>>8))
#define SUBPX_TO_PX(a) ((a)>>5)
#define DIR_DOWN 0
#define DIR_RIGHT 1
#define DIR_UP 2
#define DIR_LEFT 3
#define ANGLE_UP 0
#define ANGLE_RIGHT 64
#define ANGLE_DOWN 128
#define ANGLE_LEFT 192
#define MAX_PROJECTILES {capacity}
#define MAX_PROJECTILE_DEFS {len(definitions())}
'''
    prefix+=declaration(types,'projectile_def_t')+'\n'+declaration(types,'projectile_t')+'''
typedef struct actor_t {upoint16_t pos;rect16_t bounds;UBYTE flags,collision_group;far_ptr_t script;
UWORD hscript_hit;struct actor_t *prev;} actor_t;
static actor_t actors[4];
#define PLAYER actors[0]
static UBYTE game_time;static short draw_scroll_x,draw_scroll_y;
static unsigned ref_count,ref_logs[16],ref_draw_count,ref_draws[MAX_PROJECTILES*3];
static UBYTE frame_identity[256];static const UBYTE *metasprites[256];
typedef struct {const UBYTE **metasprites;} spritesheet_t;
static spritesheet_t ref_sheet;
static UBYTE _current_bank,_save_bank;
static unsigned allocated_hardware_sprites;
#define SWITCH_ROM(bank) ((void)(bank))
void *memcpy(void *dst,const void *src,unsigned len) {unsigned char *d=dst;const unsigned char *s=src;
while(len--) *d++=*s++;return dst;}
void script_execute(UBYTE bank,const void *ptr,UWORD *handle,UBYTE argc,UWORD group) {
ref_logs[ref_count*2]=(unsigned)(((char*)handle-(char*)&actors[0].hscript_hit)/sizeof(actor_t));
ref_logs[ref_count*2+1]=group;ref_count++;*handle=1;(void)bank;(void)ptr;(void)argc;}
unsigned move_metasprite(const UBYTE *meta,UBYTE base,unsigned allocated,UBYTE x,UBYTE y) {
ref_draws[ref_draw_count*3]=100u+3u*(*meta);ref_draws[ref_draw_count*3+1]=x;
ref_draws[ref_draw_count*3+2]=y;ref_draw_count++;(void)base;(void)allocated;return 1;}
'''
    # NGPC adaptation (docs/ECARTS_GBSTUDIO.md, projectiles): the original
    # search, actors outside the projectile's mask skipped. Only this condition
    # and the one call below differ from the released projectiles.c.
    in_mask=actor.replace('actor_overlapping_bb(rect16_t *bb, upoint16_t *offset, actor_t *ignore)',
                          'ngc_overlapping_bb_in_mask(rect16_t *bb, upoint16_t *offset, actor_t *ignore, UBYTE mask)')
    old_skip='if (actor == ignore || !CHK_FLAG(actor->flags, ACTOR_FLAG_COLLISION)) {'
    assert in_mask.count(old_skip)==1 and in_mask!=actor
    in_mask=in_mask.replace(old_skip,'if (actor == ignore || !CHK_FLAG(actor->flags, ACTOR_FLAG_COLLISION) || !(actor->collision_group & mask)) {')
    prefix+=helpers+bb+'\n'+actor+'\n'+in_mask+'\n'
    begin=source.index('projectile_t projectiles[');end=source.index('void projectiles_init')
    prefix+=source[begin:end]
    prefix+='''static projectile_t *projectile,*prev_projectile,*next_projectile;
static UBYTE min_x,max_x,min_y,max_y;
'''
    for name in ('projectiles_init','projectile_launch','projectiles_update','projectiles_render','remove_projectile'):
        prefix+=original_function(source,name)+'\n'
    # remove_projectile must be declared before projectiles_update under C99.
    prefix=prefix.replace('static projectile_t *projectile,*prev_projectile,*next_projectile;',
                           'static void remove_projectile(void);\nstatic projectile_t *projectile,*prev_projectile,*next_projectile;')
    call='hit_actor = actor_overlapping_bb(&projectile->def.bounds, &projectile->pos, NULL);'
    assert prefix.count(call)==1
    prefix=prefix.replace(call,'hit_actor = ngc_overlapping_bb_in_mask(&projectile->def.bounds, &projectile->pos, NULL, projectile->def.collision_mask);')
    prefix+='void ref_reset(void) {unsigned i;projectiles_init();ref_count=0;ref_draw_count=0;\n'
    prefix+='for(i=0;i<256;i++){frame_identity[i]=(UBYTE)i;metasprites[i]=frame_identity+i;}ref_sheet.metasprites=metasprites;\n'
    for i,d in enumerate(definitions()):
        p=f'projectile_defs[{i}]'
        prefix+=f'{p}.sprite.ptr=&ref_sheet;{p}.sprite.bank=1;{p}.base_tile=0;\n'
        for field,value in (('life_time',d.life_frames),('move_speed',d.speed_subpx),('initial_offset',d.initial_offset_subpx),
                            ('anim_tick',d.animation_tick_mask),('anim_noloop',int(not d.loop_animation)),
                            ('strong',int(not d.destroy_on_hit)),('collision_group',d.collision_group),('collision_mask',d.collision_mask)):
            prefix+=f'{p}.{field}={value};'
        for field,value in zip(('left','right','top','bottom'),d.bounds_subpx):prefix+=f'{p}.bounds.{field}={value};'
        for j,(start,end) in enumerate(d.animations):prefix+=f'{p}.animations[{j}].start={start};{p}.animations[{j}].end={end};'
        prefix+='\n'
    return prefix+'''}
void ref_begin(void){ref_count=0;ref_draw_count=0;}
void ref_actor(unsigned i,unsigned x,unsigned y,unsigned flags,unsigned bank,unsigned handle,unsigned prev,
 short l,short r,short t,short b){actors[i].pos.x=x;actors[i].pos.y=y;actors[i].flags=flags;
 actors[i].collision_group=(UBYTE)(1u<<i);actors[i].script.bank=bank;actors[i].hscript_hit=handle;
 actors[i].prev=prev==255?NULL:actors+prev;actors[i].bounds.left=l;actors[i].bounds.right=r;
 actors[i].bounds.top=t;actors[i].bounds.bottom=b;}
void ref_launch(unsigned type,unsigned x,unsigned y,unsigned angle){upoint16_t pos;pos.x=x;pos.y=y;projectile_launch(type,&pos,angle);}
void ref_update(unsigned time,short sx,short sy){game_time=time;draw_scroll_x=sx;draw_scroll_y=sy;projectiles_update();}
void ref_render(short sx,short sy){draw_scroll_x=sx;draw_scroll_y=sy;projectiles_render();}
unsigned ref_get(unsigned field,unsigned i){projectile_t *p=projectiles+i;
switch(field){case 0:return projectiles_active_head?(unsigned)(projectiles_active_head-projectiles):255;
case 1:return projectiles_inactive_head?(unsigned)(projectiles_inactive_head-projectiles):255;
case 2:return p->next?(unsigned)(p->next-projectiles):255;
case 3:return p->pos.x;case 4:return p->pos.y;case 5:return (UWORD)p->delta_pos.x;case 6:return (UWORD)p->delta_pos.y;
case 7:return p->def.life_time;case 8:return p->frame;case 9:return p->frame_start;case 10:return p->frame_end?p->frame_end:256;
case 11:return p->def.anim_tick;case 12:return p->def.anim_noloop|(p->def.strong<<1);case 13:return p->def.collision_group;
case 14:return p->def.collision_mask;case 15:return (UWORD)p->def.bounds.left;case 16:return (UWORD)p->def.bounds.right;
case 17:return (UWORD)p->def.bounds.top;case 18:return (UWORD)p->def.bounds.bottom;
case 19:return ref_count;case 20:return ref_logs[i];case 21:return actors[i].hscript_hit;
case 22:return ref_draw_count;case 23:return ref_draws[i];}return 0;}
'''


def generated_source(capacity,witness=None,spread='SPREAD_4'):
    actors='''static unsigned short act_x[4],act_y[4],gen_handles[4];
static short act_bounds[16];static unsigned char act_flags[4],act_prev[4],gen_banks[4];
static unsigned gen_count,gen_logs[16],gen_draw_count,gen_draws['''+str(capacity*3)+'''];
void ngc_projectile_script(unsigned char i,unsigned char group){
if(gen_banks[i]&&(gen_handles[i]&0x8000u)) {gen_logs[gen_count*2]=i;gen_logs[gen_count*2+1]=group;
gen_count++;gen_handles[i]=1;}}
void ngc_sprite_draw(unsigned short frame,short x,short y){gen_draws[gen_draw_count*3]=frame;
gen_draws[gen_draw_count*3+1]=(unsigned char)x;gen_draws[gen_draw_count*3+2]=(unsigned char)y;gen_draw_count++;}
'''
    bridge='\n'.join(projectile_collision_source(GROUPS))
    kernel='\n'.join(projectile_source(definitions(),FRAMES,capacity=capacity,width_tiles=20,height_tiles=18,
                                         collision_spread=spread))
    if witness=='duration':kernel=kernel.replace('proj_life[i] == 0u','proj_life[i] == 1u')
    elif witness=='spread':kernel=kernel.replace('(iterator & 3u) == 0u','(iterator & 1u) == 0u')
    elif witness=='first-overlap':
        # GBVM 4.3.2 as released: the first overlapping actor, then the mask.
        old='(act_flags[i] & 8u) && (proj_actor_groups[i] & mask) &&'
        assert bridge.count(old)==1
        bridge=bridge.replace(old,'(act_flags[i] & 8u) &&').replace(
            '            ngc_projectile_script(i, group); return 1u;',
            '            if (!(proj_actor_groups[i] & mask)) return 0u;\n            ngc_projectile_script(i, group); return 1u;')
    elif witness=='strong':kernel=kernel.replace('&& !(proj_flags[i] & 2u)','&& 1u')
    elif witness=='nonloop':kernel=kernel.replace('if (!(proj_flags[i] & 1u))','if (1u)')
    elif witness=='pool-order':kernel=kernel.replace('proj_next[i] = proj_free; proj_free = (unsigned char)i;',
                                                  'proj_next[i] = (unsigned short)i + 1u < '+str(capacity)+'u ? (unsigned char)(i + 1u) : 255u; proj_free = 0u;')
    elif witness is not None:raise ValueError('unknown witness')
    return actors+bridge+'\n'+kernel+'''
void gen_reset(void){ngc_projectiles_init();gen_count=0;gen_draw_count=0;}
void gen_begin(void){gen_count=0;gen_draw_count=0;}
void gen_actor(unsigned i,unsigned x,unsigned y,unsigned flags,unsigned bank,unsigned handle,unsigned prev,
 short l,short r,short t,short b){act_x[i]=x;act_y[i]=y;act_flags[i]=flags;gen_banks[i]=bank;gen_handles[i]=handle;
act_prev[i]=prev;act_bounds[i*4]=l;act_bounds[i*4+1]=r;act_bounds[i*4+2]=t;act_bounds[i*4+3]=b;}
void gen_launch(unsigned type,unsigned x,unsigned y,unsigned angle){ngc_projectile_launch(type,x,y,angle);}
void gen_update(unsigned time,short sx,short sy){ngc_projectiles_update(time,sx,sy);}
void gen_render(short sx,short sy){ngc_projectiles_render(sx,sy);}
unsigned gen_get(unsigned field,unsigned i){switch(field){case 0:return proj_head;case 1:return proj_free;
case 2:return proj_next[i];case 3:return proj_x[i];case 4:return proj_y[i];case 5:return (unsigned short)proj_dx[i];
case 6:return (unsigned short)proj_dy[i];case 7:return proj_life[i];case 8:return proj_frame[i];case 9:return proj_start[i];
case 10:return proj_end[i];case 11:return proj_tick[i];case 12:return proj_flags[i];case 13:return proj_group[i];case 14:return proj_mask[i];
case 15:case 16:case 17:case 18:return (unsigned short)proj_bounds[i*4+field-15];
case 19:return gen_count;case 20:return gen_logs[i];case 21:return gen_handles[i];case 22:return gen_draw_count;case 23:return gen_draws[i];}return 0;}
'''


REPLAY=r'''const fs=require('fs'),e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const capacity=Number(process.argv[3]);let checked=0,hitCalls=0,hitActors=0,rendered=0;
function snapshot(prefix){const g=e[prefix+'_get'];let active=[],free=[],head=g(0,0),tail=g(1,0);
for(let i=head;i!==255;i=g(2,i)){if(active.length>=capacity)throw Error('active cycle');active.push(i);}
for(let i=tail;i!==255;i=g(2,i)){if(free.length>=capacity)throw Error('free cycle');free.push(i);}
if(new Set([...active,...free]).size!==capacity)throw Error('lost or duplicate pool slot');
return {active,free,slots:active.map(i=>[i,...Array.from({length:16},(_,j)=>g(j+3,i))]),
handles:Array.from({length:4},(_,i)=>g(21,i)),logs:Array.from({length:g(19,0)*2},(_,i)=>g(20,i)),
draws:Array.from({length:g(22,0)*3},(_,i)=>g(23,i))};}
function compare(op,args){const a=snapshot('ref'),b=snapshot('gen');if(JSON.stringify(a)!==JSON.stringify(b))
throw Error(JSON.stringify({checked,op,args,expected:a,actual:b}));checked++;hitCalls+=a.logs.length/2;
for(let i=0;i<a.logs.length;i+=2)hitActors|=1<<a.logs[i];rendered+=a.draws.length/3;}
function call(op,...args){e.ref_begin();e.gen_begin();e['ref_'+op](...args);e['gen_'+op](...args);compare(op,args);}
function reset(){call('reset');for(let i=0;i<4;i++)call('actor',i,65500,65500,8,1,0x8000,i===3?255:i+1,-128,127,-128,127);}
// Exhaustion, LIFO reuse, zero/one lifetime, wrap animation, held script handles.
for(let type=0;type<9;type++)for(const angle of [0,31,32,64,96,97,128,159,160,192,224,225,255]){
reset();for(let i=0;i<capacity+2;i++)call('launch',type,2048+i*16,2048,angle);
for(let time=0;time<260;time++){call('update',time,0,0);call('render',0,0);
if(time===1)call('launch',type,2048,2048,angle);}}
// First overlap is NOT in mask: the later eligible actor is hit (NGPC adaptation).
reset();call('actor',0,2048,2048,8,1,0x8000,1,-128,127,-128,127);
call('actor',1,2048,2048,8,1,0x8000,2,-128,127,-128,127);
call('launch',0,2048,2048,64);call('update',0,0,0);call('render',0,0);
// Player only: bypass an overlapping NPC; strong hits recur after rearming,
// while a running actor script cannot be started a second time.
for(const type of [0,1,3,4,6,7]){reset();for(let i=0;i<4;i++)call('actor',i,2048,2048,8,1,0x8000,i===3?255:i+1,-1024,1024,-1024,1024);
for(let k=0;k<capacity;k++)call('launch',type,2048,2048,64);
for(let t=0;t<24;t++){call('update',t,0,0);call('render',0,0);
if(t===8||t===16)for(let i=0;i<4;i++)call('actor',i,2048,2048,8,1,0x8000,i===3?255:i+1,-1024,1024,-1024,1024);}}
// Exercise each actor, present/absent banks, held/terminated handles, hidden
// flags, collision disabled, and rearmed strong contacts. Other actors are far.
for(let target=0;target<4;target++)for(const [flags,bank,handle]of [[8,1,0x8000],[8,0,0x8000],[8,1,1],[0,1,0x8000],[10,1,0x8001]]){
reset();call('actor',target,2048,2048,flags,bank,handle,target===3?255:target+1,-2048,8192,-2048,8192);
for(let k=0;k<capacity;k++)call('launch',4,1536,2048,64);
for(let t=0;t<16;t++){if(t===3||t===7||t===11)call('actor',target,2048,2048,flags,bank,handle,target===3?255:target+1,-2048,8192,-2048,8192);
call('update',t,0,0);call('render',0,0);}}
// Signed scroll, saturated clip edges, positional wrap and boundary pixels.
for(const [sx,sy]of [[-32768,-1],[0,0],[1,7],[8,16],[2000,2000],[2047,2047],[32767,32767]])
for(const [x,y]of [[0,0],[65535,0],[0,65535],[65535,65535],[5631,5119],[5632,5120],[2048,2048]]){
reset();for(let k=0;k<capacity;k++)call('launch',k%9,x,y,(k*97)&255);
for(let t=250;t<262;t++){call('update',t,sx,sy);call('render',sx,sy);}}
let seed=432260;const rand=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed%n;};
const scroll=[-32768,-1,0,1,7,8,64,255,2000,2047,32767];reset();
for(let step=0;step<30000;step++){const op=rand(12);
if(op<5)call('launch',rand(9),rand(3)?2048+rand(2048):rand(65536),rand(3)?2048+rand(2048):rand(65536),rand(256));
else if(op<9){const sx=scroll[rand(scroll.length)],sy=scroll[rand(scroll.length)];call('update',rand(65536),sx,sy);call('render',sx,sy);}
else if(op<11){const i=rand(4);call('actor',i,rand(2)?2048+rand(2048):rand(65536),rand(2)?2048+rand(2048):rand(65536),
rand(2)?8:0,rand(2),rand(2)?0x8000:1,i===3?255:i+1,-rand(1025),rand(1025),-rand(1025),rand(1025));}
else reset();}
process.stdout.write(JSON.stringify({checked,hitCalls,hitActors,rendered,capacity,mismatches:0}));'''


def run(out,clang,capacity=5,witness=None,spread='SPREAD_4'):
    out=Path(out)
    if out.exists():raise ValueError('output must be new')
    out.mkdir(parents=True)
    source=reference_source(capacity,spread)+'\n'+generated_source(capacity,witness,spread)
    write_new(out/'comparison.c',source.encode('utf-8'))
    p=subprocess.run([clang,'--target=wasm32','-nostdlib','-O2','-fno-builtin','-Wl,--no-entry','-Wl,--export-all',
        str(out/'comparison.c'),'-o',str(out/'comparison.wasm')],capture_output=True,text=True,timeout=60)
    if p.returncode:raise ValueError(p.stderr)
    write_new(out/'replay.js',REPLAY.encode('utf-8'))
    p=subprocess.run(['node',str(out/'replay.js'),str(out/'comparison.wasm'),str(capacity)],capture_output=True,text=True,timeout=60)
    sources=('src/core/projectiles.c','src/core/actor.c','include/collision.h','include/sincos.h','include/gbs_types.h')
    report={'returncode':p.returncode,'witness':witness,'spread':spread,'stderr':p.stderr,'stdout':p.stdout,'gbvm_commit':GBVM_COMMIT,
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'reference_sha256':{name:hashlib.sha256((GBVM/name).read_bytes()).hexdigest() for name in sources},
            'definitions':[asdict(d) for d in definitions()], 'ram_bytes':projectile_ram_bytes(capacity),'complete_rom':False}
    if not p.returncode:report.update(json.loads(p.stdout))
    write_json(out/'comparison.json',report)
    return report


def compile_kernel(out,toolchain,capacity=5):
    out=Path(out)
    if out.exists():raise ValueError('output must be new')
    out.mkdir(parents=True);info=locate(toolchain)
    if not info['ready']:raise ValueError('Toshiba toolchain incomplete')
    source=generated_source(capacity)
    write_new(out/'projectile_pool.c',source.encode('utf-8'))
    env=os.environ.copy();env['THOME']=info['root'];env['PATH']=info['bin']+os.pathsep+env.get('PATH','')
    compiler=Path(info['bin'])/'cc900.exe'
    with _compiler_lock():
        p=subprocess.run([str(compiler),'-c','-O3','-Nb2',str(out/'projectile_pool.c'),'-o',str(out/'projectile_pool.rel')],
                         cwd=out,env=env,capture_output=True,text=True,errors='replace',timeout=60)
    report={'returncode':p.returncode,'output':p.stdout+p.stderr,'object_exists':(out/'projectile_pool.rel').exists(),
            'compiler_sha256':hashlib.sha256(compiler.read_bytes()).hexdigest(),'complete_rom':False,'capacity':capacity}
    write_json(out/'compile.json',report);return report


def measure_pool_ram(toolchain,capacity):
    source='\n'.join(projectile_source(definitions(),FRAMES,capacity=capacity,width_tiles=20,height_tiles=18))
    declarations=re.findall(r'^(?:static )?(?:unsigned char|unsigned short|short) proj_[a-z]+(?:\[\d+\])?;',source,re.M)
    if len(declarations)!=16:raise ValueError('pool RAM declarations changed')
    names=[re.search(r'\b(proj_[a-z]+)',line)[1] for line in declarations]
    prefix='\n'.join('extern '+line.removeprefix('static ') for line in declarations)+'\n'
    with _compiler_lock():
        measured=measure_layout(prefix,{'pool':'+'.join('sizeof('+name+')' for name in names)},toolchain)
    return measured


def native_operations(capacity):
    ops=[]
    def add(name,*args):ops.append([name,list(args)])
    def reset(overlap):
        add('reset')
        for i in range(4):add('actor',i,2048 if overlap else 65500,2048 if overlap else 65500,8,1,0x8000,
                             255 if i==3 else i+1,-1024 if overlap else -128,1024 if overlap else 127,
                             -1024 if overlap else -128,1024 if overlap else 127)
    for type in range(len(definitions())):
        reset(type%2==1)
        for k in range(capacity+2):add('launch',type,2048,2048,(k*97)&255)
        for t in range(32):
            add('update',t,0,0);add('render',0,0)
            if t in (8,16):add('actor',0,2048,2048,8,1,0x8000,1,-1024,1024,-1024,1024)
            if t==1:add('launch',type,2048,2048,64)
    # Bounds at wrap, sprite order at frame 255, negative scroll and clip edges.
    for sx,sy in ((-32768,-1),(1,7),(2000,2000),(2047,2047),(32767,32767)):
        reset(False)
        for k in range(capacity+2):add('launch',k%9,65535 if k%2 else 0,65535 if k%3 else 0,(k*32)&255)
        for t in (254,255,256,257):add('update',t,sx,sy);add('render',sx,sy)
    # First overlap outside the mask, a later actor in it: that one is hit.
    reset(True);add('launch',0,2048,2048,64);add('update',0,0,0);add('render',0,0)
    # Four actor groups, bank absent, script running, and strong rearming.
    for target,type in enumerate((1,0,6,8)):
        for bank,handle in ((1,0x8000),(0,0x8000),(1,1)):
            reset(False)
            add('actor',target,2048,2048,8,bank,handle,255 if target==3 else target+1,-2048,8192,-2048,8192)
            add('launch',type,(2048-definitions()[type].initial_offset_subpx)&65535,2048,64)
            for t in range(4):add('update',t,0,0);add('render',0,0)
        reset(False)
        add('actor',target,2048,2048,8,1,0x8000,255 if target==3 else target+1,-2048,8192,-2048,8192)
        for k in range(capacity):add('launch',4,1536,2048,64)
        for t in range(16):
            if t in (3,7,11):add('actor',target,2048,2048,8,1,0x8000,255 if target==3 else target+1,-2048,8192,-2048,8192)
            add('update',t,0,0);add('render',0,0)
    return ops


def native_run(out,toolchain,emulator,bios,clang,capacity=5):
    """Standalone ROM, real TLCS-900 C, stub renderer/scripts; no P3 claim.

    A ROM replay position identifies the test vector; it measures no timing.
    The observer uses unused RAM at 0x6000..0x6400, outside linked state and
    below the stack reserve. Production sources contain no observer.
    """
    from ngpcraft.compiler import analyze
    from ngpcraft.emitter import export
    from ngpcraft.toolchain import build_rom
    from tools.probe_rom import power_on_ram
    if capacity not in (5,17):raise ValueError('native observer reserved for five or seventeen slots')
    out=Path(out)
    if out.exists():raise ValueError('output must be new')
    out.mkdir(parents=True);ops=native_operations(capacity);write_json(out/'operations.json',ops)
    # Execute the reference against this independent, retained ROM schedule.
    write_new(out/'reference.c',reference_source(capacity).encode('utf-8'))
    p=subprocess.run([clang,'--target=wasm32','-nostdlib','-O2','-fno-builtin','-Wl,--no-entry','-Wl,--export-all',
                      str(out/'reference.c'),'-o',str(out/'reference.wasm')],capture_output=True,text=True,timeout=60)
    if p.returncode:raise ValueError(p.stderr)
    js=r'''const fs=require('fs'),e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const ops=JSON.parse(fs.readFileSync(process.argv[3])),n=Number(process.argv[4]);e.ref_reset();
for(let i=0;i<4;i++)e.ref_actor(i,65500,65500,8,1,0x8000,i===3?255:i+1,-128,127,-128,127);
let result=[];for(const [op,args]of ops){e.ref_begin();e['ref_'+op](...args);const g=e.ref_get,active=new Set();
for(let i=g(0,0);i!==255;i=g(2,i))active.add(i);
const row=[g(0,0),g(1,0),g(19,0),g(22,0),...Array.from({length:4},(_,i)=>g(21,i))];
for(let i=0;i<n;i++)row.push(g(2,i),active.has(i)?1:0,...Array.from({length:16},(_,j)=>active.has(i)?g(j+3,i):0));
for(let i=0;i<16;i++)row.push(i<g(19,0)*2?g(20,i):0);
for(let i=0;i<n*3;i++)row.push(i<g(22,0)*3?g(23,i):0);
result.push(row);}process.stdout.write(JSON.stringify(result));'''
    write_new(out/'reference.js',js.encode('utf-8'))
    p=subprocess.run(['node',str(out/'reference.js'),str(out/'reference.wasm'),str(out/'operations.json'),str(capacity)],
                     capture_output=True,text=True,timeout=60)
    if p.returncode:raise ValueError(p.stderr)
    expected=json.loads(p.stdout);write_json(out/'expected.json',expected)
    manifest=export(analyze({'schema':'ngpcraft-ir/0.1','start':'probe','scenes':[{'id':'probe'}]}),out/'export','rom')
    # Keep the normal link-audited M0 bootstrap; the isolated game object uses
    # private test functions, never a new feature or a fake production export.
    source=generated_source(capacity)
    source=re.sub(r'(?m)^(unsigned char|unsigned short|unsigned|void) ((?:ngc_|gen_)\w+)\(',r'static \1 \2(',source)
    source=source.replace('unsigned char proj_head;','static unsigned char proj_head;')
    source+='\nstatic unsigned short replay_position;\nstatic const unsigned short replay_ops['+str(len(ops))+'][12] = {\n'
    kinds={'reset':0,'actor':1,'launch':2,'update':3,'render':4}
    for name,args in ops:
        words=[kinds[name],*args]+[0]*(11-len(args))
        source+='    {'+', '.join('0x%04Xu'%(v&65535) for v in words)+'},\n'
    source+='''};
void ngc_game_init(void) {
    unsigned short i;
    replay_position=0u;gen_reset();
    for(i=0u;i<4u;++i)gen_actor(i,65500u,65500u,8u,1u,0x8000u,i==3u?255u:i+1u,-128,127,-128,127);
    *(volatile unsigned short *)0x6000=0xC0DEu;*(volatile unsigned short *)0x6002=65535u;
}
void ngc_game_tick(void) {
    const unsigned short *args;
    volatile unsigned short *row;
    unsigned short i;
    unsigned short j;
    unsigned char slot;
    unsigned char active;
'''
    source+=f'    if(replay_position >= {len(ops)}u)return;\n'
    source+='''    args=replay_ops[replay_position]+1;gen_begin();
    switch(replay_ops[replay_position][0]) {
    case 0u:gen_reset();break;
    case 1u:gen_actor(args[0],args[1],args[2],args[3],args[4],args[5],args[6],
                     (short)args[7],(short)args[8],(short)args[9],(short)args[10]);break;
    case 2u:gen_launch(args[0],args[1],args[2],args[3]);break;
    case 3u:gen_update(args[0],(short)args[1],(short)args[2]);break;
    case 4u:gen_render((short)args[0],(short)args[1]);break;
    }
    row=(volatile unsigned short *)0x6004;
    *row++=(unsigned short)gen_get(0u,0u);*row++=(unsigned short)gen_get(1u,0u);
    *row++=(unsigned short)gen_get(19u,0u);*row++=(unsigned short)gen_get(22u,0u);
    for(i=0u;i<4u;++i)*row++=(unsigned short)gen_get(21u,i);
'''
    source+=f'    for(i=0u;i<{capacity}u;++i){{\n'
    source+='''        active=0u;slot=proj_head;
        while(slot!=255u){if(slot==i){active=1u;break;}slot=proj_next[slot];}
        *row++=(unsigned short)gen_get(2u,i);*row++=active;
        for(j=3u;j<19u;++j)*row++=active?(unsigned short)gen_get(j,i):0u;
    }
    for(i=0u;i<16u;++i)*row++=i<gen_count*2u?(unsigned short)gen_get(20u,i):0u;
'''
    source+=f'    for(i=0u;i<{capacity*3}u;++i)*row++=i<gen_draw_count*3u?(unsigned short)gen_get(23u,i):0u;\n'
    source+='''    *(volatile unsigned short *)0x6002=replay_position;
    *(volatile unsigned short *)0x6000=0xC0DEu;
    ++replay_position;
}
'''
    path=out/'export/src/game.c'
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:f.write(source)
    os.replace(tmp,path)
    manifest['sha256']['src/game.c']=hashlib.sha256(path.read_bytes()).hexdigest()
    notice=out/'export/NOTICE.txt'
    text=notice.read_text(encoding='utf-8')+'\nProjectile arithmetic/pool adapted from GBVM bd6f41c:\n'+(GBVM/'LICENSE').read_text(encoding='utf-8')
    fd,tmp=tempfile.mkstemp(dir=notice.parent)
    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:f.write(text)
    os.replace(tmp,notice)
    manifest['sha256']['NOTICE.txt']=hashlib.sha256(notice.read_bytes()).hexdigest()
    write_json(out/'export/export.json',manifest)
    build=build_rom(out/'export',toolchain)
    if build['link_audit']['stack']['static_ram_end']>0x6000:raise ValueError('observer overlaps linked RAM')
    row_words=len(expected[0])
    if 4+row_words*2>1024:raise ValueError('observer rows exceed reserved scratch RAM')
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    machine=native.NativeMachine(rom,bios=bios_data)
    core_hash=hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest()
    # The core of the emulator given (not a stale copy elsewhere); its hash is recorded below.
    if not Path(machine._lib._name).resolve().is_relative_to(Path(emulator).resolve()):raise ValueError('unexpected native core')
    machine.set_timing_silicon(word_wait=10,bios_wait=8);machine.reset(bios_handoff=True);power_on_ram(machine,'noise')
    breakpoint=build['link_audit']['public_symbols']['_ngc_game_tick']['address']
    machine.set_breakpoints([breakpoint]);actual=[];errors=[]
    for _ in range(len(ops)+2):
        stop,_=machine.run(3_000_000,record=False)
        if stop.stop_status!=40:raise ValueError('ROM replay tick not reached')
        header=machine.read(0x6000,4)
        if int.from_bytes(header[:2],'little')!=0xC0DE:raise ValueError('observer marker missing')
        index=int.from_bytes(header[2:],'little')
        if index==65535:continue
        if index!=len(actual):raise ValueError('unexpected replay order')
        data=machine.read(0x6004,row_words*2)
        row=[int.from_bytes(data[i:i+2],'little') for i in range(0,len(data),2)]
        actual.append(row)
        if row!=expected[index]:errors.append({'index':index,'operation':ops[index],
                                              'fields':[[i,x,y] for i,(x,y) in enumerate(zip(expected[index],row)) if x!=y]})
        if len(actual)==len(ops):break
    if len(actual)!=len(ops):raise ValueError('native replay incomplete')
    write_json(out/'actual.json',actual)
    actor_mask=0;script_calls=0
    for row in actual:
        script_calls+=row[2]
        for i in range(row[2]):actor_mask|=1<<row[8+capacity*18+i*2]
    report={'operations':len(ops),'capacity':capacity,'mismatch_operations':len(errors),'first_errors':errors[:5],
            'script_calls':script_calls,'hit_actors_mask':actor_mask,'rendered':sum(row[3] for row in actual),
            'emulator_core_sha256':core_hash,'bios_sha256':hashlib.sha256(bios_data).hexdigest(),
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'silicon':{'word_wait':10,'bios_wait':8},
            'scope':'Standalone TLCS-900 kernel, noisy RAM, BIOS handoff; script/renderer stubs; no VM or OAM/pixel proof',
            'static_ram_bytes':build['link_audit']['static_ram_bytes']}
    write_json(out/'comparison.json',report);return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--clang',required=True)
    p.add_argument('--capacity',type=int,default=5);a=p.parse_args();r=run(a.out,a.clang,a.capacity)
    print(json.dumps({k:v for k,v in r.items() if k not in ('definitions','stdout','stderr')}));raise SystemExit(r['returncode']!=0)

if __name__=='__main__':main()
