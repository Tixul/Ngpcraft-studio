"""Sixteen-bit GB Studio variable flags observed in a native ROM."""
import argparse
import hashlib
import json
from pathlib import Path

from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_event_gaps import branch, set_value, var
from tools.lab_actor_push import project_case as arena
from tools.lab_expressions import run as observe
from tools.reference import REFERENCE

FILES={'EVENT_SET_FLAGS':'eventVariableSetFlags.js','EVENT_ADD_FLAGS':'eventVariableAddFlags.js',
       'EVENT_CLEAR_FLAGS':'eventVariableClearFlags.js','EVENT_IF_FLAGS_COMPARE':'eventIfVariableFlagsCompare.js'}

def flags(mask): return {f'flag{i+1}':bool(mask&(1<<i)) for i in range(16)}

def project_case(root, witness=False):
    project=arena(root)
    path=next((project/'project/scenes').rglob('scene.gbsres'))
    for p in path.parent.glob('actors/*.gbsres'):
        actor=json.loads(p.read_text(encoding='utf8'));actor['startScript']=[];write_json(p,actor)
    scene=json.loads(path.read_text(encoding='utf8'))
    script=[ev('set-all','EVENT_SET_FLAGS',variable='0',**flags(65535)),
            ev('snapshot-all','EVENT_SET_VALUE',variable='100',value=var(0))]
    expected={100:65535}
    for index in range(16):
        script += [ev(f'clear-{index}','EVENT_CLEAR_FLAGS',variable='0',**flags(1<<index)),
                   branch(ev(f'check-clear-{index}','EVENT_IF_FLAGS_COMPARE',variable='0',flag=index),
                          [set_value(101+index,0)],[set_value(101+index,1)])]
        expected[101+index]=1
    script += [ev('snapshot-cleared','EVENT_SET_VALUE',variable='133',value=var(0))]
    expected[133]=0
    for index in range(16):
        script += [ev(f'add-{index}','EVENT_ADD_FLAGS',variable='0',**flags(1<<index)),
                   branch(ev(f'check-add-{index}','EVENT_IF_FLAGS_COMPARE',variable='0',flag=index),
                          [set_value(117+index,1)],[set_value(117+index,0)])]
        expected[117+index]=1
    script += [ev('snapshot-restored','EVENT_SET_VALUE',variable='134',value=var(0)),
               ev('set-alternate','EVENT_SET_FLAGS',variable='1',**flags(0xAAAA)),
               ev('add-mixed','EVENT_ADD_FLAGS',variable='1',**flags(0x8001)),
               ev('clear-mixed','EVENT_CLEAR_FLAGS',variable='1',**flags(0xA00A)),
               ev('snapshot-mixed','EVENT_SET_VALUE',variable='135',value=var(1)),
               ev('add-empty','EVENT_ADD_FLAGS',variable='1',**flags(0)),
               ev('clear-empty','EVENT_CLEAR_FLAGS',variable='1',**flags(0)),
               ev('snapshot-noop','EVENT_SET_VALUE',variable='136',value=var(1))]
    expected.update({134:65535,135:(0xAAAA|0x8001)&~0xA00A,136:(0xAAAA|0x8001)&~0xA00A})
    custom_id='flags-by-reference'
    write_json(project/'project/scripts/flags_by_reference.gbsres',{
        '_resourceType':'script','id':custom_id,'name':'Flags by reference','description':'',
        'symbol':'script_flags_by_reference','actors':{},
        'variables':{'V0':{'id':'V0','name':'Flags','passByReference':True}},
        'script':[ev('ref-set','EVENT_SET_FLAGS',variable='V0',**flags(0x8001)),
                  ev('ref-clear','EVENT_CLEAR_FLAGS',variable='V0',**flags(1)),
                  ev('ref-add','EVENT_ADD_FLAGS',variable='V0',**flags(2)),
                  branch(ev('ref-check','EVENT_IF_FLAGS_COMPARE',variable='V0',flag=15),
                         [set_value(138,1)],[set_value(138,0)])]})
    script += [set_value(137,0),ev('ref-call','EVENT_CALL_CUSTOM_EVENT',customEventId=custom_id,
                    **{'$variable[V0]$':var(137)}),
               ev('set-empty','EVENT_SET_FLAGS',variable='1',**flags(0)),
               ev('snapshot-empty','EVENT_SET_VALUE',variable='139',value=var(1)),set_value(140,7),
               branch(ev('disabled-else','EVENT_IF_FLAGS_COMPARE',variable='1',flag=15,__disableElse=True),
                      [set_value(140,88)],[set_value(140,99)]),set_value(42,1),
               ev('wait','EVENT_WAIT',units='frames',frames=num(2)),
               ev('screen','EVENT_TEXT',text='Flags: 16 bits OK\nClear/add: $101$ / $132$\nReference: $137$ / $138$',closeWhen='key')]
    expected.update({137:0x8002,138:1,139:0,140:7,42:1})
    if witness:
        # Neutralize the authored add events; the probe must detect missing bits.
        for event in script:
            if event['command']=='EVENT_ADD_FLAGS': event['args'].update(flags(0))
    scene['script']=script;write_json(path,scene)
    return project,expected

def run(out,toolchain,emulator,bios,witness=False):
    result=observe(out,toolchain,emulator,bios,
                   case_builder=lambda root:project_case(root,witness))
    result['schema']='ngpcraft-variable-flags/1'
    result['witness']=witness
    result['reference_sha256'].update({f'src/lib/events/{name}':hashlib.sha256((REFERENCE/'src/lib/events'/name).read_bytes()).hexdigest()
                                       for name in FILES.values()})
    write_json(Path(out)/'comparison.json',result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('out','toolchain','emulator','bios'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    result=run(Path(args.out),args.toolchain,args.emulator,args.bios)
    print(json.dumps(result,indent=2))
    raise SystemExit(bool(result['mismatches']))
