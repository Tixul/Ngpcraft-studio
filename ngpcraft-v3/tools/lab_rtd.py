"""R-TD: released GB Studio resources, ordinary scripts, external native proof.

Engine and GB Studio reference are read-only during the lab. No runtime profiling
code is inserted. Every fixture/export/report is new and workspace-local.
"""
from pathlib import Path
import argparse, copy, hashlib, json, subprocess, sys
ENGINE=Path(__file__).resolve().parents[1]

sys.path.insert(0,str(ENGINE))
from tools.demo_second_background import project_case
from tools.demo_scene_switch import ev, num, set_script
from tools.demo_gbs_scene import write_json
from tools.demo_actor_moves import sequence
from tools.gbvm_actors import ScriptScheduler, engine_frames, plain, move_ops, op_idle, op_jump, ATTR_H_FIRST
from tools.gbvm_topdown import Topdown
from tools.probe_stream import Expected
from tools.check_text import ppm
from tools.gbvm_ui import rgb
from ngpcraft.project import load_project
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_assets import sprite_palettes

def start(k):
    return (216+(k%5)*24,228+(k//5)*48) if k<10 else (416+(k%5)*12,416+(k//5%4)*16)

def plan(k):
    x,y=start(k)
    return ((x+8,y),(x-8,y))

def project_fixture(root,count):
    assert count in (10,30)
    if root.exists(): raise ValueError('output must be new')
    root.mkdir()
    project,_=project_case(root,text=False,transitions=False)
    # Only the main 64x64 map is used for the R-TD baseline.
    write_json(project/'ngpcraft.json',{'schema':'ngpcraft-project/1',
                                      'ngpc':{'actor_capacity':30} if count==30 else {}})
    folder=next(p.parent for p in (project/'project/scenes').rglob('scene.gbsres'))
    paths=sorted(folder.rglob('actors/*.gbsres'))
    template=json.loads(paths[0].read_text(encoding='utf-8'))
    for path in paths:
        assert path.resolve().is_relative_to(project.resolve())
        path.unlink()
    loaded=load_project(project)
    sheet=(loaded.scenes[0]['playerSpriteSheetId'] if 'playerSpriteSheetId' in loaded.scenes[0]
           else loaded.settings['defaultPlayerSprites']['TOPDOWN'])
    for k in range(count):
        actor=copy.deepcopy(template)
        x,y=start(k)
        actor.update(id=f'rtd-actor-{k}',name=f'R-TD actor {k}',symbol=f'actor_rtd_{k}',_index=k,
                     prefabId='',spriteSheetId=sheet,isPinned=False,persistent=False,
                     coordinateType='pixels',x=x,y=y,direction='right',animSpeed=3,moveSpeed=1,
                     collisionGroup='',script=[],updateScript=[])
        for i,(px,py) in enumerate(plan(k) if k<10 else ()):
            actor['updateScript'].append(ev(f'rtd-{k}-{i}','EVENT_ACTOR_MOVE_TO',actorId='$self$',
                x=num(px),y=num(py),units='pixels',collideWith=[],moveType='horizontal',lockDirection=[]))
        write_json(folder/f'actors/rtd_actor_{k:02d}.gbsres',actor)
    scene_path=folder/'scene.gbsres'
    scene=json.loads(scene_path.read_text(encoding='utf-8'))
    scene.update(name=f'R-TD {count}',autoFadeSpeed=None,script=[],collisions='')
    write_json(scene_path,scene)
    set_script(folder,[])
    path=project/'project/settings.gbsres'
    settings=json.loads(path.read_text(encoding='utf-8'))
    settings.update(startX=32,startY=32,startDirection='right',startMoveSpeed=1,startAnimSpeed=3)
    write_json(path,settings)
    fields_path=project/'project/engine_field_values.gbsres'
    fields=json.loads(fields_path.read_text(encoding='utf-8'))
    fields['engineFieldValues']=[f for f in fields['engineFieldValues'] if f['id']!='topdown_grid']
    fields['engineFieldValues'].append({'id':'topdown_grid','value':8})
    write_json(fields_path,fields)
    return project

def build(root,count,toolchain,witness=None):
    project=project_fixture(root,count)
    manifest=export_project(project,root/'export')
    if witness:
        path=root/'export/src/scene_actors.c'
        source=path.read_text(encoding='utf-8')
        old='    ngc_actors_update(locked);'
        # Witness changes one compiled source position, not the oracle.
        marker='    ngc_sprites_begin();'
        assert source.count(marker)==1
        source=source.replace(marker,'    act_x[1] += 32u;\n'+marker)
        temp=path.with_suffix('.tmp');temp.write_text(source,encoding='utf-8');temp.replace(path)
        manifest['sha256']['src/scene_actors.c']=hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(root/'export/export.json',manifest)
    build_rom(root/'export',Path(toolchain))
    # Small loop around three camera positions: every worker stays visible.
    schedule=[]
    for first in range(40,2000,128):
        for offset,mask in ((0,8),(24,2),(48,4),(72,1)):
            schedule.append([first+offset,first+offset+16,mask])
    write_json(root/'inputs.json',schedule)
    write_json(root/'fixture.json',{'actors':count,'visible_workers':10,'outside_workers':count-10,
                                  'map_tiles':[64,64],'pinned_workers':0,'witness':witness,
                                  'worker_start':[start(k) for k in range(count)]})

def compare(root):
    project=load_project(root/'project')
    scene=project.scenes[0]
    engine=build_engine_scene(project,scene)
    fixture=json.loads((root/'fixture.json').read_text())
    probe=json.loads((root/'probe/probe.json').read_text())
    indices={a['id']:i+1 for i,a in enumerate(scene['actors'])}
    factories={}
    for k in range(fixture['visible_workers']):
        def factory(k=k):
            ops=plain(1)
            targets = fixture['worker_plan'][k] if 'worker_plan' in fixture else plan(k)
            for x,y in targets:
                ops+=plain(2)+move_ops(indices[f'rtd-actor-{k}'],x<<5,y<<5,ATTR_H_FIRST,
                                      sequence(False,x,y,'horizontal',[]))
            return ops+[op_idle(),op_jump(1)]
        factories[indices[f'rtd-actor-{k}']]=factory
    scheduler=ScriptScheduler(plain(2),factories)
    player=Topdown()
    sequence_frames=engine_frames(engine,max(probe['game_time_samples'])+10,thread=scheduler,player=player,
        inputs=lambda tick:probe['input_samples'][min(tick-1,probe['frames'])])
    source=engine.background_source
    temp=root/'scene.rgb444.tmp';temp.write_bytes(b''.join(v.to_bytes(2,'little') for v in source.pixels));temp.replace(root/'scene.rgb444')
    data={'size_px':[source.width,source.height],'view':[0,152],
          'actors':[draws for _,draws in sequence_frames],'project':str(project.root),
          'default_sprite_mode':project.settings['spriteMode'],
          'sprite_palettes':[list(p) for p in sprite_palettes(project,scene)]}
    oracle=Expected(root,data)
    errors=[];scroll_errors=[];visible_errors=[]
    for k in range(2,probe['frames']+1):
        step=probe['game_time_samples'][k-1]
        position,draws=sequence_frames[step]
        if probe['scroll_samples'][k]!=list(position):
            scroll_errors.append({'frame':k,'expected':position,'actual':probe['scroll_samples'][k]})
        if len(draws)!=fixture['visible_workers']+1:
            visible_errors.append({'frame':k,'drawn_actors':len(draws)})
        for name,wx,wy,anim,frame in draws[1:]:
            ox,oy=oracle.origin(name,wx-position[0],wy-position[1])
            if any(value and not(0<=ox+px<160 and 0<=oy+py<152)
                   for (px,py),(value,pal) in oracle.frames[(name,anim,frame)].items()):
                visible_errors.append({'frame':k,'worker_partially_outside_view':True})
        expected=b''.join(bytes(rgb(c)) for c in oracle.picture(*position,step))
        _,_,actual=ppm(root/f'probe/shot-{k:04d}.ppm')
        mismatches=sum(actual[i:i+3]!=expected[i:i+3] for i in range(0,len(expected),3))
        if mismatches:errors.append({'frame':k,'pixels':mismatches})
    timing=[t for t in probe['frame_timings'] if t['frame']>=2]
    physical=sum(t['frames_to_next_entry'] for t in timing)
    missed=[{'frame':t['frame'],'physical_frames':t['frames_to_next_entry']} for t in timing if t['frames_to_next_entry']!=1]
    result={'count':fixture['actors'],'entry_frames':probe['frames'],'pixel_errors':errors,
            'scroll_errors':scroll_errors,'visible_errors':visible_errors,'pixels_passed':not(errors or scroll_errors or visible_errors),
            'cadence_passed':not missed,'steady_entries':len(timing),'physical_frames':physical,
            'effective_fps_nominal60':60*len(timing)/physical,'missed_entries':missed,
            'commits_over_vblank':sum(t['overran_vblank'] for t in timing),
            'commits_started_outside_vblank':sum(not t['started_in_vblank'] for t in timing),
            'camera_positions':sorted(set(tuple(p) for p in probe['scroll_samples'][2:])),
            'activated_script_indices':sorted(set(index for _,index in scheduler.starts)),
            'native':{k:probe[k] for k in ('rom_sha256','bios_sha256','emulator_core_sha256','emulator_binding_sha256','silicon_timing','ram','ram_seed')},
            'scope':'tick entries, source PNG oracle; missed physical frames explicitly recorded; no ROM profiling'}
    write_json(root/'comparison.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('camera_positions','missed_entries','native')},indent=2),flush=True)
    return result

def observe(root,emulator,bios,frames=600,input_refresh_trace=False):
    subprocess.run([sys.executable,'-B',str(ENGINE/'tools/probe_gbvm.py'),str(root/'export'),
        '--emulator',str(emulator),'--bios',str(bios),
        '--out',str(root/'probe'),'--frames',str(frames),'--shots',','.join(map(str,range(1,frames+1))),
        '--inputs',str(root/'inputs.json'),'--sprite-mask-trace','--frame-timing','--duty-cycle','0.25',
        *(['--input-refresh-trace'] if input_refresh_trace else [])],check=True,cwd=ENGINE)
    return compare(root)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--actors',type=int,choices=(10,30),required=True)
    p.add_argument('--frames',type=int,default=600)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--toolchain',type=Path,required=True)
    p.add_argument('--emulator',type=Path,required=True)
    p.add_argument('--bios',type=Path,required=True)
    p.add_argument('--witness',action='store_true')
    args=p.parse_args()
    build(args.out,args.actors,args.toolchain,args.witness)
    result=observe(args.out,args.emulator,args.bios,args.frames)
    success=(not result['pixels_passed']) if args.witness else (result['pixels_passed'] and result['cadence_passed'])
    return 0 if success else 1
if __name__=='__main__':raise SystemExit(main())
