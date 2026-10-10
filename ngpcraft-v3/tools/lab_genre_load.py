"""R-SH diagnostic: 12 enemies + player + 16 live projectiles, external timing.

Pool capacity 17 is a laboratory builder parameter, not an author setting.
Original gbs2 assets are preserved; an OAM overflow is reported, not qualified.
"""
import argparse, hashlib, json, sys
from pathlib import Path
from unittest.mock import patch
from tools.genre_oracle import project_case
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json
from tools.lab_projectile_events import shot
from tools.probe_rom import verified_tick, power_on_ram
from tools.music_rom import png
from ngpcraft.project import load_project
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from ngpcraft.scene_build import build_engine_scene

def prepare(root):
    p,_=project_case(root,'shmup',keep_actors=False,fields={'shooter_scroll_speed':0},
                     actors=[(40+24*(i%5),30+28*(i//5),'1',None) for i in range(12)])
    sprites={s['name']:s['id'] for _,s in load_project(p).by_type['sprite']}
    for path in (p/'project/scenes').rglob('actors/*.gbsres'):
        a=json.loads(path.read_text(encoding='utf-8'));a['spriteSheetId']=sprites['enemy_ship'];write_json(path,a)
    path=next((p/'project/scenes').rglob('scene.gbsres'));s=json.loads(path.read_text(encoding='utf-8'))
    fire=ev('r-sh-fire-input','EVENT_SET_INPUT_SCRIPT',input=['a'],override=True)
    fire['children']={'true':[ev('r-sh-fire-'+str(i),'EVENT_LAUNCH_PROJECTILE',
        **shot(sprites['bullet_right'],actorId='player',speed=0,lifeTime=5,collisionMask=['1'],
               destroyOnHit=False,x=10+(i%8)*14,y=-64+(i//8)*12)) for i in range(16)]}
    s['script']=[fire];write_json(path,s)
    def builder(*args,**kw):
        kw['projectile_capacity']=17
        return build_engine_scene(*args,**kw)
    with patch('ngpcraft.gbvm.emit.build_engine_scene',builder):
        manifest=export_project(p,root/'export')
    return manifest

def run(root,toolchain,emulator,bios,frames=150):
    if root.exists():raise ValueError('output must be new')
    root.mkdir(parents=True);manifest=prepare(root);build=build_rom(root/'export',Path(toolchain))
    from tools.platform_oracle import c_values
    records=c_values((root/'export/src/scene_sprites.c').read_text(encoding='utf-8'),'spr_rec')
    if set(records[::8]) != {4}:raise ValueError('R-SH budget expects the unchanged four-sprite frames')
    rom=(root/'export/rom/game.ngc').read_bytes();tick=verified_tick(rom,build)
    symbols=build['link_audit']['public_symbols'];launch=symbols['_ngc_projectile_launch']['address']
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(Path(emulator).resolve()):raise ValueError('different native binding')
    m=native.NativeMachine(rom,bios=Path(bios).read_bytes());m.set_timing_silicon(word_wait=10,bios_wait=8)
    m.reset(bios_handoff=True);power_on_ram(m,'noise');m.set_breakpoints([tick,launch])
    rows=[];launches=0
    while len(rows)<=frames:
        stop,_=m.run(3_000_000,record=False)
        if stop.stop_status!=40:raise ValueError('breakpoint not reached')
        if stop.stop_pc==launch:launches+=1
        else:
            k=len(rows);rows.append({'tick':k,'frame':stop.frame_count,'cycles':stop.total_cycles,
                                     'launches':launches})
            m.write(0xB0,bytes([16 if k==10 else 0]))
            if k in (30,80,140):(root/f'tick-{k}.png').write_bytes(png(list(m.framebuffer())))
        m.set_breakpoints([]);m.run(1,record=False);m.set_breakpoints([tick,launch])
    intervals=[rows[i]['frame']-rows[i-1]['frame'] for i in range(2,len(rows))]
    result={'actors':13,'enemy_count':12,'launch_calls':launches,'pool_capacity':17,
            'hardware_sprite_demand':(13+16)*4,'hardware_sprite_limit':64,'oam_overflow':True,
            'qualified_r_sh':False,'reason':'Original 4-sprite assets require 116 slots; 52 exceed OAM.',
            'physical_frame_intervals':sorted(set(intervals)),'ticks_with_interval_above_one':[i+2 for i,v in enumerate(intervals) if v>1],
            'rom_bytes':len(rom),'ram_bytes':build['link_audit']['static_ram_bytes'],
            'core_sha256':hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
            'silicon_timing':{'word_wait':10,'bios_wait':8},'ram':'noise','debugger_only':True,'rows':rows}
    write_json(root/'proof.json',result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    for name in ('toolchain','emulator','bios'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios)
    print(json.dumps({k:v for k,v in r.items() if k!='rows'},indent=2))
    return 0 if r['launch_calls']==16 else 1

if __name__=='__main__':raise SystemExit(main())
