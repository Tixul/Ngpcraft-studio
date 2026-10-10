"""R-PF workload: original artwork, ordinary scripts, external native timing."""
import argparse, copy, json, subprocess, sys
from pathlib import Path
ENGINE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ENGINE))
from tools.platform_oracle import project_case,rom_trace,reference_run,compare,SCHEDULE
from tools.demo_gbs_scene import write_json
from tools.demo_actor_moves import ev,num,sequence
from tools.gbvm_actors import ScriptScheduler,engine_frames,plain,move_ops,op_idle,op_jump,ATTR_H_FIRST
from ngpcraft.project import load_project
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_assets import sprite_palettes
from tools.probe_stream import Expected
from tools.check_text import ppm
from tools.gbvm_ui import rgb

def fixture(root,freeze_outside=False):
    project=project_case(root,platform=('platform',32,40,104))
    folder=next((project/'project/scenes').rglob('scene.gbsres')).parent
    path=folder/'actors/m3_platform.gbsres'
    template=json.loads(path.read_text(encoding='utf-8'));path.unlink()
    resources=load_project(project)
    sheets={s['name']:s['id'] for _,s in resources.by_type['sprite']}
    plans=[]
    for k in range(15):
        role='enemy' if k<6 else 'platform' if k<14 else 'hud'
        x,y=((24+k*20,104) if k<6 else
             (96,104) if k==6 else (32+(k-6)*28,80-(k-6)*8) if k<10 else
             (768+(k-10)*32,96) if k<14 else (16,16))
        targets=[[x+8,y],[x-8,y]] if k<14 else []
        actor=copy.deepcopy(template)
        actor.update(id=f'rpf-{k}',name=f'R-PF {role} {k}',symbol=f'actor_rpf_{k}',_index=k,
            prefabId='',prefabScriptOverrides={},spriteSheetId=sheets[{'enemy':'turnip','platform':'ice','hud':'hearts'}[role]],
            x=x,y=y,coordinateType='pixels',isPinned=k==14,persistent=True,
            moveSpeed=1,animSpeed=3 if k<6 else 255,collisionGroup='',
            collisionExtraFlags=['platform'] if role=='platform' else [],
            script=[],hit1Script=[],hit2Script=[],hit3Script=[],updateScript=[
                ev(f'rpf-{k}-{i}','EVENT_ACTOR_MOVE_TO',actorId='$self$',x=num(px),y=num(py),
                   units='pixels',collideWith=[],moveType='horizontal',lockDirection=[])
                for i,(px,py) in enumerate(targets)])
        if freeze_outside and k==10:actor['updateScript']=[]
        write_json(folder/f'actors/rpf_{k:02d}.gbsres',actor)
        plans.append({'id':actor['id'],'role':role,'start':[x,y],'targets':targets,'initially_outside':10<=k<14})
    write_json(root/'fixture.json',{'workers':plans,'hud':'fixed original hearts, no HP mechanic',
        'enemy':'ordinary moving actors, no implicit combat','platforms':8,'enemies':6})
    return project

def worker_compare(engine,rows,plans):
    indices={p['id']:i+1 for i,p in enumerate(plans)}
    factories={}
    for p in plans:
        if not p['targets']:continue
        def factory(p=p):
            ops=plain(1)
            for x,y in p['targets']:
                ops+=plain(2)+move_ops(indices[p['id']],x<<5,y<<5,ATTR_H_FIRST,
                                      sequence(False,x,y,'horizontal',[]))
            return ops+[op_idle(),op_jump(1)]
        factories[indices[p['id']]]=factory
    scheduler=ScriptScheduler(plain(2),factories)
    expected={}
    def record(scene,actors,tick):
        expected[tick]=[[a.x,a.y] for a in actors[1:]]
    pictures=engine_frames(engine,len(rows),thread=scheduler,after_render=record)
    # Entry k exposes scheduler tick k (tick 1 consumed locked INIT).
    errors=[]
    for tick in range(2,len(rows)):
        actual=rows[tick]['actors'][:len(plans)]
        if actual!=expected[tick]:
            errors.append({'tick':tick,'expected':expected[tick],'actual':actual})
    return errors,sorted(set(i for _,i in scheduler.starts)),pictures

