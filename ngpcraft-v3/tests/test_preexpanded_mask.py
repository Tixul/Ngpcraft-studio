"""Mask-table geometry, independent expected row bits and bounded selection."""
import unittest
from ngpcraft.sprite_priority import _mask_plan, _preexpanded_words

class PreexpandedMaskTests(unittest.TestCase):
    def test_single_opaque_pixel_includes_negative_tile_origins(self):
        mask=[128]+[0]*7
        plan=_mask_plan(8,8,(0,0,0,0))
        self.assertEqual(plan["origin_px"],[-7,-7])
        self.assertEqual(plan["position_stride_px"],8)
        words=_preexpanded_words(mask,8,8,plan)
        self.assertEqual(len(words)*2,plan["rom_bytes"])
        # A tile starting at (-7,-7) sees that pixel at column/row 7.
        self.assertEqual(words[:8],[65535]*7+[65532])
        # At world (0,0), it is at column/row zero, clearing bits 15..14.
        index=(7*8+7)*8
        self.assertEqual(words[index:index+8],[16383]+[65535]*7)

    def test_unaligned_pair_crosses_bytes_and_masks_only_opaque_bits(self):
        mask=[1,128]+[0]*14
        plan=_mask_plan(16,8,(7,0,8,0))
        words=_preexpanded_words(mask,16,8,plan)
        # At world (4,0), opaque pixels 7 and 8 occupy columns 3 and 4.
        index=(7*plan["position_stride_px"]+4)*8
        self.assertEqual(words[index:index+8],[65535 ^ (3<<8) ^ (3<<6)]+[65535]*7)

    def test_large_or_empty_regions_keep_byte_rows(self):
        for bounds in ((0,0,191,159),(0,0,-1,-1),(0,32767,0,32768)):
            with self.subTest(bounds=bounds):
                plan=_mask_plan(192,160,bounds)
                self.assertEqual(plan["strategy"],"byte_rows")
                self.assertEqual(plan["rom_bytes"],3840)
