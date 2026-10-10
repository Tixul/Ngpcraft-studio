"""M4 Actor Push: authored collision arena, released assets, native ROM proof.

The arena changes only this disposable derived project. The original Sample
Town is untouched. Endpoint oracle uses the published bounds formula, no
generated C; execution is observed externally without instrumentation.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.lab_m3_gate import encode_collisions
from tools.probe_rom import power_on_ram, verified_tick, write_frame

PLAN = [(d, slide) for slide in (False, True) for d in ('right', 'left', 'down', 'up')]
START = (104, 104)

def project_case(root):
    root.mkdir(parents=True, exist_ok=True)
    project = root / 'project'
    derive(GBS2, project, 'sample_town', (12, 12), fade=False)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    folder = path.parent
    paths = list((folder / 'actors').glob('*.gbsres'))
    template = json.loads(paths[0].read_text(encoding='utf-8'))
    for p in paths:
        assert p.resolve().is_relative_to(project.resolve())
        p.unlink()
    scene = json.loads(path.read_text(encoding='utf-8'))
    scene.update(name='M4 Push collision arena', script=[], autoFadeSpeed=None)
    grid = [0] * (scene['width'] * scene['height'])
    for y in range(scene['height']):
        for x in range(scene['width']):
            if x in (8, 18) or y in (8, 18):
                grid[y * scene['width'] + x] = 15
    scene['collisions'] = encode_collisions(grid)
    script = [ev('player-hide','EVENT_ACTOR_HIDE',actorId='player'),
              ev('player-no-collision','EVENT_ACTOR_COLLISIONS_DISABLE',actorId='player')]
    for k, (direction, slide) in enumerate(PLAN + [('right', False)], 1):
        script += [ev(f'pos-{k}','EVENT_ACTOR_SET_POSITION',actorId='$self$',
                      x=num(START[0]),y=num(START[1]),units='pixels'),
                   ev(f'dir-{k}','EVENT_ACTOR_SET_DIRECTION',actorId='player',
                      direction={'type':'direction','value':direction}),
                   ev(f'mark-{k}','EVENT_SET_VALUE',variable='20',value=num(k)),
                   ev(f'push-{k}','EVENT_ACTOR_PUSH',**{'continue':slide}),
                   ev(f'done-{k}','EVENT_SET_VALUE',variable='21',value=num(k)),
                   ev(f'pause-{k}','EVENT_WAIT',units='frames',frames=num(8))]
        if k == len(PLAN):
            script += [ev('obstacle-pos','EVENT_ACTOR_SET_POSITION',actorId='push-obstacle',
                          x=num(128),y=num(104),units='pixels'),
                       ev('obstacle-active','EVENT_ACTOR_ACTIVATE',actorId='push-obstacle')]
    actor = copy.deepcopy(template)
    actor.update(id='push-actor',symbol='actor_push',name='Pushed actor',_index=0,prefabId='',
                 coordinateType='pixels',x=104,y=104,direction='down',moveSpeed=1,
                 persistent=True,collisionGroup='',collisionExtraFlags=[],
                 script=[],startScript=script,updateScript=[],hit1Script=[],hit2Script=[],hit3Script=[])
    obstacle = copy.deepcopy(actor)
    obstacle.update(id='push-obstacle',symbol='actor_push_obstacle',name='Obstacle',_index=1,
                    x=0,y=0,startScript=[],persistent=False)
    write_json(folder/'actors/push.gbsres',actor)
    write_json(folder/'actors/obstacle.gbsres',obstacle)
    write_json(path,scene)
    return project

def endpoints(engine):
    left,right,top,bottom = engine.bounds[1]
    result = []
    for direction, slide in PLAN:
        x,y = (v * 32 for v in START)
        if slide:
            if direction == 'right': x = 18*256-right-1
            if direction == 'left': x = 9*256-left
            if direction == 'down': y = 18*256-bottom-1
            if direction == 'up': y = 9*256-top
        else:
            if direction == 'right': x += 512
            if direction == 'left': x -= 512
            if direction == 'down': y += 512
            if direction == 'up': y -= 512
        result.append([x,y])
    other_left = engine.bounds[2][0]
    result.append([128*32+other_left-right-1,104*32])
    return result

def run(out, toolchain, emulator, bios, witness=None, frames=450, case_builder=None, endpoint_oracle=None):
    if out.exists():
        raise ValueError('output must be new')
    project_path = (case_builder or project_case)(out)
    manifest = export_project(project_path,out/'export')
    if witness:
        path = out/'export/src/scene_actors.c'
        source = path.read_text(encoding='utf-8')
        old,new = ('    return act_dir[i];','    return 0u;') if witness == 'direction' else (
            '        hit = overlapping(i);  /* index+1; 0 means no hit */',
            '        hit = 0u;  /* witness: ignore actors */')
        assert source.count(old) == (1 if witness == 'direction' else 2)
        tmp=path.with_suffix('.tmp');tmp.write_text(source.replace(old,new),encoding='utf-8');tmp.replace(path)
        manifest['sha256']['src/scene_actors.c']=hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(out/'export/export.json',manifest)
    build = build_rom(out/'export',toolchain)
    loaded = load_project(project_path)
    engine = build_engine_scene(loaded,loaded.scenes[0])
    expected = (endpoint_oracle or endpoints)(engine)
    symbols = build['link_audit']['public_symbols']
    def address(name):return symbols['_'+name]['address']
    # Import our tools before adding the emulator's own tools package.
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    rom=(out/'export/rom/game.ngc').read_bytes();bios_data=Path(bios).read_bytes()
    machine=native.NativeMachine(rom,bios=bios_data)
    machine.set_timing_silicon(word_wait=10,bios_wait=8)
    machine.reset(bios_handoff=True);power_on_ram(machine,'noise');machine.write(0xA000,bytes(8192))
    machine.set_breakpoints([verified_tick(rom,build)])
    def word(a):return int.from_bytes(machine.read(a,2),'little')
    memory=address('script_memory')
    markers={v:manifest['variables'][f'VAR_VARIABLE_{v}'] for v in (20,21)}
    rows=[];observed={};previous=None;shots=[]
    for k in range(frames+1):
        stop,_=machine.run(3_000_000,record=False)
        if stop.stop_status != 40:raise ValueError('game tick not reached')
        row={'tick':k,'position':[word(address('act_x')+2),word(address('act_y')+2)],
             'phase':word(memory+2*markers[20]),'done':word(memory+2*markers[21]),
             'frames_between':None if previous is None else stop.frame_count-previous}
        rows.append(row);previous=stop.frame_count
        if row['done'] and row['done'] not in observed:
            observed[row['done']]=row['position']
            # Current frame commits the previous computed state. Capture a
            # rest frame below, after the endpoint has reached the screen.
        if k>1 and row['done'] and rows[-2]['done']==row['done'] and row['done'] not in shots:
            shots.append(row['done']);write_frame(out/f'shot-{row["done"]}.ppm',machine.framebuffer())
        if row['done']==9 and k>1 and rows[-2]['done']==9:break
    differences=[{'case':i,'expected':xy,'actual':observed.get(i)}
                 for i,xy in enumerate(expected,1) if observed.get(i)!=xy]
    motion_differences=[]
    for i,end in enumerate(expected,1):
        actual=[]
        for row in rows:
            if row['phase']==i and row['done']<=i:
                if not actual or actual[-1]!=row['position']:
                    actual.append(row['position'])
                if row['done']==i:break
        start=[v*32 for v in START]
        axis=0 if end[0]!=start[0] else 1
        sign=1 if end[axis]>start[axis] else -1
        wanted=[start]
        while wanted[-1]!=end:
            xy=list(wanted[-1]);xy[axis]+=32*sign
            if sign*(xy[axis]-end[axis])>0:xy[axis]=end[axis]
            wanted.append(xy)
        if actual!=wanted:motion_differences.append({'case':i,'expected':wanted,'actual':actual})
    result={'expected_endpoints':expected,'observed_endpoints':observed,'mismatches':differences,
            'motion_mismatches':motion_differences,
            'completed_cases':len(observed),'ticks':len(rows)-1,
            'dropped_frames':[r['tick'] for r in rows[4:] if r['frames_between']!=1],
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'bios_sha256':hashlib.sha256(bios_data).hexdigest(),
            'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'silicon_timing':{'word_wait':10,'bios_wait':8},'features':manifest['features']}
    write_json(out/'comparison.json',result);write_json(out/'rows.json',rows)
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--toolchain',required=True)
    parser.add_argument('--emulator',required=True)
    parser.add_argument('--bios',required=True)
    args=parser.parse_args()
    result=run(args.out,args.toolchain,args.emulator,args.bios)
    print(json.dumps(result,indent=2))
    if result['mismatches'] or result['motion_mismatches']:raise SystemExit(1)

if __name__=='__main__':main()
