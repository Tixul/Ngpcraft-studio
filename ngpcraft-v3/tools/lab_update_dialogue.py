"""Concurrent On Update movement suspended inside a modal dialogue instruction.

Derived Sample Town, unchanged PNGs, empty collision map. Two pinned workers
cross the GB priority area. A third actor opens two pages after sixty ticks.
No instrumentation in the ROM; the independent scheduler predicts positions.
"""
import argparse
import hashlib
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import default_font
from tools.demo_actor_moves import ev, num, sequence
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.demo_text import PAGES
from tools.gbvm_actors import (ScriptScheduler, engine_frames, plain, op_wait,
                               op_idle, op_jump, move_ops, ATTR_H_FIRST)
from tools.gbvm_topdown import Topdown
from tools.gbvm_ui import Ui, load_font
from tools.check_text import page_bytes
from tools.lab_modal_sprite_stress import project_case, observe, compare

def worker_start(k):
    if k < 2:
        return (48,64) if k==0 else (72,88)
    return 48+(k%4)*24,64+(k//4)*24


def worker_plan(k):
    if k < 2:
        return PLANS[k]
    x,y=worker_start(k)
    return ((x+16,y),(x-16,y))


PAUSE = 8
PLANS = [((96,64),(32,64)), ((72,104),(72,48))]


def build_case(root, toolchain, *, rows=4, witness=None, no_fade=False, interaction=False, worker_count=2):
    if not 2 <= worker_count <= 14:
        raise ValueError('worker_count must be between 2 and 14')
    project = project_case(root, rows=rows)
    scene_path = next(project.rglob('scene.gbsres'))
    scene = json.loads(scene_path.read_text(encoding='utf-8'))
    scene.update(script=[], autoFadeSpeed=None if no_fade else 1, collisions='')
    write_json(scene_path, scene)
    if interaction:
        settings_path=project/'project/settings.gbsres'
        settings=json.loads(settings_path.read_text(encoding='utf-8'))
        settings.update(startDirection='down',startMoveSpeed=1)
        write_json(settings_path,settings)
    actor_paths = sorted(scene_path.parent.rglob('actors/*.gbsres'))
    # Extra actors use the same unchanged sprite asset and ordinary GB scripts.
    worker_paths=actor_paths[:2]+(actor_paths[3:] if worker_count>2 else [])
    extra_paths=[]
    template=json.loads(actor_paths[0].read_text(encoding='utf-8'))
    for k in range(len(worker_paths),worker_count):
        actor=dict(template,id=f'budget-worker-{k}',symbol=f'actor_budget_worker_{k}',
                   name=f'Budget worker {k}',script=[])
        path=actor_paths[0].parent/f'budget_worker_{k:02d}.gbsres'
        write_json(path,actor);extra_paths.append(path)
    actor_paths=(worker_paths+extra_paths+[actor_paths[2]] if worker_count>2
                 else actor_paths)
    workers = []
    controller = None
    for k,path in enumerate(actor_paths):
        actor=json.loads(path.read_text(encoding='utf-8'))
        actor.update(collisionGroup='', moveSpeed=1)
        if k < worker_count:
            x,y=worker_start(k)
            actor.update(x=x,y=y)
            actor['updateScript'] = []
            for i,(x,y) in enumerate(worker_plan(k)):
                actor['updateScript'] += [ev(f'move-{k}-{i}', 'EVENT_ACTOR_MOVE_TO', actorId='$self$',
                    x=num(x), y=num(y), units='pixels', collideWith=[], moveType='horizontal', lockDirection=[]),
                    ev(f'wait-{k}-{i}', 'EVENT_WAIT', units='frames', frames=num(PAUSE))]
            workers.append(actor['id'])
        elif k==worker_count:
            actor.update(x=128,y=64)
            actor['updateScript'] = [ev('dialogue-wait','EVENT_WAIT',units='frames',frames=num(60)),
                ev('dialogue','EVENT_TEXT',text=PAGES,minHeight=rows,maxHeight=rows),
                ev('repeat-wait','EVENT_WAIT',units='frames',frames=num(240))]
            if interaction:
                actor.update(isPinned=False,x=96,y=352,updateScript=[])
                actor['script']=[ev('interaction-text','EVENT_TEXT',text=PAGES,minHeight=rows,maxHeight=rows)]
            controller=actor['id']
        else:
            actor.update(x=400,y=400)
        write_json(path,actor)
    manifest=export_project(project,root/'export')
    if witness:
        loaded = load_project(project)
        worker_index = next(i+1 for i,a in enumerate(loaded.scenes[0]['actors']) if a['id']==workers[0])
        rel,old,new={
            'frame_wait': ('src/game.c', '    ngc_vm_frame_end();\n}', '}'),
            'frame_position': ('src/scene_sprites.c', 'first->x == mask_x && first->y == mask_y', '1u'),
            'modal_position': ('src/game.c','void ngc_ui_modal_logic(void)\n{',
                'void ngc_ui_modal_logic(void)\n{\n'
                '    extern void ngc_actor_set_pos(unsigned char i, unsigned short x, unsigned short y);\n'
                f'    ngc_actor_set_pos({worker_index}u, (unsigned short)(((ngc_game_time & 7u) + 40u) << 5), 2048u);'),
            'modal_input': ('src/game.c','void ngc_ui_modal_logic(void)\n{',
                'void ngc_ui_modal_logic(void)\n{\n    ngc_topdown_update();'),
            'position': ('src/scene_sprites.c','cache->x == sx && cache->y == sy','1u')
        }[witness]
        path=root/'export'/rel
        src=path.read_text(encoding='utf-8');assert src.count(old)==1
        tmp=path.with_suffix('.tmp');tmp.write_text(src.replace(old,new),encoding='utf-8');tmp.replace(path)
        manifest['sha256'][rel]=hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(root/'export/export.json',manifest)
    build=build_rom(root/'export',Path(toolchain))
    schedule=([[20,80,16],[110,118,16],[210,218,16],[350,410,16],
               [460,468,16],[560,568,16],[40,180,8],[370,500,8],[620,660,8]]
              if interaction else [[150,158,16],[300,308,16],[800,808,16],[1000,1008,16]])
    write_json(root/'inputs.json',schedule)
    write_new(root/'ngpcraft_update_dialogue.ngc',(root/'export/rom/game.ngc').read_bytes())
    write_json(root/'fixture.json',{'rows':rows,'workers':workers,'controller':controller,
                                   'pngs_unchanged':True,'collision_map_cleared':True,'witness':witness,'no_fade':no_fade,'interaction':interaction,'worker_count':worker_count})
    return manifest,build


class ModalUi:
    """ui.c/vm_ui.c calls as operations within ONE preserved VM quantum."""
    def __init__(self,font,pages,rows,probe):
        self.ui=Ui(font);self.pages=pages;self.rows=rows;self.probe=probe
        self.joy=0;self.starts=[];self.modal_ticks=[];self.scheduler=None

    def held(self,k):
        raw=self.probe['input_samples'][max(0,min(k,self.probe['frames']))]
        return (raw&48)|((raw&3)<<2)|((raw&4)>>1)|((raw&8)>>3)

    def read(self,k):
        old=self.joy;self.joy=self.held(k);return self.joy&~old&255

    def call(self,method,*args):
        def op(m,s,a,t,th):
            getattr(self.ui,method)(*args)
            return False,False
        return op

    def wait(self,flags):
        def op(m,s,a,t,th):
            self.joy=self.held(t-2) if t>=2 else 0
            pressed=self.read(t-1)
            while not self.ui.wait_ok(flags,pressed):
                tick=self.scheduler.tick
                if not self.modal_ticks or self.modal_ticks[-1]!=tick-1:
                    self.starts.append(tick)
                self.modal_ticks.append(tick)
                self.ui.update(0,pressed)
                yield
                pressed=self.read(self.scheduler.tick-1)
            return False,False
        return op

    def text_ops(self):
        ops=[]
        for i,page in enumerate(self.pages):
            ops += [self.call('overlay_clear',0,0,20,self.rows,1,1)]
            if i==0:
                ops += [self.call('move_to',0,18,-3),self.call('move_to',0,18-self.rows,-1),
                        self.call('set_scroll',1,1,18,5)]
            ops += plain(2)+[self.call('display_text',page),self.wait(7)]
        ops += [self.call('move_to',0,18,-2),self.wait(3)]
        return ops


def compare_updates(root):
    project=load_project(root/'project');scene=project.scenes[0]
    fixture=json.loads((root/'fixture.json').read_text())
    controller=next(a for a in scene['actors'] if a['id']==fixture['controller'])
    def expected(engine,probe,states):
        index={a['id']:i+1 for i,a in enumerate(scene['actors'])}
        rel,_=default_font(project);font=load_font(project.root/Path(rel).with_suffix(''))
        mapping=json.loads((project.root/Path(rel).with_suffix('').with_suffix('.json')).read_text()).get('mapping') or {}
        ui=ModalUi(font,[page_bytes(p,mapping) for p in PAGES],fixture['rows'],probe)
        factories={}
        for k,actor_id in enumerate(fixture['workers']):
            def worker(k=k,actor_id=actor_id):
                ops=plain(1)
                for x,y in worker_plan(k):
                    ops += plain(2)+move_ops(index[actor_id],x<<5,y<<5,ATTR_H_FIRST,
                        sequence(False,x,y,'horizontal',[])) + plain(1)+[op_wait(PAUSE)]
                return ops+[op_idle(),op_jump(1)]
            factories[index[actor_id]]=worker
        interaction=fixture.get('interaction',False)
        if not interaction:
            factories[index[fixture['controller']]]=lambda: plain(2)+[op_wait(60)]+ui.text_ops()+plain(1)+[op_wait(240),op_idle(),op_jump(1)]
        scheduler=ScriptScheduler(plain(2) if fixture.get('no_fade',False) else plain(2)+[op_idle()]+plain(2),factories);ui.scheduler=scheduler
        player=(Topdown(interactions={index[fixture['controller']]:lambda: plain(1)+ui.text_ops()},
                        scheduler=scheduler) if interaction else None)
        trace=[]
        def record(s,actors,t):
            trace.append({'tick':t,'modal':t in ui.modal_ticks,
                          'positions':[[actors[index[w]].x,actors[index[w]].y] for w in fixture['workers']],
                          'player':[actors[0].x,actors[0].y],'locked':s.locked})
        frames=engine_frames(engine,max(probe['game_time_samples'])+10,thread=scheduler,after_render=record,player=player,
                             inputs=lambda tick: probe['input_samples'][min(tick-1,probe['frames'])])
        normal=[r for r in probe['frame_timings'] if r['mode']=='normal' and r['frame']>2]
        return frames,{'oracle':'GBVM vm.c + vm_actor.c + ui.c + topdown.c','worker_trace':trace,
            'interaction_ticks':player.interaction_ticks if player else [],
            'predicted_modal_starts':[t for t in ui.starts if t<=probe['frames']],
            'predicted_modal_ticks':[t for t in ui.modal_ticks if t<=probe['frames']],
            'modal_schedule_passed':[t for t in ui.modal_ticks if t<=probe['frames']] == probe['modal_frames'],
            'normal_commit_max_lines':max(r['lines'] for r in normal),
            'normal_commit_overruns_after_dialogue':[r for r in normal if r['overran_vblank'] or not r['started_in_vblank']],
            'normal_missed_frames_after_dialogue':[r for r in normal if r['frames_to_next_entry']!=1]}
    return compare(root,sequence_factory=expected,dialogue_events=controller['script'] if fixture.get('interaction',False) else controller['updateScript'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--toolchain',type=Path,required=True)
    p.add_argument('--emulator',type=Path,required=True);p.add_argument('--bios',type=Path,required=True)
    p.add_argument('--rows',type=int,choices=(4,11),default=4)
    p.add_argument('--workers',type=int,choices=range(2,15),default=2)
    p.add_argument('--interaction',action='store_true')
    p.add_argument('--no-fade',action='store_true')
    p.add_argument('--witness',choices=('position','modal_position','modal_input','frame_wait','frame_position'))
    args=p.parse_args()
    if args.out.exists():p.error('output must be new')
    args.out.mkdir(parents=True)
    build_case(args.out,args.toolchain,rows=args.rows,witness=args.witness,no_fade=args.no_fade,interaction=args.interaction,worker_count=args.workers)
    result=observe(args.out,args.emulator,args.bios,frames=720 if args.interaction else 1160,comparator=compare_updates)
    print(json.dumps({k:v for k,v in result.items() if k not in ('worker_trace','predicted_modal_ticks')},indent=1))
    return 0 if (not result['pixels_passed'] if args.witness else result['passed']) else 1

if __name__=='__main__':raise SystemExit(main())
