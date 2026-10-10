"""Executed GBVM decoder/scheduler and projectile pool; no renderer/OAM proof."""
import argparse,hashlib,json,os,re,subprocess
from pathlib import Path
from ngpcraft.gbvm.assembler import assemble_bank,load_model
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.emit import gbvm_source,RUNTIME,GBVM_H
from ngpcraft.gbvm.stack_budget import stack_budget
from ngpcraft.projectile_runtime import ProjectileDefinition,projectile_source
from ngpcraft.toolchain import locate,_compiler_lock
from tools.lab_projectile_motion import reference_source
from tools.reference import REFERENCE,GBVM_COMMIT


def program(witness=None):
    methods=[('launch_projectile_in_direction','right'),('launch_projectile_in_angle',-255.5),
             ('launch_projectile_in_angle_variable','0'),('launch_projectile_in_source_actor_direction',None),
             ('launch_projectile_in_actor_direction',1),('launch_projectile_towards_actor',1)]
    texts=[]
    for i,(method,arg) in enumerate(methods):
        out=[];builder=ScriptBuilder(out,{})
        builder.actor_set_active(0)
        getattr(builder,method)(7,1.125,-2.25,*([] if arg is None else [arg]))
        builder._pack_locals();builder.script_end()
        texts.append(builder.to_script_string('launch_'+str(i),False))
    texts.append('''.module hit
___bank_hit = 255
_hit::
        VM_GET_TLOCAL 1, 0
        VM_IDLE
        VM_IDLE
        VM_RPN
            .R_REF 2
            .R_INT16 1
            .R_OPERATOR .ADD
            .R_REF_SET 2
            .R_STOP
        VM_STOP
''')
    texts.append('.module empty\n___bank_empty = 255\n_empty::\n        VM_STOP\n')
    model=load_model();runtime=json.loads(RUNTIME.read_text(encoding='utf-8'))
    data,scripts,macros,rpn=assemble_bank(texts,{'VAR_VARIABLE_0':0},model)
    budget=stack_budget(scripts,model,runtime,contexts=4,entry_arguments={'hit':1})
    if any(s['method']!='bytecode_cfg' for s in budget['scripts'].values()):raise ValueError('Unclassified probe stack')
    code,handlers=gbvm_source(runtime,macros,rpn,[],[],model,heap_words=8,stack_words=budget['words_per_context'],
                             input_contexts=4,projectile_actors=2)
    vm=code
    if witness=='angle-byte':vm=vm.replace('(UBYTE)params[2]', '(UBYTE)(params[2] + 1u)')
    elif witness=='busy-handle':vm=vm.replace('if (!(*handle & SCRIPT_TERMINATED)) return;', '/* witness: duplicate hit allowed */')
    elif witness=='atan2-quadrant':vm=vm.replace('return 192 + atan2_table[-x][-y];','return 192 - atan2_table[-x][-y];')
    elif witness is not None:raise ValueError('Unknown VM witness')
    definition=ProjectileDefinition((-96,95,-96,95),((0,0),(1,1),(2,2),(3,3)),60,96,257,15,True,True,8,2)
    pool='\n'.join(projectile_source((definition,),(0,1,2,3),capacity=5,width_tiles=20,height_tiles=18,definition_ids=(7,)))
    hit=scripts[6].address;empty=scripts[7].address
    support=f'''
static const unsigned char bank_0[] = {{{','.join(str(b) for b in data)}}};
static const unsigned char * const banks[] = {{bank_0,bank_0}};
static const unsigned short entries[6] = {{{','.join(str(s.address) for s in scripts[:6])}}};
static unsigned short probe_x[2],probe_y[2];static unsigned char probe_dir[2];
void ngc_actor_get_pos(unsigned char actor,unsigned short *params){{params[1]=probe_x[actor];params[2]=probe_y[actor];}}
unsigned char ngc_actor_get_dir(unsigned char actor){{return probe_dir[actor];}}
unsigned char ngc_projectile_hit(const short *bounds,unsigned short x,unsigned short y,unsigned char mask,unsigned char group){{return 0u;}}
void ngc_sprite_draw(unsigned short frame,short x,short y){{(void)frame;(void)x;(void)y;}}
static unsigned short probe_done(void){{unsigned short i;unsigned char state;
 for(i=0u;i<100u;++i){{state=script_runner_update();if(state==RUNNER_DONE)return 0u;if(state==RUNNER_EXCEPTION)return 1u;}}return 2u;}}
unsigned short probe_case(unsigned char which,unsigned short x,unsigned short y,unsigned char dir,
                         unsigned short tx,unsigned short ty,unsigned char td,unsigned short angle){{
 probe_x[0]=x;probe_y[0]=y;probe_dir[0]=dir;probe_x[1]=tx;probe_y[1]=ty;probe_dir[1]=td;
 ngc_vm_start(banks,0u,entries[which]);ngc_vm_projectile_reset();ngc_projectiles_init();script_memory[0]=angle;
 return probe_done();}}
unsigned short probe_get(unsigned char field){{unsigned char i=proj_head;
 if(i==255u)return 65535u;
 switch(field){{case 0:return proj_x[i];case 1:return proj_y[i];case 2:return (unsigned short)proj_dx[i];
 case 3:return (unsigned short)proj_dy[i];case 4:return proj_frame[i];case 5:return proj_life[i];}}return 0u;}}
unsigned short probe_math(short y,short x){{return ngc_atan2(y,x);}}
unsigned short probe_hits(void){{
 unsigned short i;SCRIPT_CTX *ctx;
 ngc_vm_start(banks,0u,{empty}u);if(probe_done())return 1u;
 ngc_vm_projectile_reset();script_memory[1]=0u;script_memory[2]=0u;
 ngc_vm_projectile_hit(0u,0u,0u,{hit}u,8u);if(first_ctx)return 2u;
 ngc_vm_projectile_hit(255u,1u,0u,{hit}u,8u);if(first_ctx)return 3u;
 ngc_vm_projectile_hit(0u,1u,0u,{hit}u,8u);if(!first_ctx)return 4u;
 if(*first_ctx->base_addr!=8u||ngc_projectile_handles[0]&SCRIPT_TERMINATED)return 5u;
 ngc_vm_projectile_hit(0u,1u,0u,{hit}u,4u);if(first_ctx->next)return 6u;
 ngc_vm_projectile_hit(1u,1u,1u,{hit}u,2u);if(!first_ctx->next)return 7u;
 if(*first_ctx->next->base_addr!=2u)return 8u;
 if(probe_done()||script_memory[2]!=2u)return 9u;
 if(!(ngc_projectile_handles[0]&SCRIPT_TERMINATED)||!(ngc_projectile_handles[1]&SCRIPT_TERMINATED))return 10u;
 ngc_vm_projectile_hit(0u,1u,0u,{hit}u,4u);if(probe_done()||script_memory[2]!=3u||script_memory[1]!=4u)return 11u;
 /* Exhausted contexts preserve the terminated handle; a later attempt works. */
 for(i=0u;i<4u;++i){{ctx=script_execute(0u,(UBYTE *)(bank_0+{hit}u),0,0u);if(!ctx)return 12u;*(ctx->stack_ptr++)=1u;}}
 ngc_vm_projectile_hit(0u,1u,0u,{hit}u,8u);if(!(ngc_projectile_handles[0]&SCRIPT_TERMINATED))return 13u;
 if(probe_done())return 14u;
 ngc_vm_projectile_hit(0u,1u,0u,{hit}u,8u);if(probe_done()||script_memory[1]!=8u)return 15u;
 /* Deactivation detaches the handle, preserving the running script. */
 ngc_vm_projectile_hit(1u,1u,0u,{hit}u,2u);ctx=first_ctx;
 ngc_vm_projectile_detach(1u);if(ctx->hthread||ctx->terminated)return 16u;
 ngc_vm_projectile_activate(1u);ngc_vm_projectile_hit(1u,1u,0u,{hit}u,4u);
 if(!ctx->next)return 17u;
 ctx->PC=bank_0+{empty}u;script_runner_update();
 if(ngc_projectile_handles[1]&SCRIPT_TERMINATED)return 18u;
 if(probe_done()||script_memory[1]!=4u)return 19u;
 /* VM reset drops contexts; load_scene must then reset the actor handles. */
 ngc_vm_projectile_hit(0u,1u,0u,{hit}u,8u);script_runner_init(0u);
 if(ngc_projectile_handles[0]&SCRIPT_TERMINATED)return 20u;
 ngc_vm_projectile_reset();if(ngc_projectile_handles[0]!=SCRIPT_TERMINATED)return 21u;
 return 0u;
}}
'''
    return vm+'\n'+pool+'\n'+support,dict(stack=budget,handlers=handlers,macros=sorted(macros),rpn=sorted(rpn),
                                         scene_actor_api='position/direction fixtures',renderer='stub',real_scheduler=True,real_projectile_pool=True)


