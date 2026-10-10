"""Player/actor collision ROM, including unlocked callbacks and queue ordering."""
import argparse
import hashlib
import json
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_assets import background_pixels,sprite_palettes
from ngpcraft.background_stream import view_of
from ngpcraft.toolchain import build_rom
from ngpcraft.scene_collisions import collision_groups
from tools.demo_topdown import project_case as topdown_project
from tools.demo_actor_moves import ev,num,ANGRY
from tools.demo_gbs_scene import write_json
from tools.demo_backgrounds import write_new
from tools.gbvm_actors import plain,op_wait,engine_frames,ScriptScheduler,op_set_dir,op_idle,op_jump
from tools.gbvm_topdown import Topdown,held_at
from tools.gbvm_player_collisions import PlayerCollisions

SCHEDULE=[(20,90,8),(180,250,4),(320,390,8),(480,550,4)]
CONSOLE=[(120,136,8),(180,196,4),(420,436,8),(480,496,4),(720,736,8),(780,796,4)]

def project_case(root,pause=60,group='1',repeat=False,overlap=False,updates=False):
    p=topdown_project(root)
    paths=list((p/'project/scenes').rglob('actors/*.gbsres'))
    duck=next(json.loads(path.read_text()) for path in paths if json.loads(path.read_text())['x']==18)
    for path in paths:
        a=json.loads(path.read_text());a['script']=[]
        if a['id']==ANGRY:
            a['collisionGroup']=group
            if updates:
                a['updateScript']=[ev('up','EVENT_ACTOR_SET_DIRECTION',actorId='$self$',direction={'type':'direction','value':'up'}),ev('w-up','EVENT_WAIT',units='frames',frames=num(9)),ev('down','EVENT_ACTOR_SET_DIRECTION',actorId='$self$',direction={'type':'direction','value':'down'}),ev('w-down','EVENT_WAIT',units='frames',frames=num(9))]
            if overlap:a['x']=12
            a['script']=[ev('show-duck','EVENT_ACTOR_SHOW',actorId=duck['id'])]
            if not repeat:a['script'] += [ev('disable','EVENT_ACTOR_COLLISIONS_DISABLE',actorId='$self$')]
            a['script'] += [ev('hide','EVENT_ACTOR_HIDE',actorId='$self$'),ev('wait','EVENT_WAIT',units='frames',frames=num(pause)),ev('show','EVENT_ACTOR_SHOW',actorId='$self$')]
            if not repeat:a['script'] += [ev('enable','EVENT_ACTOR_COLLISIONS_ENABLE',actorId='$self$')]
        write_json(path,a)
    path=next(p.rglob('scene.gbsres'));s=json.loads(path.read_text())
    s[f'playerHit{group}Script']=[ev('hide-duck','EVENT_ACTOR_HIDE',actorId=duck['id']),ev('player-wait','EVENT_WAIT',units='frames',frames=num(pause)),ev('return-duck','EVENT_ACTOR_SHOW',actorId=duck['id'])]
    write_json(path,s)
    return p

def build_case(root,toolchain,pause=60,group='1',repeat=False,overlap=False,schedule=None,mutate=None,updates=False):
    if schedule is None:schedule=[] if overlap else SCHEDULE
    p=project_case(root,pause,group,repeat,overlap,updates);manifest=export_project(p,root/'export')
    if mutate:
        rel,old,new=mutate;path=root/'export'/rel;s=path.read_text();assert s.count(old)==1,(rel,old,s.count(old));s=s.replace(old,new)
        write_new(path.with_suffix('.mutated'),s.encode());path.with_suffix('.mutated').replace(path)
        manifest['sha256'][rel]=hashlib.sha256(s.encode()).hexdigest();write_json(root/'export/export.json',manifest)
    build=build_rom(root/'export',toolchain)
    project=load_project(p);scene=project.scenes[0];engine=build_engine_scene(project,scene)
    angry=next(i+1 for i,a in enumerate(scene['actors']) if a['id']==ANGRY)
    duck=next(i+1 for i,a in enumerate(scene['actors']) if a['x']==18)
    def update_factory():
        return plain(2)+[op_set_dir(angry,2)]+plain(1)+[op_wait(9)]+plain(1)+[op_set_dir(angry,0)]+plain(1)+[op_wait(9),op_idle(),op_jump(1)]
    scheduler=ScriptScheduler(plain(4)+[op_wait(12)],{angry:update_factory} if updates else {})
    def flags(index,mask,value):
        def run(m,s,actors,tick,thread):
            actors[index].flags=(actors[index].flags&~mask)|value
            return False,False
        return run
    def player_factory(argument):
        assert argument=={'1':2,'2':4,'3':8}[group]
        return plain(4)+[flags(duck,2,2)]+plain(1)+[op_wait(pause)]+plain(1)+[flags(duck,2,0)]
    def actor_factory():
        ops=plain(4)+[flags(duck,2,0)]
        if not repeat:ops+=plain(1)+[flags(angry,8,0)]
        ops+=plain(1)+[flags(angry,2,2)]+plain(1)+[op_wait(pause)]+plain(1)+[flags(angry,2,0)]
        if not repeat:ops+=plain(1)+[flags(angry,8,8)]
        return ops
    hits=PlayerCollisions(collision_groups(project,scene),scheduler,player_factory,{angry:actor_factory})
    player=Topdown(8,16,{},scheduler,hits=hits)
    total=max([end for _,end,_ in schedule]+[400])+pause+80
    frames=engine_frames(engine,total+10,thread=scheduler,player=player,inputs=lambda tick:held_at(schedule,tick-3),after_render=lambda s,a,t:hits.finish(t))
    w,h,pixels=background_pixels(project,scene);write_new(root/'scene.rgb444',b''.join(v.to_bytes(2,'little') for v in pixels))
    write_json(root/'source.json',{'size_px':[w,h],'probe_frames':total,'step_from_game_time':True,'view':list(view_of(engine.background))[::-1],
        'input_schedule':schedule,'expected_scroll':[list(p) for p,_ in frames],'actors':[d for _,d in frames],
        'project':str(p),'default_sprite_mode':project.settings['spriteMode'],'sprite_palettes':[list(p) for p in sprite_palettes(project,scene)],
        'oracle':'GBVM actor.c collision callbacks and topdown.c','collision_calls':hits.calls,'collision_trace':hits.trace,'player_trace':player.log})
    return manifest,build

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--toolchain',required=True);p.add_argument('--console',action='store_true');a=p.parse_args()
    if a.out.exists():p.error('output must be new')
    a.out.mkdir(parents=True);m,b=build_case(a.out,a.toolchain,pause=120 if a.console else 60,schedule=CONSOLE if a.console else SCHEDULE)
    name='ngpcraft_m2_player_collision.ngc' if a.console else 'ngpcraft_m2_collision_probe.ngc'
    write_new(a.out/name,(a.out/'export/rom/game.ngc').read_bytes());print(json.dumps({'rom':str(a.out/name),'bytes':b['header']['size_bytes']}))
if __name__=='__main__':main()
