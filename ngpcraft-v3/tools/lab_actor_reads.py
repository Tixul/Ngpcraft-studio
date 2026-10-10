"""Direction and live actor properties: native ROM with published assets."""
import argparse,json,hashlib,sys
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import derive,write_json
from tools.demo_actor_moves import ev,num,GBS2
from tools.probe_rom import power_on_ram,verified_tick
from tools.music_rom import png
from tools.reference import REFERENCE

EXPECTED={20:0,21:1,22:2,23:3,24:6,25:8,26:50,27:67,28:1600,29:2144,30:3,31:81,32:93,33:174,34:1}

def project_case(root):
    project=Path(root)/'project';derive(GBS2,project,'sample_town',(6,8),False)
    path=next((project/'project/scenes').rglob('scene.gbsres'))
    scene=json.loads(path.read_text());script=[]
    for index,direction in enumerate(('down','right','up','left')):
        script.extend([ev(f'dir-{index}','EVENT_ACTOR_SET_DIRECTION',actorId='player',direction={'type':'direction','value':direction}),
                       ev(f'read-{index}','EVENT_ACTOR_GET_DIRECTION',actorId='player',direction=str(20+index))])
    script.append(ev('position','EVENT_ACTOR_SET_POSITION',actorId='player',x=num(50),y=num(67),units='pixels'))
    def prop(name):return {'type':'property','target':'player','property':name}
    for index,name in enumerate(('xpos','ypos','pxpos','pypos','spxpos','spypos','direction'),24):
        script.append(ev(f'property-{index}','EVENT_SET_VALUE',variable=str(index),value=prop(name)))
    script.append(ev('move-again','EVENT_ACTOR_SET_POSITION',actorId='player',x=num(81),y=num(93),units='pixels'))
    for index,name in ((31,'pxpos'),(32,'pypos')):
        script.append(ev(f'reread-{index}','EVENT_SET_VALUE',variable=str(index),value=prop(name)))
    script.extend([ev('shared-fetch','EVENT_SET_VALUE',variable='33',value={'type':'add','valueA':prop('pxpos'),'valueB':prop('pypos')}),
                   ev('done','EVENT_SET_VALUE',variable='34',value=num(1))])
    scene['script']=script;write_json(path,scene)
    return project

def run(out,toolchain,emulator,bios):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    project=project_case(out);manifest=export_project(project,out/'export');build=build_rom(out/'export',toolchain)
    rom=(out/'export/rom/game.ngc').read_bytes();symbols=build['link_audit']['public_symbols']
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    m=native.NativeMachine(rom,bios=Path(bios).read_bytes());m.set_timing_silicon(word_wait=10,bios_wait=8)
    m.reset(bios_handoff=True);power_on_ram(m,'noise');m.set_breakpoints([verified_tick(rom,build)])
    base=symbols['_script_memory']['address'];actual={}
    done=base+2*manifest['variables']['VAR_VARIABLE_34']
    for _ in range(30):
        stop,_=m.run(15_000_000,record=False)
        if stop.stop_status!=40:raise ValueError('read lab did not reach game tick')
        if int.from_bytes(m.read(done,2),'little')==1:break
    for index in EXPECTED:
        offset=manifest['variables'][f'VAR_VARIABLE_{index}']
        actual[index]=int.from_bytes(m.read(base+offset*2,2),'little')
    m.set_breakpoints([]);m.run_frames(3);(out/'screen.png').write_bytes(png(m.framebuffer()))
    source=REFERENCE/'appData/engine/gbvm/src/core/vm_actor.c'
    report={'schema':'ngpcraft-actor-reads-lab/1','expected':EXPECTED,'actual':actual,
            'mismatches':{k:[v,actual[k]] for k,v in EXPECTED.items() if v!=actual[k]},
            'reference_actor_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'silicon_timing':[10,8],'ram':'noise',
            'basis':'Published vm_actor_get_dir stores actors[idx].dir; vm_actor_get_pos stores fixed-point pos. xpos/ypos shift 8, pxpos/pypos shift 5, spxpos/spypos unchanged.',
            'console_validated':False}
    write_json(out/'comparison.json',report);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for option in ('out','toolchain','emulator','bios'):p.add_argument('--'+option,required=True)
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios);print(json.dumps(r,indent=2))
    if r['mismatches']:raise SystemExit(1)
