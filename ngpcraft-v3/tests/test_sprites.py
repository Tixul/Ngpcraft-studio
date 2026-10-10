"""Sprite compiler: GB Studio's own optimiseTiles tests replayed from their
file, and the anchor of every frame of every gbs2 sheet checked against the
editor's independent drawing rule (ActorView + MetaspriteCanvas.worker)."""
import json
import re
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.sprites import (HardwareSprite, animation_map, editor_frame_origin, ngpc_sheet,
                              optimise_tiles, to_engine_order)
from tools.reference import REFERENCE
from tools.sprite_png import editor_frame, load_sheets, read_indexed

SPRITE_TEST = REFERENCE / "test/compiler/sprites/compileSprites.test.ts"
FILES = REFERENCE / "test/compiler/_files/assets/sprites"
GBS2 = REFERENCE / "appData/templates/gbs2"


def ascii_to_data(text):
    return [{".": 1, "x": 2, "X": 3}.get(c, 0)
            for line in text.strip().split("\n") for c in line.strip()]


def js_to_json(text):
    text = re.sub(r"(\w+):", r'"\1":', text)
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def optimise_cases():
    source = SPRITE_TEST.read_text(encoding="utf-8")
    section = source[source.index('describe("Optimise Tiles"'):]
    starts = [(m.start(), m.group(1)) for m in re.finditer(r'test\("([^"]+)", async \(\) => \{', section)]
    for (start, name), end in zip(starts, [s for s, _ in starts[1:]] + [len(section)]):
        yield name, section[start:end]


ASSERTIONS = (
    ("count", r"expectValidTilesOfLength\(result\.tiles, (\d+)\);"),
    ("defined", r"expect\(result\.lookup\.(\w+)\?\.tile\)\.(toBeUndefined|toBeDefined)\(\);"),
    ("flip", r"expect\(result\.lookup\.(\w+)\?\.(flipX|flipY)\)\.toBe\((true|false)\);"),
    ("same", r"expect\((\w+)Index\)\.(?:toEqual|toBe)\((\w+)Index\);"),
    ("ascii", r"expectTileMatchAscii\(\s*result\.tiles\[([^\]]+)\]\?\.data,\s*`([^`]*)`,?\s*\);"),
    ("ascii", r"expect\(Array\.from\(result\.tiles\[([^\]]+)\]\?\.data \?\? \[\]\)\)\.toEqual\(\s*"
              r"asciiToData\(`([^`]*)`\),?\s*\);"),
)


class GbStudioOptimiseTilesReplay(unittest.TestCase):
    def test_every_optimise_tiles_case(self):
        cases = list(optimise_cases())
        self.assertEqual(len(cases), 10)  # 8 in "Mode: 8x16", 2 in "Mode: 8x8"
        for name, block in cases:
            with self.subTest(name):
                png = re.search(r'"([\w.]+\.png)"', block).group(1)
                call = re.search(r"optimiseTiles\(\s*filename,\s*(\d+),\s*(\d+),\s*(\[.*?\])\s*,\s*"
                                 r'"(8x8|8x16)",?\s*\)', block, re.S)
                width, height, tiles, mode = int(call[1]), int(call[2]), js_to_json(call[3]), call[4]
                tiles_out, lookup = optimise_tiles(read_indexed(FILES / png), width, height, tiles, mode)
                index = lambda tid: lookup[tid][0] if lookup.get(tid) else 0
                rest = block[call.end():]
                checked = 0
                for kind, pattern in ASSERTIONS:
                    for m in re.finditer(pattern, rest):
                        checked += 1
                        if kind == "count":
                            self.assertEqual(len(tiles_out), int(m[1]))
                            self.assertTrue(all(len(t.data) == 64 for t in tiles_out))
                        elif kind == "defined":
                            self.assertEqual(lookup.get(m[1]) is None, m[2] == "toBeUndefined")
                        elif kind == "flip":
                            self.assertEqual(lookup[m[1]][1 if m[2] == "flipX" else 2], m[3] == "true")
                        elif kind == "same":
                            self.assertEqual(index(m[1]), index(m[2]))
                        else:
                            expr = re.fullmatch(r"(\d+)|(\w+)Index(?: \+ (\d+))?", m[1].strip())
                            n = int(expr[1]) if expr[1] else index(expr[2]) + int(expr[3] or 0)
                            self.assertEqual(tiles_out[n].data, ascii_to_data(m[2]), m[1])
                        rest = rest.replace(m[0], "", 1)
                self.assertGreater(checked, 1)
                self.assertNotIn("expect", rest, "unrecognised assertion in GB Studio test")


