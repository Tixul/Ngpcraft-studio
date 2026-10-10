"""Concurrent updates and UI suspension against GBVM's independent oracle."""
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from tools.gbvm_actors import ScriptScheduler, plain, op_marker
from tools.lab_update_dialogue import build_case, compare_updates
from tools.lab_modal_sprite_stress import observe


class SuspendedQuantumTests(unittest.TestCase):
    def test_modal_call_retains_lock_context_and_remaining_quantum(self):
        def modal(m,s,a,t,th):
            yield
            yield
            return False,False
        scheduler=ScriptScheduler(plain(15)+[modal,op_marker(17),op_marker(18)],{})
        thread=scheduler.init
        scene=SimpleNamespace(locked=False)
        for tick in (1,2,3):
            scheduler.step(None,scene,[],tick)
            self.assertTrue(scene.locked)
        self.assertEqual(thread.mark_log,[(3,17)])
        scheduler.step(None,scene,[],4)
        self.assertEqual(thread.mark_log,[(3,17),(4,18)])
        self.assertFalse(scene.locked)


ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator and BIOS')
class UpdateDialogueRomTests(unittest.TestCase):
    def run_case(self,*,rows=4,witness=None,no_fade=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            manifest,_=build_case(root,Path(ENV[0]),rows=rows,witness=witness,no_fade=no_fade)
            self.assertEqual(len(manifest['scripts']),4)
            return observe(root,Path(ENV[1]),Path(ENV[2]),frames=1160,comparator=compare_updates)

    def check_valid(self,result):
        self.assertTrue(result['passed'],result['sprite_frames_with_mismatch'][:3])
        self.assertEqual(result['predicted_modal_starts'],result['text']['starts'])
        self.assertEqual(len(result['predicted_modal_starts']),2)
        self.assertGreater(result['text']['priority_pixels_checked'],0)
        trace={r['tick']:r['positions'] for r in result['worker_trace']}
        runs=[]
        for tick in result['predicted_modal_ticks']:
            if not runs or tick!=runs[-1][-1]+1:runs.append([])
            runs[-1].append(tick)
        self.assertEqual(len(runs),2)
        for run in runs:
            self.assertEqual(len({str(trace[t]) for t in run}),1)
        for worker in (0,1):
            self.assertGreater(len({tuple(v[worker]) for t,v in trace.items() if t<runs[0][0]}),10)
            self.assertGreater(len({tuple(v[worker]) for t,v in trace.items() if t>runs[-1][-1]}),10)

    def test_two_workers_pause_and_resume_four_row_dialogues(self):
        self.check_valid(self.run_case())

    def test_two_workers_pause_and_resume_maximum_dialogues(self):
        self.check_valid(self.run_case(rows=11))

    def test_no_fade_four_row_dialogues(self):
        self.check_valid(self.run_case(no_fade=True))

    def test_no_fade_maximum_dialogues(self):
        self.check_valid(self.run_case(rows=11,no_fade=True))

    def test_moving_cache_must_include_world_position(self):
        result=self.run_case(witness='position')
        self.assertFalse(result['pixels_passed'])
        self.assertTrue(result['sprite_frames_with_mismatch'])

    def test_forced_movement_during_modal_is_detected(self):
        result=self.run_case(witness='modal_position')
        self.assertFalse(result['pixels_passed'])
        self.assertTrue(result['sprite_frames_with_mismatch'])
        self.assertLess(result['sprite_frames_with_mismatch'][0]['frame'],result['predicted_modal_ticks'][-1])
