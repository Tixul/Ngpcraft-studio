"""Possible OAM frame starts, allocation and omission of the A3/R2 frame cache."""
from types import SimpleNamespace
import unittest
from ngpcraft.compiler import CompileError
from ngpcraft.sprite_priority import frame_mask_cache_layout


class FrameMaskLayoutTests(unittest.TestCase):
    def test_all_possible_starts_fit_with_empty_and_odd_sized_frames(self):
        for lengths, stride in (((0, 2, 4), 2), ((3, 6), 3), ((1, 4), 1), ((8,), 8)):
            with self.subTest(lengths=lengths):
                sheets=[SimpleNamespace(frames=[tuple(range(n)) for n in lengths])]
                r=frame_mask_cache_layout(sheets,64,True)
                self.assertEqual(r['frame_cache_stride'],stride)
                self.assertEqual(r['frame_cache_bytes'],r['frame_cache_slots']*8)
                reachable={0}
                for start in range(64):
                    if start in reachable:
                        for n in lengths:
                            if n and start+n<64:
                                reachable.add(start+n)
                for start in reachable:
                    self.assertEqual(start%stride,0)
                    self.assertLess(start//stride,r['frame_cache_slots'])
                if stride>1:
                    self.assertLess(r['frame_cache_bytes'],64*8)

    def test_no_shared_text_allocates_no_frame_cache(self):
        self.assertEqual(frame_mask_cache_layout([],64,False),
                         {'frame_cache_stride':0,'frame_cache_slots':0,'frame_cache_bytes':0})

    def test_empty_metasprites_are_not_given_an_invented_stride(self):
        with self.assertRaisesRegex(CompileError,'nonempty metasprites'):
            frame_mask_cache_layout([SimpleNamespace(frames=[()])],64,True)