def hardware_canvas(frame, sheet_tiles, sx, sy):
    """Draw hardware sprites like the K2GE: sprite 0 is written first and wins."""
    out = {}
    for spr in frame:
        tile = sheet_tiles[spr.tile]
        for y in range(8):
            for x in range(8):
                value = tile[(7 - y if spr.vflip else y) * 8 + (7 - x if spr.hflip else x)]
                key = (sx + spr.x + x, sy + spr.y + y)
                if value and key not in out:
                    out[key] = (value, spr.palette)
    return out


class SpriteAnchorTests(unittest.TestCase):
    """Every frame of every gbs2 sheet, all 8 engine animations per state."""

    def test_hardware_sprites_match_editor_drawing(self):
        settings = json.loads((GBS2 / "project/settings.gbsres").read_text(encoding="utf-8"))
        default_mode = settings["spriteMode"]
        sheets = load_sheets(GBS2)
        self.assertGreater(len(sheets), 40)
        checked = 0
        for name, (sheet, image) in sheets.items():
            with self.subTest(name):
                ngpc = ngpc_sheet(sheet, image, default_mode)
                mode = sheet.get("spriteMode") or default_mode
                anim = 0
                for state in sheet["states"]:
                    shown = to_engine_order(animation_map(state["animations"], state["animationType"],
                                                          state["flipLeft"], lambda a, f: (a, f)))
                    for animation, mirrored in shown:
                        start, end = ngpc.animation_offsets[anim]
                        anim += 1
                        if not animation:
                            continue
                        self.assertEqual(end - start + 1, len(animation["frames"]))
                        for k, frame in enumerate(animation["frames"]):
                            hw = ngpc.frames[ngpc.frame_order[start + k]]
                            for sx, sy in ((100, 90), (3, 17)):
                                ox, oy = editor_frame_origin(sheet, sx, sy)
                                want = {(ox + x, oy + y): v for (x, y), v in
                                        editor_frame(sheet, image, frame, mode, mirrored).items()}
                                self.assertEqual(hardware_canvas(hw, ngpc.tiles, sx, sy), want)
                                checked += 1
        self.assertGreater(checked, 1000)

    def test_standard_16x16_actor_covers_its_tile_and_the_one_above(self):
        # GB Studio convention: an actor at tile (tx, ty) covers tiles tx..tx+1, ty-1..ty.
        sheet, image = load_sheets(GBS2)["player"]
        frame = ngpc_sheet(sheet, image).frames[0]
        xs = [s.x for s in frame] + [s.x + 7 for s in frame]
        ys = [s.y for s in frame] + [s.y + 7 for s in frame]
        self.assertEqual((min(xs), max(xs), min(ys), max(ys)), (0, 15, -8, 7))

    def test_priority_tile_is_drawn_behind(self):
        # docs/M6_SPRITE_PRIORITY.md: the GB priority bit becomes behind=True
        # (PR.C = 01), on its hardware sprites only.
        sheet, image = load_sheets(GBS2)["player"]
        sheet = json.loads(json.dumps(sheet))
        sheet["states"][0]["animations"][0]["frames"][0]["tiles"][0]["priority"] = True
        frames = ngpc_sheet(sheet, image).frames
        behind = [s.behind for frame in frames for s in frame]
        self.assertTrue(any(behind))
        self.assertFalse(all(behind))

    def test_8x16_vertical_flip_swaps_halves(self):
        """A tile reused upside down (dedupe flipY): the hardware shows its
        bottom 8x8 half on top, both halves flipped. Tiles are deduplicated in
        reverse list order, as in GB Studio: b is stored, a reuses it flipped."""
        tile = lambda tid, x, fy: {"id": tid, "x": x, "y": 0, "sliceX": 0, "sliceY": 0,
                                   "flipX": False, "flipY": fy, "paletteIndex": 5,
                                   "objPalette": "OBP0", "priority": False}
        sheet = {"name": "t", "canvasWidth": 16, "canvasHeight": 16, "canvasOriginX": 0,
                 "canvasOriginY": 0, "spriteMode": "8x16",
                 "states": [{"animationType": "fixed", "flipLeft": False, "animations": [
                     {"frames": [{"tiles": [tile("a", 0, False), tile("b", 8, True)]}]}]}]}
        from ngpcraft.sprites import Indexed
        image = Indexed(8, 16, [1] * 64 + [3] * 64)
        frame = ngpc_sheet(sheet, image).frames[0]
        self.assertEqual(frame, (HardwareSprite(8, -8, 0, False, False, 5),
                                 HardwareSprite(8, 0, 1, False, False, 5),
                                 HardwareSprite(0, -8, 1, False, True, 5),
                                 HardwareSprite(0, 0, 0, False, True, 5)))


if __name__ == "__main__":
    unittest.main()
