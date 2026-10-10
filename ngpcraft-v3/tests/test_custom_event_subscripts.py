"""Custom script arguments used by input/timer scripts (docs/M2_CUSTOM_EVENTS.md).

Such a script runs in a context of its own: the arguments of the custom
script that attached it are not on its stack. GB Studio 4.3.2 still
addresses them there (found with Dead Dungeon: its attack guard read and
wrote a random cell). The V3 binds a variable passed by reference and an
actor at the call site, and refuses a value argument there.
"""
import json
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena


def project(root, *, by_reference=True, actor_arg=False):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    actor_id = None
    for p in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(p.read_text(encoding='utf8'))
        actor['startScript'] = []
        actor_id = actor_id or actor['id']
        write_json(p, actor)
    body = [ev('guard', 'EVENT_SET_VALUE', variable='V0', value=num(1))]
    if actor_arg:
        body.append(ev('hide', 'EVENT_ACTOR_HIDE', actorId='A0'))
    write_json(project / 'project/scripts/attach_attack.gbsres', {
        '_resourceType': 'script', 'id': 'attach-attack', 'name': 'Attach attack', 'description': '',
        'symbol': 'script_attach_attack',
        'actors': {'A0': {'id': 'A0', 'name': 'Target'}} if actor_arg else {},
        'variables': {'V0': {'id': 'V0', 'name': 'Guard', 'passByReference': by_reference}},
        'script': [{**ev('attach', 'EVENT_SET_INPUT_SCRIPT', input=['b'], persist=False),
                    'children': {'true': body}}]})
    call = {'customEventId': 'attach-attack', '$variable[V0]$': {'type': 'variable', 'value': '7'}}
    if actor_arg:
        call['$actor[A0]$'] = actor_id
    scene = json.loads(path.read_text(encoding='utf8'))
    scene['script'] = [ev('call', 'EVENT_CALL_CUSTOM_EVENT', **call)]
    write_json(path, scene)
    return project


def input_scripts(manifest):
    return {k: v['source'] for k, v in manifest['scripts'].items() if k.startswith('script_input')}


class SubScriptArgumentTests(unittest.TestCase):
    def test_reference_and_actor_are_bound_at_the_call_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = export_project(project(Path(tmp), actor_arg=True), Path(tmp) / 'out')
        sources = input_scripts(manifest)
        self.assertEqual(len(sources), 1)
        text = next(iter(sources.values()))
        self.assertNotIn('SCRIPT_ARG', text)
        self.assertIn('VM_SET_CONST            VAR_QUEST8, 1', text)       # gbs2 variable 7
        self.assertIn('VM_ACTOR_SET_HIDDEN', text)
        bound = manifest['scripts']['script_attach_attack_bound']['source']
        self.assertIn('VM_RET_FAR\n', bound)              # nothing passed any more

    def test_value_argument_in_an_input_script_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CompileError, 'runs in its own context.*value argument'):
                export_project(project(Path(tmp), by_reference=False), Path(tmp) / 'out')


if __name__ == '__main__':
    unittest.main()
