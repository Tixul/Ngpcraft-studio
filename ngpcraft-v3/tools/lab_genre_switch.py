"""Four selected GBVM scene states in one native cartridge, no ROM profiler."""
import argparse, hashlib, json, shutil, sys
from pathlib import Path
from tools.demo_scene_switch import derive_scenes, switch
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json
from tools.reference import REFERENCE
from tools.probe_rom import verified_tick, power_on_ram, write_frame
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom

FOLDERS=('sample_town','path_to_sample_town','space_battle','players_house')
TYPES=('TOPDOWN','PLATFORM','SHMUP','POINTNCLICK')
STARTS=((12,20,'right'),(4,10,'right'),(0,9,'right'),(9,6,'right'))

def project_case(root,start=0):
    project=root/'project'
    keep,ids=derive_scenes(REFERENCE/'appData/templates/gbs2',project,FOLDERS,FOLDERS[start],STARTS[start][:2])
    for k,folder in enumerate(FOLDERS):
        path=keep[folder]/'scene.gbsres';scene=json.loads(path.read_text(encoding='utf-8'))
        scene.update(type=TYPES[k],autoFadeSpeed=None)
        for sub in ('actors','triggers'):
            if (path.parent/sub).exists():shutil.rmtree(path.parent/sub)
        target=(k+1)%len(FOLDERS)
        change=ev('genre-change-'+str(k),'EVENT_SET_INPUT_SCRIPT',input=['b'],override=True)
        change['children']={'true':[switch('genre-go-'+str(k),ids[FOLDERS[target]],STARTS[target],'0')]}
        scene['script']=[ev('genre-visit-'+str(k),'EVENT_INC_VALUE',variable=str(k)),change]
        write_json(path,scene)
    path=project/'project/settings.gbsres';settings=json.loads(path.read_text(encoding='utf-8'))
    settings['startDirection']=STARTS[start][2];write_json(path,settings)
    return project

def run(root,toolchain,emulator,bios,start=0,frames=460):
    if root.exists():raise ValueError('output must be new')
    root.mkdir(parents=True)
    project=project_case(root,start)
    manifest=export_project(project,root/'export');build=build_rom(root/'export',Path(toolchain))
    # Resource order, not folder order, determines linked scene indexes.
    ids=[json.loads(next((project/'project/scenes').rglob(folder+'/scene.gbsres')).read_text(encoding='utf-8'))['id']
         for folder in FOLDERS]
    indexes={s['id']:s['index'] for s in manifest['scene_engine']['scenes']}
    cycle=[indexes[ids[(start+i)%4]] for i in range(5)]
    rom=(root/'export/rom/game.ngc').read_bytes();tick=verified_tick(rom,build)
    symbols=build['link_audit']['public_symbols']
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(Path(emulator).resolve()):raise ValueError('different native binding')
    machine=native.NativeMachine(rom,bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10,bios_wait=8);machine.reset(bios_handoff=True)
    power_on_ram(machine,'noise');machine.set_breakpoints([tick])
    rows=[];captures=set()
    for k in range(frames+1):
        stop,_=machine.run(3_000_000,record=False)
        if stop.stop_status!=40:raise ValueError('game tick not reached')
        def read(name,size):return int.from_bytes(machine.read(symbols['_'+name]['address'],size),'little')
        rows.append({'tick':k,'scene':read('ngc_scene',1),'x':read('act_x',2),'y':read('act_y',2),
                     'physical_frame':stop.frame_count})
        if k in (30,130,230,330,430):
            write_frame(root/f'tick-{k}.ppm',machine.framebuffer());captures.add(k)
            from tools.music_rom import png
            (root/f'tick-{k}.png').write_bytes(png(list(machine.framebuffer())))
        held=32 if k in (100,200,300,400) else (8 if k%100<70 else 0)
        machine.write(0xB0,bytes([held]));machine.set_breakpoints([])
        machine.run(1,record=False);machine.set_breakpoints([tick])
    visits=[r['scene'] for i,r in enumerate(rows) if i==0 or r['scene']!=rows[i-1]['scene']]
    result={'visits':visits,'types':TYPES,'features':manifest['features'],'rom_bytes':len(rom),
            'ram_bytes':build['link_audit']['static_ram_bytes'],'rows':rows,
            'core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'silicon_timing':{'word_wait':10,'bios_wait':8},'ram':'noise',
            'expected_visits':cycle,'passed':visits==cycle}
    write_json(root/'proof.json',result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    for name in ('toolchain','emulator','bios'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--start',type=int,choices=range(4),default=0)
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios,a.start)
    print(json.dumps({k:r[k] for k in ('visits','passed','rom_bytes','ram_bytes')}))
    return 0 if r['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
