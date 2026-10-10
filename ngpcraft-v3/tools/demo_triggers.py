"""Console proof: entering a zone hides its neighbour, leaving shows it again."""
import argparse
import hashlib
import json
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_assets import background_pixels, sprite_palettes
from ngpcraft.background_stream import view_of
from ngpcraft.toolchain import build_rom
from tools.demo_topdown import project_case as topdown_project
from tools.demo_actor_moves import ev, num, ANGRY
from tools.demo_gbs_scene import write_json
from tools.demo_backgrounds import write_new
from tools.gbvm_actors import plain, op_wait, engine_frames, ScriptScheduler
from tools.gbvm_topdown import Topdown, held_at
from tools.gbvm_triggers import Triggers

SCHEDULE = [(20,180,8),(240,410,4),(440,590,8)]
CONSOLE = [(120,152,8),(400,416,8),(800,816,8),(1200,1232,4),(1600,1632,4)]

def project_case(root,pause=20):
    p = topdown_project(root)
    path=p/'project/settings.gbsres'
    settings=json.loads(path.read_text());settings['startX']=8;write_json(path,settings)
    actors=[]
    for path in (p/'project/scenes').rglob('actors/*.gbsres'):
        a=json.loads(path.read_text());a['script']=[]
        if a['id']==ANGRY:
            a['x'],a['y']=15,18
        write_json(path,a);actors.append(a)
    second=next(a for a in actors if a['id']!=ANGRY and a['x']==18)
    templates=list((p/'project/scenes').rglob('triggers/*.gbsres'))
    template=json.loads(templates[0].read_text())
    for i,actor in enumerate((ANGRY,second['id'])):
        tr={**template,'id':f'proof-trigger-{i}','symbol':f'trigger_proof_{i}','name':f'Proof {i}',
            'prefabId':'','prefabScriptOverrides':{},'x':12+2*i,'y':20,'width':1,'height':1}
        for key,command in (('script','EVENT_ACTOR_HIDE'),('leaveScript','EVENT_ACTOR_SHOW')):
            tr[key]=[ev(f'{i}-{key}',command,actorId=actor),ev(f'{i}-{key}-wait','EVENT_WAIT',units='frames',frames=num(pause))]
        write_json(templates[0].parent/f'proof_{i}.gbsres',tr)
    return p

def build_case(root,toolchain,pause=20,schedule=None,mutate=None):
    if schedule is None: schedule=SCHEDULE
    p=project_case(root,pause)
    manifest=export_project(p,root/'export')
    if mutate:
        rel,old,new=mutate;path=root/'export'/rel;s=path.read_text();assert s.count(old)==1
        s=s.replace(old,new);write_new(path.with_suffix('.mutated'),s.encode());path.with_suffix('.mutated').replace(path)
        manifest['sha256'][rel]=hashlib.sha256(s.encode()).hexdigest();write_json(root/'export/export.json',manifest)
    build=build_rom(root/'export',toolchain)
    project=load_project(p);scene=project.scenes[0];engine=build_engine_scene(project,scene)
    scheduler=ScriptScheduler(plain(4)+[op_wait(12)],{})
    regions=[];targets=[]
    from ngpcraft.scene_triggers import build_triggers
    for tr in build_triggers(project):
        a=tr.trigger
        regions.append((a['x'],a['y'],a['width'],a['height'],bool(a['script']),bool(a['leaveScript'])))
        actor=a['script'][0]['args']['actorId'];targets.append(next(i+1 for i,a in enumerate(scene['actors']) if a['id']==actor))
    def factory(index,arg):
        def flag(m,s,actors,tick,thread):
            if arg==1: actors[targets[index]].flags |= 2
            else: actors[targets[index]].flags &= ~2
            return False,False
        # LOCK/RESERVE. First param branch: GET/IF (+JUMP if false).
        # Second branch GET/IF. Actor const/flags, wait const/invoke, STOP.
        prefix=plain(4 if arg==1 else 7)
        return prefix+plain(1)+[flag]+plain(1)+[op_wait(pause)]
    triggers=Triggers(regions,scheduler,factory)
    player=Topdown(8,16,{},scheduler,triggers=triggers)
    total=max([end for _,end,_ in schedule]+[560])+180
    frames=engine_frames(engine,total+10,thread=scheduler,player=player,inputs=lambda tick:held_at(schedule,tick-3))
    w,h,pixels=background_pixels(project,scene)
    write_new(root/'scene.rgb444',b''.join(v.to_bytes(2,'little') for v in pixels))
    write_json(root/'source.json',{'size_px':[w,h],'probe_frames':total,'step_from_game_time':True,
        'view':list(view_of(engine.background))[::-1],'input_schedule':schedule,
        'expected_scroll':[list(p) for p,_ in frames],'actors':[d for _,d in frames],
        'project':str(p),'default_sprite_mode':project.settings['spriteMode'],
        'sprite_palettes':[list(p) for p in sprite_palettes(project,scene)],
        'oracle':'GBVM trigger.c/trigger.h and topdown.c','trigger_calls':triggers.calls,
        'player_trace':player.log})
    return manifest,build

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--toolchain',required=True)
    p.add_argument('--console',action='store_true');a=p.parse_args()
    if a.out.exists():p.error('output must be new')
    a.out.mkdir(parents=True)
    m,b=build_case(a.out,a.toolchain,120 if a.console else 20,CONSOLE if a.console else SCHEDULE)
    name='ngpcraft_m2_trigger_zones.ngc' if a.console else 'ngpcraft_m2_trigger_probe.ngc'
    write_new(a.out/name,(a.out/'export/rom/game.ngc').read_bytes())
    print(json.dumps({'rom':str(a.out/name),'bytes':b['header']['size_bytes']}))
if __name__=='__main__':main()
