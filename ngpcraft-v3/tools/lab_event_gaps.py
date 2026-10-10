"""Released 4.3.2 distance/While/For and player sheets; existing native probes."""
import argparse
import json
from pathlib import Path

from ngpcraft.project import load_project
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.lab_actor_spritesheets import project_case as sheet_case, run as sheet_probe
from tools.lab_expressions import run as variable_probe
from tools.reference import REFERENCE
import hashlib
import sys
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.probe_rom import power_on_ram, verified_tick
from tools.music_rom import atomic_bytes, png

def var(index):
    return {"type": "variable", "value": str(index)}

def value(kind, a, b):
    return {"type": kind, "valueA": a, "valueB": b}

def set_value(index, number):
    return ev(f"set-{index}-{number}", "EVENT_SET_VALUE", variable=str(index), value=num(number))

def branch(event, yes, no=None):
    event["children"] = {"true": yes, "false": no or []}
    return event

def player_project(root, pause=32):
    project = sheet_case(root, pause)
    path = next((project/'project/scenes').rglob('actors/push.gbsres'))
    actor = json.loads(path.read_text(encoding='utf8'))
    script = actor['startScript'][2:]
    for event in script:
        if event['command'] == 'EVENT_ACTOR_SET_SPRITE':
            event['command'] = 'EVENT_PLAYER_SET_SPRITE'
            del event['args']['actorId']
        elif event['command'] == 'EVENT_ACTOR_SET_STATE':
            event['args']['actorId'] = 'player'
    actor['startScript'] = [ev('hide-author','EVENT_ACTOR_HIDE',actorId='$self$'),
                           ev('player-collisions','EVENT_ACTOR_COLLISIONS_DISABLE',actorId='player')] + script
    write_json(path, actor)
    settings_path = project/'project/settings.gbsres'
    settings = json.loads(settings_path.read_text(encoding='utf8'))
    settings['startDirection'] = 'right'
    write_json(settings_path, settings)
    return project

