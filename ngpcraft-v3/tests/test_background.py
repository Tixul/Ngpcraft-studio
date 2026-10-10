import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.background import compile_background, encode_tile
from ngpcraft.background_rom import background_source, export_background_rom
from ngpcraft.compiler import CompileError, analyze
from ngpcraft.emitter import generate
from ngpcraft.toolchain import build_rom

ROOT = Path(__file__).resolve().parents[1]


def decode(asset):
    """Test oracle: decode packed map words and VRAM bytes like the hardware."""
    result = []
    for y in range(asset.height):
        for x in range(asset.width):
            color = asset.backdrop
            for layer in reversed(range(len(asset.planes))):
                entry = asset.maps[layer][(y//8)*(asset.width//8)+x//8]
                tx, ty = x % 8, y % 8
                if entry & 0x8000:
                    tx = 7-tx
                if entry & 0x4000:
                    ty = 7-ty
                tile = asset.tiles[(entry & 511)-asset.tile_base]
                word = tile[ty*2] + tile[ty*2+1]*256
                index = (word >> (2*(7-tx))) & 3
                if index:
                    color = asset.palettes[layer][(entry >> 9) & 15][index-1]
            result.append(color)
    return tuple(result)


def two_tiles():
    return tuple(((1,2,3,4) if x < 8 else (5,6,7,8))[(x+y*2) & 3]
                 for y in range(8) for x in range(16))


class BackgroundTests(unittest.TestCase):
    def test_snk_pixel_byte_order(self):
        self.assertEqual(encode_tile(tuple([0,1,2,3,3,2,1,0]*8)), bytes([0xE4,0x1B]*8))
        with self.assertRaises(CompileError):
            encode_tile((4,)*64)

    def test_any_source_color_can_become_backdrop(self):
        # Different source color-zero equivalents, shared color 9.
        pixels = tuple(((1,2,3,9) if x < 8 else (4,5,6,9))[(x+y) & 3]
                       for y in range(8) for x in range(16))
        asset = compile_background(16,8,pixels,planes=(2,))
        self.assertEqual(asset.backdrop,9)
        self.assertEqual(asset.planes,(2,))
        self.assertEqual(decode(asset),pixels)

    def test_two_planes_and_plane_conflict(self):
        pixels = two_tiles()
        for planes in ((1,2),(2,1)):
            asset = compile_background(16,8,pixels,planes=planes)
            self.assertEqual(asset.planes,planes)
            self.assertEqual(len(asset.complement_cells),1)
            self.assertEqual(decode(asset),pixels)
        with self.assertRaisesRegex(CompileError,"fourth opaque color.*another plane"):
            compile_background(16,8,pixels,planes=(1,),where="scene.house")

    def test_flips_share_one_pattern(self):
        tile = tuple(1 if (x == y or (x==1 and y==3)) else 2 for y in range(8) for x in range(8))
        pixels = tuple(tile[y*8+x] if x<8 else tile[y*8+15-x] for y in range(8) for x in range(16))
        asset = compile_background(16,8,pixels,backdrop=0,tile_base=31,tile_limit=481)
        self.assertEqual(len(asset.tiles),2)  # blank plus asymmetric motif
        self.assertEqual(asset.maps[0][0] & 511, asset.maps[0][1] & 511)
        self.assertNotEqual(asset.maps[0][0] & 0x8000, asset.maps[0][1] & 0x8000)
        self.assertEqual(decode(asset),pixels)

    def test_validation_is_localized(self):
        for kwargs in ({"width":7},{"height":0},{"pixels":(4096,)*64},
                       {"pixels":(True,)*64},{"planes":(1,1)}, {"palette_limit":17},
                       {"tile_base":511,"tile_limit":2}):
            case = dict(width=8,height=8,pixels=(1,)*64,where="scene.test.bg")
            case.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(CompileError,r"scene.test.bg:"):
                compile_background(**case)
        with self.assertRaisesRegex(CompileError,"more than 4 colors"):
            compile_background(8,8,tuple(i%5 for i in range(64)))

    def test_tile_budget_at_exact_hardware_boundary(self):
        rng = random.Random(17)
        tiles = [tuple(rng.randrange(1,4) for _ in range(64)) for _ in range(512)]
        def pixels(ts):
            return tuple(ts[(y//8)*32+x//8][(y%8)*8+x%8] for y in range(128) for x in range(256))
        asset = compile_background(256,128,pixels(tiles[:-1]+[tiles[0]]),backdrop=0)
        self.assertEqual(len(asset.tiles),512)
        with self.assertRaisesRegex(CompileError,"tile budget 512 exceeded"):
            compile_background(256,128,pixels(tiles),backdrop=0)

    def test_palette_budget_rejects_instead_of_wrapping(self):
        pixels = tuple(1+(x//8)*3+((x+y)%3) for y in range(8) for x in range(17*8))
        with self.assertRaisesRegex(CompileError,"17 palettes, budget 16"):
            compile_background(136,8,pixels,backdrop=0)

    def test_packed_reconstruction_randomized(self):
        rng = random.Random(29)
        for _ in range(20):
            palettes = [tuple(rng.sample(range(32),4)) for _ in range(3)]
            pixels = tuple(palettes[x//8][rng.randrange(4)] for y in range(8) for x in range(24))
            self.assertEqual(decode(compile_background(24,8,pixels)),pixels)

    def test_selected_runtime_and_no_implicit_screen_resize(self):
        asset = compile_background(160,152,(7,)*(160*152))
        source = background_source(asset)
        self.assertNotIn("bg_map2",source)
        self.assertNotIn("bg_pal2",source)
        with self.assertRaisesRegex(CompileError,"160x152"):
            background_source(compile_background(160,144,(7,)*(160*144)))
        empty = generate(analyze({"schema":"ngpcraft-ir/0.1", "start":"empty", "scenes":[{"id":"empty"}]}),"rom")
        self.assertNotIn("src/scene_background.c",empty)
        self.assertNotIn("static_background",empty["export.json"])
        with tempfile.TemporaryDirectory() as tmp:
            manifest = export_background_rom(asset,tmp)
            self.assertIn("src/scene_background.c",manifest["sources"])
            with self.assertRaisesRegex(CompileError,"new or empty"):
                export_background_rom(asset,tmp)


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA","NGC_TEST_EMULATOR","NGC_TEST_BIOS")),
                     "requires Toshiba, native emulator and BIOS")
class BackgroundRomTests(unittest.TestCase):
    def test_one_and_two_planes_on_native_core(self):
        for planes, palette_count in (((1,),1),((1,2),2),((2,1),2)):
            colors = ((1,2,3,4),(5,6,7,8))[:palette_count]
            pixels = tuple(colors[(x//8+y//8)%len(colors)][(x+y*2)&3]
                           for y in range(152) for x in range(160))
            with self.subTest(planes=planes), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                export_background_rom(compile_background(160,152,pixels,planes=planes),root/"export")
                build = build_rom(root/"export",os.environ["NGC_TEST_TOSHIBA"])
                self.assertIn("_ngc_background_load",build["link_audit"]["public_symbols"])
                (root/"expected.rgb444").write_bytes(b"".join(v.to_bytes(2,"little") for v in pixels))
                proc = subprocess.run([sys.executable,"-B",str(ROOT/"tools/probe_background.py"),str(root),
                    "--emulator",os.environ["NGC_TEST_EMULATOR"],"--bios",os.environ["NGC_TEST_BIOS"],
                    "--out",str(root/"probe")],capture_output=True,text=True,timeout=90)
                self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
                self.assertTrue(json.loads((root/"probe/probe.json").read_text())["passed"])


if __name__ == "__main__":
    unittest.main()