def original():
    source=(REFERENCE/'appData/engine/gbvm/src/core/math_atan2.c').read_text(encoding='utf-8')
    source=source[source.index('const uint8_t atan2_table'):].replace('atan2(', 'reference_atan2(')
    return reference_source()+'\n#define CLAMP(a,min,max) (((a)<(min))?(min):(((a)>(max))?(max):(a)))\n'+source+'''
unsigned reference_angle(short y,short x){return reference_atan2(y,x);}
'''


REPLAY=r'''
import fs from 'node:fs';
const e=(await WebAssembly.instantiate(fs.readFileSync(process.argv[2]),{})).instance.exports;
let math=0,launches=0;
function angle(y,x){const expected=e.reference_angle(y,x),actual=e.probe_math(y,x);
 if(expected!==actual)throw Error(JSON.stringify({scope:'atan2',y,x,expected,actual}));math++;return expected;}
const edges=[-32768,-20,-19,-1,0,1,19,20,32767];
for(let v=-32768;v<=32767;v++)for(const edge of edges){angle(v,edge);angle(edge,v);}
for(let x=-19;x<=19;x++)for(let y=-17;y<=17;y++)angle(y,x);
const positions=[[0,0],[1,65535],[65535,1],[1024,1024],[65535,65535],[8192,16384],[32768,32768],[31,32],[255,256]];
const dirs=[128,64,0,192];const signed=v=>(v<<16)>>16;
for(const [x,y]of positions)for(const [tx,ty]of positions)for(let d=0;d<4;d++)for(const a of [0,255,256,65535]) {
 const td=(d+1)%4;
 for(let which=0;which<6;which++){
   const result=e.probe_case(which,x,y,d,tx,ty,td,a);if(result)throw Error('scheduler '+result);
   const angleValue=[64,1,a&255,dirs[d],dirs[td],e.reference_angle(Math.trunc(signed(ty-y)/256),Math.trunc(signed(tx-x)/256))][which];
   e.reference_call((x+36)&65535,(y+72)&65535,angleValue,96,257);
   for(let f=0;f<5;f++){const expected=e.reference_get(f),actual=e.probe_get(f);
      if(expected!==actual)throw Error(JSON.stringify({scope:'launch',which,x,y,tx,ty,d,a,field:f,expected,actual}));}
   if(e.probe_get(5)!==60)throw Error('type/life decoder');launches++;
 }
}
const hits=e.probe_hits();if(hits)throw Error('hit handles '+hits);
process.stdout.write(JSON.stringify({math,launches,fields_per_launch:5,hit_handles:'passed',mismatches:0}));
'''


