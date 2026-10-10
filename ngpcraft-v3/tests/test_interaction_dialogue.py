"""Player interactions with TEXT, independent scheduler and native pixel checks."""
import contextlib
import os
from pathlib import Path
import tempfile
import unittest
from tools.lab_update_dialogue import build_case, compare_updates
from tools.lab_modal_sprite_stress import observe

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator and BIOS')
class InteractionDialogueRomTests(unittest.TestCase):
    def run_case(self,rows=4,witness=None):
        artifacts=os.environ.get('NGC_TEST_ARTIFACTS')
        if artifacts:
            parent=Path(artifacts);parent.mkdir(parents=True,exist_ok=True)
            context=contextlib.nullcontext(tempfile.mkdtemp(prefix=f'interaction-{rows}-',dir=parent))
        else:
            context=tempfile.TemporaryDirectory()
        with context as tmp:
            root=Path(tmp)
            manifest,_=build_case(root,Path(ENV[0]),rows=rows,no_fade=True,interaction=True,witness=witness)
            self.assertIn('actor_interactions',manifest['features'])
            self.assertNotIn('fades',manifest['features'])
            self.assertEqual(len(manifest['scripts']),4)
            result=observe(root,Path(ENV[1]),Path(ENV[2]),frames=720,comparator=compare_updates)
            result['artifacts']=str(root)
            return result

    def check_valid(self,result):
        self.assertTrue(result['passed'],result['sprite_frames_with_mismatch'][:3])
        starts=result['predicted_modal_starts']
        self.assertEqual(starts,result['text']['starts'])
        self.assertEqual(len(starts),2)
        self.assertEqual([t+1 for t in result['interaction_ticks']],starts)
        # Holding the triggering A does not dismiss the first page or open again.
        self.assertLess(starts[0],80)
        self.assertGreater(starts[1],350)
        trace={r['tick']:r for r in result['worker_trace']}
        runs=[]
        for t in result['predicted_modal_ticks']:
            if not runs or t!=runs[-1][-1]+1:runs.append([])
            runs[-1].append(t)
        self.assertEqual(len(runs),2)
        for run in runs:
            self.assertGreater(len(run),180)
            self.assertTrue(all(trace[t]['locked'] for t in run))
            self.assertEqual(len({str(trace[t]['positions']) for t in run}),1)
            self.assertEqual(len({str(trace[t]['player']) for t in run}),1)
        for k in (0,1):
            self.assertGreater(len({tuple(r['positions'][k]) for t,r in trace.items() if t>runs[-1][-1]}),10)
        self.assertGreater(len({tuple(r['player']) for t,r in trace.items() if t>=620}),10)

    def test_four_row_interactions_freeze_and_resume(self):
        self.check_valid(self.run_case())

    def test_maximum_interactions_freeze_and_resume(self):
        self.check_valid(self.run_case(rows=11))

    def test_modal_player_update_is_detected(self):
        result=self.run_case(rows=11,witness='modal_input')
        self.assertFalse(result['pixels_passed'])
        self.assertTrue(result['sprite_frames_with_mismatch'])
