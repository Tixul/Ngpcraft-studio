"""Released variable reset: heap cleared, custom call stack and arguments survive."""
import argparse
import hashlib
import json
from pathlib import Path

from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.lab_event_gaps import set_value, var
from tools.lab_expressions import run as observe
from tools.reference import REFERENCE

FILES = {'EVENT_RESET_VARIABLES': 'eventVariablesReset.js'}


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    for p in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(p.read_text(encoding='utf8'))
        actor['startScript'] = [ev('actor-local-seed', 'EVENT_SET_VALUE', variable='L0', value=num(88))]
        write_json(p, actor)
    scene = json.loads(path.read_text(encoding='utf8'))
    custom_id = 'reset-with-live-arguments'
    write_json(project / 'project/scripts/reset_live_args.gbsres', {
        '_resourceType': 'script', 'id': custom_id, 'name': 'Reset with live arguments',
        'description': '', 'symbol': 'script_reset_live_args', 'actors': {},
        'variables': {'V0': {'id': 'V0', 'name': 'Value', 'passByReference': False},
                      'V1': {'id': 'V1', 'name': 'Reference', 'passByReference': True}},
        'script': ([] if witness else [ev('reset', 'EVENT_RESET_VARIABLES')]) + [
            ev('read-reference-after-reset', 'EVENT_SET_VALUE', variable='104', value=var('V1')),
            ev('value-survives-reset', 'EVENT_SET_VALUE', variable='105', value=var('V0')),
            ev('reference-survives-reset', 'EVENT_SET_VALUE', variable='V1', value=num(9))]})
    script = [set_value(0, -32768), set_value(1, 32767), set_value(137, 77),
              ev('scene-local-seed', 'EVENT_SET_VALUE', variable='L0', value=num(66)),
              ev('temporary-seed', 'EVENT_SET_VALUE', variable='T0', value=num(55)),
              ev('call-reset', 'EVENT_CALL_CUSTOM_EVENT', customEventId=custom_id,
                 **{'$variable[V0]$': num(123), '$variable[V1]$': var(137)}),
              ev('global-low', 'EVENT_SET_VALUE', variable='100', value=var(0)),
              ev('global-high', 'EVENT_SET_VALUE', variable='101', value=var(1)),
              ev('scene-local', 'EVENT_SET_VALUE', variable='102', value=var('L0')),
              ev('temporary', 'EVENT_SET_VALUE', variable='103', value=var('T0')),
              set_value(42, 1), ev('wait', 'EVENT_WAIT', units='frames', frames=num(2)),
              ev('screen', 'EVENT_TEXT', text='Reset variables OK\nGlobals: $100$ / $101$\nLocal/temp: $102$ / $103$\nArgs: $105$ / $137$', closeWhen='key')]
    scene.update(name='M4 Variable reset', script=script)
    write_json(path, scene)
    return project, {100: 0, 101: 0, 102: 0, 103: 0,
                     104: 0, 105: 123, 137: 9, 42: 1,
                     'VAR_QUEST1': 0, 'VAR_QUEST2': 0,
                     'VAR_S0A0_LOCAL_0': 0, 'VAR_S0A0_LOCAL_0_1': 0}


def run(out, toolchain, emulator, bios, witness=False):
    report = observe(out, toolchain, emulator, bios,
                     case_builder=lambda root: project_case(root, witness))
    report.update(schema='ngpcraft-variable-reset/1', witness=witness)
    for rel in ('src/lib/events/eventVariablesReset.js',
                'src/lib/compiler/scriptBuilder/scriptBuilder.ts',
                'src/lib/compiler/generateGBVMData.ts'):
        report['reference_sha256'][rel] = hashlib.sha256((REFERENCE / rel).read_bytes()).hexdigest()
    write_json(Path(out) / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    result = run(Path(args.out), args.toolchain, args.emulator, args.bios)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result['mismatches']))
