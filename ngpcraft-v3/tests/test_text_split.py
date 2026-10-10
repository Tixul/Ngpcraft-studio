"""Case B raster foundation: streaming ring allocation and native split."""
import os
from pathlib import Path
import tempfile
import unittest
from tools.lab_text_split import prepare, path, build, probe

class SplitAllocationTests(unittest.TestCase):
    def test_scratch_rows_are_disjoint_from_streaming_ring_even_at_wrap(self):
        for ty in range(64):
            active = {(ty+d)&31 for d in range(-1,20)}
            free = {(ty+20+d)&31 for d in range(11)}
            self.assertFalse(active & free)
            self.assertEqual(len(active | free),32)

    def test_town_complement_and_text_fit_palette_and_tile_budgets(self):
        _,_,bg,_,_,_,data,base,tiles,cells=prepare()
        self.assertTrue(bg.complement_cells)
        self.assertLessEqual(len(bg.tiles),384)
        self.assertLessEqual(base+len(data.palettes),16)
        self.assertEqual(len(cells),80)
        self.assertEqual(len(tiles),1024)
        self.assertTrue(all((cell&511)>=384 for cell in cells))

env=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')]
@unittest.skipUnless(all(env),'requires Toshiba, native emulator and BIOS')
class SplitRomTests(unittest.TestCase):
    def run_case(self,mutate):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);build(out,Path(env[0]),mutate)
            return probe(out,Path(env[1]),Path(env[2]))
    def test_shared_plane_remains_exact_during_scroll_and_window_motion(self):
        self.assertEqual(self.run_case(False)['wrong_frames'],[])
    def test_wrong_split_offset_is_detected(self):
        self.assertTrue(self.run_case(True)['wrong_frames'])
