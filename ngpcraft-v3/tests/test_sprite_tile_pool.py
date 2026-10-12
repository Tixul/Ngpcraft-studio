"""Sprite characters pooled across the sheets of a scene (scene_sprites.pool_tiles, BILAN_MEGAX 2.2)."""
import unittest

from ngpcraft.scene_sprites import _flipped, pool_tiles, sprite_tile_count, sprites_source
from ngpcraft.sprites import HardwareSprite, NgpcSheet


def sheet(name, tiles):
    frame = tuple(HardwareSprite(x=0, y=0, tile=k, hflip=False, vflip=False, palette=0) for k in range(len(tiles)))
    return NgpcSheet(name, "8x8", tuple(tiles), (frame,), (0,), (0,) * 8)


A = tuple((x + y) % 4 for y in range(8) for x in range(8))
B = tuple((x * y) % 4 for y in range(8) for x in range(8))


class SpriteTilePoolTests(unittest.TestCase):
    def test_flips_are_involutions(self):
        for h, v in ((False, False), (True, False), (False, True), (True, True)):
            self.assertEqual(_flipped(_flipped(A, h, v), h, v), A)

    def test_repeated_and_flipped_patterns_share_one_character(self):
        sheets = [sheet("one", [A, B]), sheet("two", [B, _flipped(A, True, False), _flipped(B, False, True)])]
        pool, maps = pool_tiles(sheets)
        self.assertEqual(pool, [A, B])
        self.assertEqual(maps[1], [(1, False, False), (0, True, False), (1, False, True)])
        self.assertEqual(sprite_tile_count(sheets), 2)

    def test_the_oam_word_toggles_the_flips_of_a_shared_character(self):
        sheets = [sheet("one", [A]), sheet("two", [_flipped(A, True, True)])]
        source = sprites_source(sheets, 100, [(0, 0, 0)] * 8)
        # one character at 100; the second sheet draws it flipped both ways (H.F 0x80, V.F 0x40)
        self.assertIn("spr_tiles", source)
        words = source.split("spr_words")[1].split("}")[0]
        attrs = [int(w.strip().rstrip("u"), 0) for w in words.split("{")[1].split(",") if w.strip()]
        self.assertEqual(attrs[0] & 0xFF, 100)
        self.assertEqual(attrs[2] & 0xFF, 100)
        self.assertEqual((attrs[2] >> 8) & 0xC0, 0xC0)
        self.assertEqual((attrs[0] >> 8) & 0xC0, 0)

    def test_distinct_patterns_keep_the_previous_layout(self):
        sheets = [sheet("one", [A]), sheet("two", [B])]
        self.assertEqual(pool_tiles(sheets)[0], [A, B])


def native(name, tiles, palettes, uses):
    """uses: per tile, the palette it is drawn with."""
    frame = tuple(HardwareSprite(x=0, y=0, tile=k, hflip=False, vflip=False, palette=p) for k, p in enumerate(uses))
    return NgpcSheet(name, "8x8", tuple(tiles), (frame,), (0,), (0,) * 8, palettes=tuple(palettes))


def pixels(sheet):
    """Colour of each pixel of each drawn sprite (None = transparent)."""
    return [tuple(None if v == 0 else sheet.palettes[s.palette][v - 1] for v in sheet.tiles[s.tile])
            for frame in sheet.frames for s in frame]


class AlignPalettesTests(unittest.TestCase):
    """sprites.align_palettes: same drawing, other colours (docs/M6_SPRITE_TILES.md)."""
    RED = (0x00F, 0x08F, 0xFFF)
    BLUE = (0xF00, 0xF80, 0x0F0)

    def recoloured(self):
        # the same drawing as "red", its colours sorted otherwise: other indices
        swap = {1: 3, 2: 1, 3: 2}
        return (native("red", [A], [self.RED], [0]),
                native("blue", [tuple(swap.get(v, 0) for v in A)],
                       [tuple(self.BLUE[i - 1] for i in (2, 3, 1))], [0]))

    def test_recoloured_sheet_shares_its_drawing(self):
        from ngpcraft.sprites import align_palettes
        sheets = self.recoloured()
        self.assertEqual(sprite_tile_count(sheets), 2)
        aligned = align_palettes(sheets)
        self.assertEqual(sprite_tile_count(aligned), 1)
        for before, after in zip(sheets, aligned):         # same pixels
            self.assertEqual(pixels(before), pixels(after))
        self.assertEqual(aligned[0], sheets[0])            # the first use keeps its order

    def test_no_gain_no_change_and_shared_tiles_left_alone(self):
        from ngpcraft.sprites import align_palettes
        plain = (native("a", [A], [self.RED], [0]), native("b", [B], [self.BLUE], [0]))
        self.assertEqual(align_palettes(plain), plain)
        # a tile drawn with two palettes: neither palette is reordered
        red, blue = self.recoloured()
        both = NgpcSheet("both", "8x8", blue.tiles, ((HardwareSprite(0, 0, 0, False, False, 0),
                                                      HardwareSprite(8, 0, 0, False, False, 1)),),
                         (0,), (0,) * 8, palettes=(blue.palettes[0], (0x123, 0x456, 0x789)))
        self.assertEqual(align_palettes((red, both))[1], both)

    def test_identical_palettes_stay_identical(self):
        from ngpcraft.sprites import align_palettes
        red, blue = self.recoloured()
        again = native("blue2", blue.tiles, blue.palettes, [0])
        out = align_palettes((red, blue, again))
        self.assertEqual(out[1].palettes, out[2].palettes)
        self.assertEqual(sprite_tile_count(out), 1)
        for before, after in zip((red, blue, again), out):
            self.assertEqual(pixels(before), pixels(after))


if __name__ == "__main__":
    unittest.main()
