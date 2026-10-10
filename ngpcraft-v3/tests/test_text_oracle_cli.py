"""Oracle command: scene allocation, real failure codes and coverage."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from tools import check_text as oracle


def scene(sid, name):
    return {'id': sid, 'name': name, 'script': [
        {'command': 'EVENT_TEXT', 'args': {'text': ['Page']}}]}


def report(sid, case):
    return {'id': sid, 'planes': {'case': case},
            'view': {'top_band': 4 if case == 'A' else 0, 'height': 144 if case == 'A' else 152}}


def good():
    return {'compared_frames': 10, 'wrong_frames': [], 'background_errors': [], 'sprite_mask_errors': []}


class TextOracleCliTests(unittest.TestCase):
    def execute(self, reports, scenes, verdicts, options=(), single=False, text=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'export').mkdir()
            engine = dict(reports[0]) if single else {'scenes': reports}
            if single:
                engine.pop('id')
            (root / 'export/export.json').write_text(json.dumps(
                {'features': ['text'] if text else [], 'scene_engine': engine}), encoding='utf-8')
            project = SimpleNamespace(scenes=scenes, settings={'startSceneId': scenes[0]['id']})
            out, err = StringIO(), StringIO()
            with patch.object(oracle, 'load_project', return_value=project), \
                 patch.object(oracle, 'check', side_effect=verdicts) as checker, \
                 patch('sys.argv', ['check_text', str(root), *options]), \
                 redirect_stdout(out), redirect_stderr(err):
                try:
                    code = oracle.main()
                except SystemExit as error:
                    code = error.code
            return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue(), checker.call_args_list

    def test_single_case_b_needs_no_manual_mapping_flags(self):
        code, result, _, calls = self.execute([report('town', 'B')], [scene('town', 'Sample Town')], [good()], single=True)
        self.assertEqual(code, 0)
        self.assertTrue(result['passed'])
        self.assertEqual(result['case'], 'B')
        self.assertEqual(calls[0].kwargs['scene_id'], 'town')
        self.assertEqual((calls[0].kwargs['offset'], calls[0].kwargs['bottom'], calls[0].kwargs['committed_map']),
                         (8, 152, True))

    def test_all_exported_dialogue_scenes_are_checked(self):
        code, result, _, calls = self.execute([report('town', 'B'), report('cave', 'A')],
            [scene('town', 'Town'), scene('cave', 'Cave')], [good(), good()])
        self.assertEqual(code, 0)
        self.assertEqual(len(result['scenes']), 2)
        self.assertEqual((calls[1].kwargs['offset'], calls[1].kwargs['bottom'], calls[1].kwargs['committed_map']),
                         (4, 148, False))

    def test_any_pixel_decor_or_sprite_error_returns_failure(self):
        for field in ('wrong_frames', 'background_errors', 'sprite_mask_errors'):
            verdict = good()
            verdict[field] = [{'frame': 10}]
            with self.subTest(field=field):
                code, result, _, _ = self.execute([report('town', 'B')], [scene('town', 'Town')], [verdict])
                self.assertEqual(code, 1)
                self.assertFalse(result['passed'])

    def test_no_compared_pixels_cannot_pass(self):
        verdict = good()
        verdict['compared_frames'] = 0
        code, result, _, _ = self.execute([report('town', 'B')], [scene('town', 'Town')], [verdict])
        self.assertEqual(code, 1)
        self.assertIn('no dialogue pixels', result['failure_reason'])

    def test_failure_in_later_scene_cannot_be_hidden_by_first_scene(self):
        bad = good()
        bad['background_errors'] = [{'frame': 22}]
        code, result, _, _ = self.execute([report('town', 'B'), report('cave', 'A')],
            [scene('town', 'Town'), scene('cave', 'Cave')], [good(), bad])
        self.assertEqual(code, 1)
        self.assertFalse(result['passed'])
        self.assertTrue(result['scenes'][0]['passed'])
        self.assertFalse(result['scenes'][1]['passed'])

    def test_identical_names_still_select_distinct_scene_ids(self):
        reports = [report('first', 'A'), report('second', 'B')]
        scenes = [scene('first', 'Room'), scene('second', 'Room')]
        code, result, _, calls = self.execute(reports, scenes, [good(), good()])
        self.assertEqual(code, 0)
        self.assertEqual([call.kwargs['scene_id'] for call in calls], ['first', 'second'])
        code, result, _, calls = self.execute(reports, scenes, [good()], ['--scene-id', 'second'])
        self.assertEqual(code, 0)
        self.assertEqual(result['scene_id'], 'second')
        self.assertEqual(len(calls), 1)

    def test_ambiguous_name_or_missing_text_runtime_is_an_explicit_error(self):
        reports = [report('first', 'A'), report('second', 'B')]
        scenes = [scene('first', 'Room'), scene('second', 'Room')]
        code, _, error, calls = self.execute(reports, scenes, [], ['--scene-folder', 'Room'])
        self.assertEqual(code, 2)
        self.assertIn('ambiguous', error)
        self.assertEqual(calls, [])
        code, _, error, calls = self.execute(reports, scenes, [], text=False)
        self.assertEqual(code, 2)
        self.assertIn('no text runtime', error)
        self.assertEqual(calls, [])

    def test_explicit_scene_filter_is_case_insensitive(self):
        code, result, _, calls = self.execute([report('town', 'B'), report('cave', 'A')],
            [scene('town', 'Town'), scene('cave', 'Dungeon/Cave')], [good()], ['--scene-folder', 'CAVE'])
        self.assertEqual(code, 0)
        self.assertEqual(result['scene_id'], 'cave')
        self.assertEqual(len(calls), 1)

    def test_disabled_dialogues_and_end_tails_are_not_selected(self):
        events = [{'command': 'EVENT_TEXT', 'args': {'text': 'Ignored', '__comment': True}},
                  {'command': 'EVENT_END', 'args': {}},
                  {'command': 'EVENT_TEXT', 'args': {'text': 'Unreachable'}}]
        self.assertIsNone(oracle.first_dialogue(events))
        events = [{'command': 'EVENT_LOOP', 'children': {'true': [
            {'command': 'EVENT_TEXT', 'args': {'text': 'Nested'}}]}}]
        self.assertEqual(oracle.first_dialogue(events)['text'], 'Nested')
