"""Reachability is conservative; it never enables an undelivered event."""
from types import SimpleNamespace
import unittest
from ngpcraft.compiler import CompileError
from ngpcraft.scene_graph import scene_graph


def event(command='EVENT_SWITCH_SCENE', **args):
    return {'command': command, 'args': args}


def graph(scripts, hooks=(), custom=(), excluded=(), start='a'):
    threads = [SimpleNamespace(scene_id=sid, events=events,
               sources=[f'{sid}:script[{i}]' for i in range(len(events))])
               for sid, events in scripts.items()]
    return scene_graph(SimpleNamespace(settings={'startSceneId': start},
                       by_type={'script': list(custom)}),
                       SimpleNamespace(scenes=threads, excluded=list(excluded)), hooks)


class SceneGraphTests(unittest.TestCase):
    def test_cycle_and_unused_scene(self):
        result = graph({'a': [event(sceneId='b')], 'b': [event(sceneId='a')], 'c': []})
        self.assertEqual(result['reachable'], ['a', 'b'])
        self.assertEqual(result['unreachable'], ['c'])
        self.assertFalse(result['runtime_delivered'])

    def test_both_conditional_branches(self):
        branch = event('EVENT_IF', variable='0')
        branch['children'] = {'true': [event(sceneId='b')], 'false': [event(sceneId='c')]}
        result = graph({'a': [branch], 'b': [], 'c': []})
        self.assertEqual(result['reachable'], ['a', 'b', 'c'])
        self.assertEqual(result['transitions'][0]['path'], 'a:script[0].children.true[0]')

    def test_comment_discards_children(self):
        branch = event('EVENT_GROUP', __comment=True)
        branch['children'] = {'true': [event(sceneId='b')]}
        self.assertEqual(graph({'a': [branch], 'b': []})['reachable'], ['a'])

    def test_hook_can_reach_scene(self):
        hook = SimpleNamespace(scene_id='a', events=[event(sceneId='b')], sources=['actor:updateScript[0]'])
        result = graph({'a': [], 'b': []}, hooks=[hook])
        self.assertEqual(result['reachable'], ['a', 'b'])
        self.assertEqual(result['transitions'][0]['path'], 'actor:updateScript[0]')

    def test_shared_recursive_custom_script_retains_each_owner(self):
        call = event('EVENT_CALL_CUSTOM_EVENT', customEventId='shared')
        custom = [('shared.gbsres', {'id': 'shared', 'script': [call, event(sceneId='c')]})]
        result = graph({'a': [call, event(sceneId='b')], 'b': [call], 'c': []}, custom=custom)
        self.assertEqual([(e['from'], e['to']) for e in result['transitions']],
                         [('a', 'c'), ('a', 'b'), ('b', 'c')])

    def test_bad_destinations_remain_located(self):
        result = graph({'a': [event(sceneId='absent'), event(sceneId='disabled'), event(sceneId=[])]},
                       excluded=[{'scene': 'disabled'}])
        self.assertEqual(len(result['diagnostics']), 3)
        self.assertIn('a:script[1]', result['diagnostics'][1])
        self.assertIn('disabled scene', result['diagnostics'][1])

    def test_missing_start_is_refused(self):
        with self.assertRaisesRegex(CompileError, 'startSceneId'):
            graph({'a': []}, start='missing')

    def test_unreachable_edges_do_not_make_scenes_reachable(self):
        result = graph({'a': [], 'b': [event(sceneId='c')], 'c': []})
        self.assertEqual(result['reachable'], ['a'])
        self.assertEqual(len(result['transitions']), 1)

    def test_real_plan_graph_on_gbs2(self):
        from ngpcraft.pipeline import plan_project
        from tools.reference import REFERENCE
        plan = plan_project(REFERENCE / 'appData/templates/gbs2')
        result = plan['scene_graph']
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(len(result['reachable']) + len(result['unreachable']), len(plan['scenes']))
        # Delivered since 2026-10-01 (tests/test_scene_switch.py); the graph
        # stays a plan report (hooks of undelivered types are not covered).
        self.assertNotIn('EVENT_SWITCH_SCENE', plan['refused'])
        self.assertFalse(result['runtime_delivered'])