def run(out,clang,toolchain=None,witness=None):
    out=Path(out)
    if out.exists():raise ValueError('Output directory must be new')
    out.mkdir(parents=True)
    source,details=program(witness)
    (out/'gbvm.h').write_text(GBVM_H,encoding='utf-8')
    (out/'probe.c').write_text(source,encoding='utf-8');(out/'original.c').write_text(original(),encoding='utf-8')
    command=[clang,'--target=wasm32','-nostdlib','-O2','-fno-builtin','-Wl,--no-entry','-Wl,--export-all',
             str(out/'probe.c'),str(out/'original.c'),'-o',str(out/'probe.wasm')]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    if compiled.returncode:raise ValueError(compiled.stderr)
    (out/'replay.mjs').write_text(REPLAY,encoding='utf-8')
    replay=subprocess.run(['node',str(out/'replay.mjs'),str(out/'probe.wasm')],capture_output=True,text=True,timeout=60)
    report=dict(gbvm_commit=GBVM_COMMIT,witness=witness,returncode=replay.returncode,stdout=replay.stdout,stderr=replay.stderr,
                details=details,source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                reference_sha256=hashlib.sha256(original().encode()).hexdigest(),complete_rom=False)
    if not replay.returncode:report.update(json.loads(replay.stdout))
    if toolchain:
        info=locate(toolchain)
        if not info['ready']:raise ValueError('Toshiba toolchain incomplete')
        env=os.environ.copy();env['THOME']=info['root'];env['PATH']=info['bin']+os.pathsep+env.get('PATH','')
        with _compiler_lock():
            target=subprocess.run([str(Path(info['bin'])/'cc900.exe'),'-c','-O3','-Nb2',str(out/'probe.c'),'-o',str(out/'probe.rel')],
                                  cwd=out,env=env,capture_output=True,text=True,errors='replace',timeout=60)
        report['toshiba']=dict(returncode=target.returncode,output=target.stdout+target.stderr,object_exists=(out/'probe.rel').exists())
    (out/'proof.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def native_run(out,toolchain,emulator,bios,clang):
    """M0 bootstrap, private VM probe, native silicon timing; renderer stub.

    Scratch 0x6000..0x6040 is outside the link allocation, below stack reserve.
    Replay position identifies a vector only; it is not a performance counter.
    """
    import sys,tempfile
    from ngpcraft.compiler import analyze
    from ngpcraft.emitter import export
    from ngpcraft.toolchain import build_rom
    from tools.probe_rom import power_on_ram
    out=Path(out)
    if out.exists():raise ValueError('Output must be new')
    out.mkdir(parents=True)
    ops=[]
    positions=[(0,0),(1,65535),(65535,1),(1024,32768),(65535,65535)]
    for i,(x,y) in enumerate(positions):
        tx,ty=positions[(i+1)%len(positions)]
        for direction in range(4):
            for which in range(6):ops.append([0,which,x,y,direction,tx,ty,(direction+1)%4,65535])
    for y in (-32768,-18,-17,-1,0,1,17,18,32767):
        for x in (-32768,-20,-19,-1,0,1,19,20,32767):ops.append([1,y,x,0,0,0,0,0,0])
    ops.append([2,0,0,0,0,0,0,0,0])
    (out/'operations.json').write_text(json.dumps(ops),encoding='utf-8')
    (out/'original.c').write_text(original(),encoding='utf-8')
    compiled=subprocess.run([clang,'--target=wasm32','-nostdlib','-O2','-fno-builtin','-Wl,--no-entry','-Wl,--export-all',
        str(out/'original.c'),'-o',str(out/'original.wasm')],capture_output=True,text=True,timeout=60)
    if compiled.returncode:raise ValueError(compiled.stderr)
    js=r'''const fs=require('fs'),e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
const ops=JSON.parse(fs.readFileSync(process.argv[3])),dirs=[128,64,0,192],s=v=>(v<<16)>>16;
const rows=ops.map(o=>{if(o[0]===1)return [1,e.reference_angle(o[1],o[2]),0,0,0,0,0,0];if(o[0]===2)return [2,0,0,0,0,0,0,0];
const [kind,which,x,y,d,tx,ty,td,a]=o;
const angle=[64,1,a&255,dirs[d],dirs[td],e.reference_angle(Math.trunc(s(ty-y)/256),Math.trunc(s(tx-x)/256))][which];
e.reference_call((x+36)&65535,(y+72)&65535,angle,96,257);
return [0,0,...Array.from({length:5},(_,i)=>e.reference_get(i)),60];});process.stdout.write(JSON.stringify(rows));'''
    (out/'reference.js').write_text(js,encoding='utf-8')
    replay=subprocess.run(['node',str(out/'reference.js'),str(out/'original.wasm'),str(out/'operations.json')],capture_output=True,text=True,timeout=60)
    if replay.returncode:raise ValueError(replay.stderr)
    expected=json.loads(replay.stdout);(out/'expected.json').write_text(json.dumps(expected),encoding='utf-8')
    manifest=export(analyze({'schema':'ngpcraft-ir/0.1','start':'probe','scenes':[{'id':'probe'}]}),out/'export','rom')
    source,details=program()
    source=source.replace('#include "gbvm.h"','')
    source=re.sub(r'(?m)^(void|UBYTE|UWORD|unsigned short|unsigned char) (\w+)\(',r'static \1 \2(',source)
    source=source.replace('UWORD script_memory[','static UWORD script_memory[').replace('unsigned char proj_head;','static unsigned char proj_head;')
    source+='\nstatic unsigned short replay_position;\nstatic const unsigned short replay_ops['+str(len(ops))+'][9] = {\n'
    source+='\n'.join('    {'+','.join('0x%04Xu'%(v&65535) for v in row)+'},' for row in ops)+'\n};\n'
    source+='''void ngc_game_init(void) {
    replay_position=0u;
    *(volatile unsigned short *)0x6000=0xC0DEu;*(volatile unsigned short *)0x6002=65535u;
}
void ngc_game_tick(void) {
    const unsigned short *a;volatile unsigned short *row;unsigned short result;unsigned short i;
'''+f'    if(replay_position>={len(ops)}u)return;\n'+'''
    a=replay_ops[replay_position];row=(volatile unsigned short *)0x6004;
    for(i=0u;i<8u;++i)row[i]=0u;row[0]=a[0];
    if(a[0]==0u){result=probe_case((unsigned char)a[1],a[2],a[3],(unsigned char)a[4],a[5],a[6],(unsigned char)a[7],a[8]);
        row[1]=result;for(i=0u;i<6u;++i)row[i+2]=probe_get((unsigned char)i);}
    else if(a[0]==1u)row[1]=probe_math((short)a[1],(short)a[2]);
    else row[1]=probe_hits();
    *(volatile unsigned short *)0x6002=replay_position;*(volatile unsigned short *)0x6000=0xC0DEu;
    ++replay_position;
}
'''
    def replace(path,data):
        fd,tmp=tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd,'wb') as f:f.write(data)
        os.replace(tmp,path)
    game=out/'export/src/game.c';replace(game,source.encode('utf-8'))
    manifest['sha256']['src/game.c']=hashlib.sha256(game.read_bytes()).hexdigest()
    notice=out/'export/NOTICE.txt'
    replace(notice,(notice.read_text(encoding='utf-8')+'\nGBVM projectile VM probe, bd6f41c:\n'+
                    (REFERENCE/'appData/engine/gbvm/LICENSE').read_text(encoding='utf-8')).encode('utf-8'))
    manifest['sha256']['NOTICE.txt']=hashlib.sha256(notice.read_bytes()).hexdigest()
    replace(out/'export/export.json',json.dumps(manifest,indent=2).encode('utf-8'))
    build=build_rom(out/'export',toolchain)
    if build['link_audit']['stack']['static_ram_end']>0x6000:raise ValueError('Observer overlaps linked RAM')
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    machine=native.NativeMachine(rom,bios=bios_data)
    core_hash=hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest()
    # The core of the emulator given (not a stale copy elsewhere); its hash is recorded below.
    if not Path(machine._lib._name).resolve().is_relative_to(Path(emulator).resolve()):raise ValueError('Unexpected native core')
    machine.set_timing_silicon(word_wait=10,bios_wait=8);machine.reset(bios_handoff=True);power_on_ram(machine,'noise')
    machine.set_breakpoints([build['link_audit']['public_symbols']['_ngc_game_tick']['address']])
    actual=[];errors=[]
    for _ in range(len(ops)+2):
        stop,_=machine.run(3_000_000,record=False)
        if stop.stop_status!=40:raise ValueError('Native tick not reached')
        header=machine.read(0x6000,4)
        if int.from_bytes(header[:2],'little')!=0xC0DE:raise ValueError('Observation marker missing')
        index=int.from_bytes(header[2:],'little')
        if index==65535:continue
        if index!=len(actual):raise ValueError('Replay order changed')
        data=machine.read(0x6004,16);row=[int.from_bytes(data[i:i+2],'little') for i in range(0,16,2)]
        actual.append(row)
        if row!=expected[index]:errors.append(dict(index=index,operation=ops[index],expected=expected[index],actual=row))
        if len(actual)==len(ops):break
    if len(actual)!=len(ops):raise ValueError('Native replay incomplete')
    (out/'actual.json').write_text(json.dumps(actual),encoding='utf-8')
    report=dict(operations=len(ops),launches=120,math_cases=81,hit_lifecycle='passed' if actual[-1][1]==0 else 'failed',
                mismatch_operations=len(errors),first_errors=errors[:5],real_scheduler=True,details=details,
                emulator_core_sha256=core_hash,bios_sha256=hashlib.sha256(bios_data).hexdigest(),
                rom_sha256=hashlib.sha256(rom).hexdigest(),silicon=dict(word_wait=10,bios_wait=8),
                static_ram_bytes=build['link_audit']['static_ram_bytes'],observer_reserved_bytes=64,
                scope='Private native VM/scheduler/pool probe; actor position/direction fixtures and renderer stub; no OAM/pixel proof')
    (out/'proof.json').write_text(json.dumps(report,indent=2),encoding='utf-8');return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--clang',required=True);parser.add_argument('--toolchain');parser.add_argument('--witness')
    args=parser.parse_args();report=run(args.out,args.clang,args.toolchain,args.witness)
    print(json.dumps(report));return int(bool(report['returncode'] or report.get('toshiba',{}).get('returncode')))


if __name__=='__main__':raise SystemExit(main())
