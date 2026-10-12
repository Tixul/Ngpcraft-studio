"""Save journal blocks on 4, 8 and 16 Mbit chips (docs/M5_SAVE.md, toolchain.save_image_limit)."""
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.toolchain import save_image_limit


class SaveImageLimitTests(unittest.TestCase):
    def test_smallest_chip_holding_the_image(self):
        # runtime/save_flash.c ngc_flash_top - 8 KiB: where block 0 of the journal starts.
        for size, chip, limit in ((0x40000, 0x80000, 0x78000), (0x78000, 0x80000, 0x78000),
                                  (0x80000, 0x80000, 0x78000), (0x80001, 0x100000, 0xF8000),
                                  (0xB5D28, 0x100000, 0xF8000), (0x100001, 0x200000, 0x1F8000)):
            with self.subTest(size=hex(size)):
                self.assertEqual(save_image_limit(size), (chip, limit))

    def test_images_reaching_the_blocks(self):
        # a 4 Mbit image over 0x78000, and an 8 Mbit one over 0xF8000, would be erased by a save
        self.assertGreater(0x78001, save_image_limit(0x78001)[1])
        self.assertGreater(0xF8001, save_image_limit(0xF8001)[1])
        with self.assertRaises(CompileError):
            save_image_limit(0x200001)


if __name__ == "__main__":
    unittest.main()
