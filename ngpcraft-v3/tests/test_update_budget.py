"""Budget proof with three to six autonomous actors and maximal modal text."""
import contextlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.lab_update_dialogue import build_case, compare_updates, worker_plan, worker_start
from tools.lab_modal_sprite_stress import observe

class WorkerPlanTests(unittest.TestCase):
    def test_worker_plans_preserve_baseline_and_stay_on_screen(self):
        self.assertEqual(worker_plan(0),((96,64),(32,64)))
        self.assertEqual(worker_plan(1),((72,104),(72,48)))
        for k in range(2,14):
            x,y=worker_start(k)
            self.assertEqual(worker_plan(k),((x+16,y),(x-16,y)))
            self.assertLess(y,144)

    def test_invalid_worker_count_is_rejected_before_project_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for n in (1,15):
                with self.assertRaisesRegex(ValueError,'worker_count'):
                    build_case(root,Path('unused'),worker_count=n)
                self.assertFalse((root/'project').exists())

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]
@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator and BIOS')
class UpdateBudgetRomTests(unittest.TestCase):
    def check_budget_case(self, workers):
        artifacts=os.environ.get('NGC_TEST_ARTIFACTS')
        if artifacts:
            parent=Path(artifacts);parent.mkdir(parents=True,exist_ok=True)
            context=contextlib.nullcontext(tempfile.mkdtemp(prefix=f'budget-{workers}-',dir=parent))
        else:
            context=tempfile.TemporaryDirectory()
        with context as tmp:
            root=Path(tmp)
            manifest,_=build_case(root,Path(ENV[0]),rows=11,no_fade=True,interaction=True,worker_count=workers)
            self.assertEqual(len(manifest['scripts']),workers+2)
            result=observe(root,Path(ENV[1]),Path(ENV[2]),frames=720,comparator=compare_updates)
            self.assertTrue(result['pixels_passed'])
            self.assertTrue(result['commits_passed'])
            self.assertTrue(result['schedule_passed'])
            self.assertFalse(result['modal_missed_frames'])
            missed=result['normal_missed_frames_after_dialogue']
            self.assertFalse(missed)
            self.assertTrue(result['passed'])
            self.assertEqual(result['text']['starts'],[24,354])
            self.assertEqual(len(result['interaction_ticks']),2)
            trace=result['worker_trace'];modal=set(result['predicted_modal_ticks'])
            for k in range(workers):
                self.assertGreater(len({tuple(r['positions'][k]) for r in trace if r['tick']>620}),10)
            runs=[]
            for t in sorted(modal):
                if not runs or t!=runs[-1][-1]+1:runs.append([])
                runs[-1].append(t)
            by_tick={r['tick']:r for r in trace}
            self.assertEqual(len(runs),2)
            for run in runs:
                self.assertEqual(len({str(by_tick[t]['positions']) for t in run}),1)

    def test_three_workers_preserve_pixels_schedule_and_cadence(self):
        self.check_budget_case(3)

    def test_four_workers_preserve_pixels_schedule_and_cadence(self):
        self.check_budget_case(4)

    def test_six_workers_preserve_pixels_schedule_and_cadence(self):
        self.check_budget_case(6)

    def test_five_workers_preserve_pixels_schedule_and_cadence(self):
        self.check_budget_case(5)

    def test_overloaded_eight_workers_wait_for_vblank_and_missing_wait_is_detected(self):
        for witness in (None, 'frame_wait'):
            with self.subTest(witness=witness):
                artifacts=os.environ.get('NGC_TEST_ARTIFACTS')
                if artifacts:
                    parent=Path(artifacts);parent.mkdir(parents=True,exist_ok=True)
                    context=contextlib.nullcontext(tempfile.mkdtemp(prefix=f'overload-{witness}-',dir=parent))
                else:
                    context=tempfile.TemporaryDirectory()
                with context as tmp:
                    root=Path(tmp)
                    build_case(root,Path(ENV[0]),rows=11,no_fade=True,interaction=True,
                               worker_count=8,witness=witness)
                    result=observe(root,Path(ENV[1]),Path(ENV[2]),frames=720,comparator=compare_updates)
                    self.assertTrue(result['schedule_passed'])
                    self.assertTrue(result['normal_missed_frames_after_dialogue'])
                    if witness:
                        self.assertTrue(result['normal_commit_overruns_after_dialogue'])
                        self.assertFalse(result['pixels_passed'])
                    else:
                        self.assertTrue(result['pixels_passed'])
                        self.assertTrue(result['commits_passed'])
                        self.assertFalse(result['normal_commit_overruns_after_dialogue'])
                        self.assertFalse(result['modal_missed_frames'])
                        self.assertFalse(result['passed'])

    def test_complete_frame_cache_ignoring_world_position_is_detected(self):
        artifacts=os.environ.get('NGC_TEST_ARTIFACTS')
        if artifacts:
            parent=Path(artifacts);parent.mkdir(parents=True,exist_ok=True)
            context=contextlib.nullcontext(tempfile.mkdtemp(prefix='frame-position-witness-',dir=parent))
        else:
            context=tempfile.TemporaryDirectory()
        with context as tmp:
            root=Path(tmp)
            build_case(root,Path(ENV[0]),rows=11,no_fade=True,interaction=True,
                       worker_count=6,witness='frame_position')
            result=observe(root,Path(ENV[1]),Path(ENV[2]),frames=720,comparator=compare_updates)
            self.assertTrue(result['schedule_passed'])
            self.assertFalse(result['pixels_passed'])
            self.assertTrue(result['sprite_frames_with_mismatch'])

    def test_missing_cached_attribute_restoration_is_detected(self):
        artifacts=os.environ.get('NGC_TEST_ARTIFACTS')
        if artifacts:
            parent=Path(artifacts);parent.mkdir(parents=True,exist_ok=True)
            context=contextlib.nullcontext(tempfile.mkdtemp(prefix='attribute-witness-',dir=parent))
        else:
            context=tempfile.TemporaryDirectory()
        with context as tmp:
            root=Path(tmp)
            # Both R2 and A3 use this hook. Removing it leaves cached mutable
            # tile numbers/flips or hidden priority in the next source frame.
            with patch('ngpcraft.scene_sprites.restore_cached_attributes',side_effect=lambda source:source):
                build_case(root,Path(ENV[0]),rows=11,no_fade=True,interaction=True,worker_count=3)
            result=observe(root,Path(ENV[1]),Path(ENV[2]),frames=720,comparator=compare_updates)
            self.assertFalse(result['pixels_passed'])
            self.assertTrue(result['sprite_frames_with_mismatch'])