def pixel_proof(root,project,engine,rows,pictures,probe):
    """Original PNGs, independent worker scripts/animation; player canvas excluded.

    Camera is observed, not an independent camera proof. All workers persist;
    their far sprites cannot enter this measured camera range. Pinning is
    represented in viewport coordinates, independent of the map scroll.
    """
    bg=engine.background_source
    temp=root/'scene.rgb444.tmp'
    temp.write_bytes(b''.join(v.to_bytes(2,'little') for v in bg.pixels));temp.replace(root/'scene.rgb444')
    draws=[]
    for k,(position,shown) in enumerate(pictures):
        scroll=probe['scroll_samples'][min(k+1,len(probe['scroll_samples'])-1)]
        draws.append([[name,wx+scroll[0] if name=='hearts' else wx,
                       wy+scroll[1] if name=='hearts' else wy,anim,frame]
                      for name,wx,wy,anim,frame in shown[1:]])
    oracle=Expected(root,{'size_px':[bg.width,bg.height],'view':[4,144],
        'actors':draws,'project':str(project.root),'default_sprite_mode':project.settings['spriteMode'],
        'sprite_palettes':[list(p) for p in sprite_palettes(project,project.scenes[0])]})
    raw=next(s for _,s in project.by_type['sprite'] if s['id']==engine.sheet_ids[0])
    checked=[];errors=[]
    for shot in sorted((root/'probe').glob('shot-*.ppm')):
        k=int(shot.stem.split('-')[-1])
        if k<2:continue
        step=probe['game_time_samples'][k-1]
        sx,sy=probe['scroll_samples'][k]
        expected=oracle.picture(sx,sy,step)
        _,_,actual=ppm(shot)
        pose=rows[k-1]
        ox,oy=oracle.origin(raw['name'],(pose['x']>>5)-sx,(pose['y']>>5)-sy)
        differences=0;pixels=0
        for y in range(152):
            for x in range(160):
                if ox<=x<ox+raw['canvasWidth'] and oy+4<=y<oy+4+raw['canvasHeight']:continue
                i=y*160+x;pixels+=1
                differences+=actual[3*i:3*i+3]!=bytes(rgb(expected[i]))
        checked.append({'frame':k,'pixels':pixels})
        if differences:errors.append({'frame':k,'pixels':differences})
    result={'checked':checked,'errors':errors,'scope':'source PNG background/workers/HUD, player canvas excluded, observed scroll'}
    write_json(root/'pixel-proof.json',result)
    return result

