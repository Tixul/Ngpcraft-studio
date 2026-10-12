import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.background import compile_background
from ngpcraft.background_rom import write_export
from ngpcraft.background_stream import clamp_path, stream_source
from ngpcraft.compiler import CompileError
from ngpcraft.scene_sprites import export_scene_rom, frame_ids, sprites_source
from ngpcraft.sprites import HardwareSprite, NgpcSheet, ngpc_sheet
from ngpcraft.toolchain import build_rom
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets

ROOT = Path(__file__).resolve().parents[1]
GBS2 = REFERENCE / "appData/templates/gbs2"
PALETTES = [(0x00F + i, 0x0F0 + i, 0xF00 + i) for i in range(8)]
NAMES = ("player", "cat", "npc_gardener", "elephant")


def distinct_tile(k):
    """Tile k: its base-3 digits on the first row (+1, never 0) and a 2 in the last pixel, so no two
    tiles (or their flips) are equal and scene_sprites.pool_tiles keeps them apart."""
    return tuple(1 + (k // 3 ** i) % 3 if i < 8 else (2 if i == 63 else 1) for i in range(64))


def sheet(frames, tiles=1):
    return NgpcSheet("t", "8x8", tuple(distinct_tile(k) for k in range(tiles)), tuple(frames), (0,), ((0, 0),) * 8)


class SceneSpritesTests(unittest.TestCase):
    def test_oam_words_use_the_position_chain(self):
        frame = (HardwareSprite(-3, -8, 0, False, False, 2), HardwareSprite(5, -8, 1, True, False, 3),
                 HardwareSprite(5, 0, 1, False, True, 7))
        source = sprites_source([sheet([frame], 2)], 300, PALETTES)
        # first: tile 300 (bit 8 set), PR.C front, not chained; others chained both axes
        self.assertIn("0x192Cu, 0x0000u, 0x9F2Du, 0x0008u, 0x5F2Du, 0x0800u", source)
        self.assertIn("2, 3, 7", source)                        # CP.C per sprite
        self.assertIn("3, -3, -8, -3, 12, -8, 7, 0", source)    # count, first offset, extent

    def test_refusals_are_localized(self):
        with self.assertRaisesRegex(CompileError, "8 sprite palettes"):
            sprites_source([], 0, PALETTES[:7])
        with self.assertRaisesRegex(CompileError, "exceed the 512 shared tiles"):
            sprites_source([sheet([], 20)], 500, PALETTES)
        wide = (HardwareSprite(0, 0, 0, False, False, 0), HardwareSprite(88, 0, 0, False, False, 0))
        with self.assertRaisesRegex(CompileError, "t frame 0 spans 96x8 px"):
            sprites_source([sheet([wide])], 0, PALETTES)

    def test_frame_ids_follow_gb_studio_animation_offsets(self):
        loaded = load_sheets(GBS2)
        sheets = [ngpc_sheet(*loaded[n]) for n in ("cat", "player")]
        ids = frame_ids(sheets)
        player = sheets[1]
        start, end = player.animation_offsets[5]
        self.assertEqual(ids[(1, 5, 1)], len(sheets[0].frames) + player.frame_order[start + 1])
        self.assertEqual(len({v for k, v in ids.items() if k[0] == 0}), len(sheets[0].frames))


def steps_for(width, height, sheets, count=90):
    xm, ym = width - 160, height - 152
    ids = frame_ids(sheets)
    frames = lambda s, a: sheets[s].animation_offsets[a][1] - sheets[s].animation_offsets[a][0] + 1
    rom, oracle = [], []
    for t in range(count):
        a = 2 * math.pi * t / count
        cx, cy = int(xm / 2 + (xm / 2 - 4) * math.cos(a)), int(ym / 2 + (ym / 2 - 4) * math.sin(a))
        if t == 50:
            cx, cy = xm - cx, ym - cy
        cx = min(max(cx, 0), xm - 1) + (t & 1)
        walk = 4 + (t // 12) % 4
        actors = [(0, cx + 70, cy + 80, walk, (t // 4) % frames(0, walk)),
                  (1, cx + 74, cy + 84, 1, 0),
                  (2, 150, 120, 3, 0),
                  None if 30 <= t < 40 else (3, cx - 40 + 4 * t % 240, cy + 70, 0, 0)]
        rom.append((cx, cy, [(0xFFFF, 0, 0) if e is None else (ids[(e[0], e[3], e[4])], e[1], e[2])
                             for e in actors]))
        oracle.append([[NAMES[e[0]], e[1], e[2], e[3], e[4]] for e in actors if e])
    return rom, oracle


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                     "requires Toshiba, native emulator and BIOS")
class SceneSpritesRomTests(unittest.TestCase):
    def build(self, root, mutate=None):
        from test_background_stream import synthetic
        width, height = 448, 320
        pixels = synthetic(width, height)
        background = compile_background(width, height, pixels)
        loaded = load_sheets(GBS2)
        sheets = [ngpc_sheet(*loaded[n]) for n in NAMES]
        rom, oracle = steps_for(width, height, sheets)
        export_scene_rom(background, sheets, PALETTES, rom, root / "export")
        if mutate:
            files = {p: (root / "export" / p).read_text(encoding="utf-8")
                     for p in ("src/game.h", "src/game.c", "src/scene_stream.c", "src/scene_sprites.c")}
            files["src/scene_sprites.c"] = mutate(files["src/scene_sprites.c"])
            root.joinpath("export").rename(root / "reference")
            write_export(["streamed_background", "sprites"], files, "mutation witness", {"background": background.report()}, root / "export", "test")
        build = build_rom(root / "export", os.environ["NGC_TEST_TOSHIBA"])
        (root / "scene.rgb444").write_bytes(b"".join(v.to_bytes(2, "little") for v in pixels))
        (root / "source.json").write_text(json.dumps({
            "size_px": [width, height], "expected_scroll": clamp_path(background, [(x, y) for x, y, _ in rom]),
            "project": str(GBS2), "default_sprite_mode": "8x16",
            "sprite_palettes": [list(p) for p in PALETTES], "actors": oracle}))
        return build

    def probe(self, root):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_stream.py"), str(root),
                               "--emulator", os.environ["NGC_TEST_EMULATOR"], "--bios", os.environ["NGC_TEST_BIOS"],
                               "--out", str(root / "probe")], capture_output=True, text=True, timeout=300)
        return proc, json.loads((root / "probe/probe.json").read_text())

    def test_actors_exact_every_frame_over_streamed_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build = self.build(root)
            self.assertIn("_ngc_sprite_draw", build["link_audit"]["public_symbols"])
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(result["actors_checked"])
            self.assertEqual(result["frames_with_mismatch"], [])
            self.assertEqual(result["dropped_frames"], [])
            # No re-blit frame: the whole tick (actors + streaming) fits the vertical blank.
            steady = [r["next_tick_duration_lines"] for a, r in zip(result["rows"][1:], result["rows"])
                      if abs(a["scroll"][0] // 8 - r["scroll"][0] // 8) <= 1
                      and abs(a["scroll"][1] // 8 - r["scroll"][1] // 8) <= 1]
            self.assertLess(max(steady), result["vblank_lines"])

    def test_probe_detects_stale_slot_cache(self):
        """Mutation witness: hidden slots not invalidated -> a returning actor stays hidden."""
        def keep_stale(source):
            text = "        ngc_slot_frame[i] = 0xFFFFu;\n    }\n    ngc_oam_prev"
            self.assertEqual(source.count(text), 1)
            return source.replace(text, "    }\n    ngc_oam_prev")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build(root, keep_stale)
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(result["frames_with_mismatch"])


if __name__ == "__main__":
    unittest.main()