def flow_project(root):
    project = arena(root)
    path = next((project/'project/scenes').rglob('scene.gbsres'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(actor_path.read_text(encoding='utf8'))
        actor['startScript'] = []
        write_json(actor_path, actor)
    scene = json.loads(path.read_text(encoding='utf8'))
    script = [ev('position-player','EVENT_ACTOR_SET_POSITION',actorId='player',x=num(72),y=num(64),units='pixels'),
              ev('position-target','EVENT_ACTOR_SET_POSITION',actorId='push-actor',x=num(96),y=num(96),units='pixels')]
    expected = {}
    for index, (op, result) in enumerate((('==',1),('!=',0),('<',0),('>',0),('<=',1),('>=',1)), 20):
        script.append(branch(ev(f'distance-{index}','EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR',
            actorId='player',otherActorId='push-actor',distance=num(5),operator=op),
            [set_value(index,1)], [set_value(index,0)]))
        expected[index] = result
    script += [set_value(0,4), branch(ev('live-distance','EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR',
        actorId='player',otherActorId='push-actor',distance=value('add',var(0),num(1)),operator='=='),
        [set_value(26,1)], [set_value(26,0)])]
    expected[26] = 1
    script += [set_value(27,7),branch(ev('no-else','EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR',
        actorId='player',otherActorId='player',distance=num(1),operator='>',__disableElse=True),
        [set_value(27,99)],[set_value(27,88)])]
    expected[27] = 7
    script += [set_value(28,0),branch(ev('while-three','EVENT_LOOP_WHILE',condition=value('lt',var(28),num(3))),
        [ev('while-inc','EVENT_INC_VALUE',variable='28'),ev('while-yield','EVENT_WAIT',units='frames',frames=num(1))]),
        branch(ev('while-zero','EVENT_LOOP_WHILE',condition=num(0)),[set_value(28,99)])]
    expected[28] = 3
    script += [set_value(29,0),branch(ev('for-inclusive','EVENT_LOOP_FOR',variable='30',
        **{'from':num(1),'to':num(4),'comparison':'<=','operation':'+=','value':num(1)}),
        [ev('sum','EVENT_VARIABLE_MATH_EVALUATE',variable='29',expression='$29$+$30$')])]
    expected.update({29:10,30:5})
    script += [set_value(31,0),branch(ev('for-down','EVENT_LOOP_FOR',variable='32',
        **{'from':num(5),'to':num(0),'comparison':'>','operation':'-=','value':num(2)}),
        [ev('down-inc','EVENT_INC_VALUE',variable='31')])]
    expected.update({31:3,32:65535})
    script += [set_value(33,0),set_value(34,5),set_value(35,1),
        branch(ev('for-live','EVENT_LOOP_FOR',variable='36',
            **{'from':num(0),'to':var(34),'comparison':'<','operation':'+=','value':var(35)}),
            [ev('live-inc','EVENT_INC_VALUE',variable='33'),set_value(34,3),set_value(35,2)])]
    expected.update({33:2,36:4})
    script += [set_value(37,0),branch(ev('for-empty','EVENT_LOOP_FOR',variable='38',
        **{'from':num(5),'to':num(2),'comparison':'<','operation':'+=','value':num(1)}),[set_value(37,99)])]
    expected.update({37:0,38:5})
    # Both loop events inside a custom event with a pass-by-reference variable.
    custom_id = 'event-gap-loop-ref'
    write_json(project/'project/scripts/event_gap_loop_ref.gbsres',{
        '_resourceType':'script','id':custom_id,'name':'Live loop reference','description':'',
        'symbol':'script_event_gap_loop_ref','actors':{},
        'variables':{'V0':{'id':'V0','name':'Counter','passByReference':True}},
        'script':[branch(ev('ref-while','EVENT_LOOP_WHILE',condition=value('lt',var('V0'),num(2))),
                   [ev('ref-inc','EVENT_INC_VALUE',variable='V0')]),
                  branch(ev('ref-for','EVENT_LOOP_FOR',variable='V0',
                      **{'from':num(2),'to':num(6),'comparison':'<','operation':'+=','value':num(2)}),
                      [ev('ref-yield','EVENT_WAIT',units='frames',frames=num(1))])]})
    script += [set_value(39,0),ev('ref-call','EVENT_CALL_CUSTOM_EVENT',customEventId=custom_id,
              **{'$variable[V0]$':var(39)}),set_value(42,1),
              ev('wait-screen','EVENT_WAIT',units='frames',frames=num(2)),
              ev('screen','EVENT_TEXT',text='Distance 3-4-5: $20$\nWhile: $28$ / For: $29$\nLive: $33$ / Ref: $39$',closeWhen='key')]
    expected.update({39:6,42:1})
    scene['script'] = script
    write_json(path,scene)
    return project, expected

def run_flow(out, toolchain, emulator, bios):
    result = variable_probe(out,toolchain,emulator,bios,case_builder=flow_project)
    result['schema'] = 'ngpcraft-event-gaps/1'
    result['reference_sha256'].update({rel:hashlib.sha256((REFERENCE/rel).read_bytes()).hexdigest()
        for rel in ('src/lib/compiler/scriptBuilder/scriptBuilder.ts',
                    'src/lib/events/eventIfActorDistanceFromActor.js','src/lib/events/eventLoopWhile.js',
                    'src/lib/events/eventLoopFor.js')})
    write_json(Path(out)/'comparison.json',result)
    return result

def run_player(out, toolchain, emulator, bios, clang, **kwargs):
    return sheet_probe(Path(out),toolchain,emulator,bios,clang,
                       case_builder=player_project,actor_index=0,**kwargs)

def control_project(root):
    project=arena(root)
    settings_path=project/'project/settings.gbsres'
    settings=json.loads(settings_path.read_text(encoding='utf8'))
    settings.update(startX=10,startY=10)
    write_json(settings_path,settings)
    path=next((project/'project/scenes').rglob('scene.gbsres'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor=json.loads(actor_path.read_text(encoding='utf8'))
        actor['startScript']=[]
        write_json(actor_path,actor)
    scene=json.loads(path.read_text(encoding='utf8'))
    # Initial scene context already holds one lock. Each phase runs eight Idles.
    operations=[[],['EVENT_SCRIPT_UNLOCK'],['EVENT_SCRIPT_LOCK','EVENT_SCRIPT_LOCK'],
                ['EVENT_SCRIPT_UNLOCK'],['EVENT_SCRIPT_UNLOCK'],['EVENT_SCRIPT_UNLOCK'],['EVENT_SCRIPT_LOCK']]
    script=[]
    for phase,commands in enumerate(operations,1):
        script += [ev(f'control-{phase}-{i}',command) for i,command in enumerate(commands)]
        script.append(set_value(20,phase))
        script += [ev(f'idle-{phase}-{i}','EVENT_IDLE') for i in range(8)]
    script.append(set_value(20,8)) # Return while locked: runner releases this context's remaining lock.
    scene['script']=script
    write_json(path,scene)
    return project

def run_control(out,toolchain,emulator,bios):
    out=Path(out)
    out.mkdir(parents=True,exist_ok=False)
    project=control_project(out)
    manifest=export_project(project,out/'export')
    build=build_rom(out/'export',toolchain)
    rom=(out/'export/rom/game.ngc').read_bytes()
    sys.path.insert(0,str(Path(emulator).resolve()))
    from core import native
    machine=native.NativeMachine(rom,bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10,bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine,'noise')
    machine.set_breakpoints([verified_tick(rom,build)])
    symbols=build['link_audit']['public_symbols']
    def addr(name): return symbols['_'+name]['address']
    def word(address): return int.from_bytes(machine.read(address,2),'little')
    marker=addr('script_memory')+2*manifest['variables']['VAR_VARIABLE_20']
    rows=[]
    for tick in range(72):
        machine.write(0xB0,bytes([8]))
        stop,_=machine.run(3_000_000,record=False)
        if stop.stop_status!=40: raise ValueError('control lab did not reach game tick')
        rows.append({'time':word(addr('ngc_game_time')),'phase':word(marker),
                     'x':word(addr('act_x')),'frame':stop.frame_count,
                     'joy':machine.read(addr('ngc_joy'),1)[0]})
        if rows[-1]['phase'] in (2,3,4,5,7,8):
            atomic_bytes(out/f"phase-{rows[-1]['phase']}.png",png(machine.framebuffer()))
    counts={phase:sum(r['phase']==phase for r in rows) for phase in range(1,8)}
    deltas={phase:[b['x']-a['x'] for a,b in zip(rows,rows[1:]) if a['phase']==b['phase']==phase]
            for phase in range(1,9)}
    mismatches=[]
    for phase,count in counts.items():
        if count!=8: mismatches.append({'phase':phase,'idle_ticks':count,'expected':8})
    for phase,changes in deltas.items():
        locked=phase in (1,3,4,7)
        if not changes or (any(changes) if locked else any(delta<=0 for delta in changes)):
            mismatches.append({'phase':phase,'deltas':changes,'locked':locked})
    drops=[i for i,(a,b) in enumerate(zip(rows,rows[1:]),1) if i>3 and b['frame']-a['frame']!=1]
    result={'schema':'ngpcraft-script-control/1','counts':counts,'deltas':deltas,
            'mismatches':mismatches,'dropped_frames':drops,'console_validated':False,
            'rom_sha256':hashlib.sha256(rom).hexdigest(),'ram':'noise','silicon_timing':[10,8],
            'emulator_core_sha256':hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
            'reference_sha256':{rel:hashlib.sha256((REFERENCE/rel).read_bytes()).hexdigest()
                for rel in ('src/lib/events/eventScriptLock.js','src/lib/events/eventScriptUnlock.js',
                            'src/lib/events/eventIdle.js','appData/engine/gbvm/src/core/vm.c')}}
    write_json(out/'rows.json',rows)
    write_json(out/'comparison.json',result)
    return result

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=('flow','player','control'))
    for option in ('out','toolchain','emulator','bios','clang'):
        parser.add_argument('--'+option,required=option!='clang')
    args=parser.parse_args()
    result=(run_control(Path(args.out),args.toolchain,args.emulator,args.bios) if args.kind=='control' else
            run_flow(Path(args.out),args.toolchain,args.emulator,args.bios) if args.kind=='flow' else
            run_player(Path(args.out),args.toolchain,args.emulator,args.bios,args.clang))
    print(json.dumps(result,indent=2))
    raise SystemExit(bool(result.get('mismatches') or result.get('pixel_mismatch_frames') or result.get('dropped_frames')))