def run(root,toolchain,emulator,bios,clang,frames=720,freeze_outside=False):
    if root.exists():raise ValueError('output must be new')
    root.mkdir(parents=True)
    project=fixture(root,freeze_outside)
    manifest=export_project(project,root/'export');build_rom(root/'export',Path(toolchain))
    write_json(root/'inputs.json',SCHEDULE)
    loaded=load_project(project)
    rows=rom_trace(root/'export',emulator,bios,frames,SCHEDULE)
    ref=reference_run(root,root/'export',loaded,rows,clang)['rows']
    errors=compare(rows,ref)
    plans=json.loads((root/'fixture.json').read_text())['workers']
    engine=build_engine_scene(loaded,loaded.scenes[0])
    workers,activated,pictures=worker_compare(engine,rows,plans)
    write_json(root/'state-proof.json',{'physics_errors':errors,'worker_errors':workers,'rom':rows,'reference':ref})
    subprocess.run([sys.executable,'-B',str(ENGINE/'tools/probe_gbvm.py'),str(root/'export'),
        '--emulator',str(emulator),'--bios',str(bios),'--out',str(root/'probe'),'--frames',str(frames),
        '--shots',','.join(str(k) for k in (2,40,100,200,400,600,720) if k<=frames),'--inputs',str(root/'inputs.json'),
        '--sprite-mask-trace','--frame-timing','--duty-cycle','0.25'],check=True,cwd=ENGINE)
    probe=json.loads((root/'probe/probe.json').read_text())
    pixels=pixel_proof(root,loaded,engine,rows,pictures,probe)
    timing=[t for t in probe['frame_timings'] if t['frame']>=2]
    physical=sum(t['frames_to_next_entry'] for t in timing)
    walking=[t for t in timing if 30<=t['frame']<60]
    walk_physical=sum(t['frames_to_next_entry'] for t in walking)
    grounded_walk=[]
    for t in timing:
        tick=t['frame'];held=0
        for first,last,mask in SCHEDULE:
            if first<=tick<last:held|=mask
        if held in (4,8) and rows[tick+1]['plat_grounded']:
            grounded_walk.append(t)
    ground_physical=sum(t['frames_to_next_entry'] for t in grounded_walk)
    outside={p['id']:len(set(tuple(r['actors'][i]) for r in rows[2:]))
             for i,p in enumerate(plans) if p['initially_outside']}
    result={'ticks':frames,'physics_mismatch_ticks':len(errors),'worker_mismatch_ticks':len(workers),
        'first_worker_error':workers[:1],'activated_update_indices':activated,'outside_distinct_positions':outside,
        'freeze_outside_witness':freeze_outside,'pixel_checked_frames':len(pixels['checked']),
        'pixel_mismatch_frames':len(pixels['errors']),
        'attached_ticks':sum(bool(r['plat_is_actor_attached']) for r in rows[2:]),
        'fps_nominal60':60*len(timing)/physical,'steady_entries':len(timing),'physical_frames':physical,
        'walking_30_60':{'entries':len(walking),'physical_frames':walk_physical,
            'grounded_ticks':sum(bool(rows[t['frame']]['plat_grounded']) for t in walking),
            'fps_nominal60':60*len(walking)/walk_physical if walking else None,
            'missed_entries':sum(t['frames_to_next_entry']!=1 for t in walking)},
        'grounded_walking':{'entries':len(grounded_walk),'physical_frames':ground_physical,
            'fps_nominal60':60*len(grounded_walk)/ground_physical if grounded_walk else None,
            'missed_ticks':[t['frame'] for t in grounded_walk if t['frames_to_next_entry']!=1],
            'scope':'only raw LEFT/RIGHT held, no A/B, grounded after the tick'},
        'missed_entries':sum(t['frames_to_next_entry']!=1 for t in timing),
        'commits_over_vblank':sum(t['overran_vblank'] for t in timing),
        'max_oam_used':max(len(s)//4 for s in probe['sprite_trace'][2:]),
        'features':manifest['features'],'native':{k:probe[k] for k in ('rom_sha256','bios_sha256',
            'emulator_core_sha256','emulator_binding_sha256','silicon_timing','ram','ram_seed')},
        'scope':'6 moving enemies (no combat), 8 moving platforms (4 initially outside), fixed pinned HUD; source assets unchanged'}
    write_json(root/'comparison.json',result)
    print(json.dumps(result,indent=2),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('out','toolchain','emulator','bios','clang'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--frames',type=int,default=720)
    p.add_argument('--freeze-outside',action='store_true')
    a=p.parse_args();r=run(a.out,a.toolchain,a.emulator,a.bios,a.clang,a.frames,a.freeze_outside)
    raise SystemExit(bool(r['physics_mismatch_ticks'] or r['pixel_mismatch_frames'] or (not r['worker_mismatch_ticks'] if a.freeze_outside else r['worker_mismatch_ticks'])))
