"""EVENT_TEXT avatars (docs/M4_AVATARS.md): compiler, data, runtime selection.

Oracles: GB Studio 4.3.2's own test (compileEntityEvents.test.js, "should
output text with avatar command"), its readFileToTilesData run by Node, and
sdas's escape rule (asxxsrc/aslex.c getmap: \\n is 0x0A). The ROM proof is
tools/lab_avatar.py (ui.c oracle, every pixel of the box).
"""
import json
import os
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib
from pathlib import Path

from gbs_fixture import GBS2, event, make_project
from ngpcraft.avatars import printed_glyphs, read_avatar_tiles, scene_glyphs, used_avatars
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import _asciz
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import _event_text
from ngpcraft.pipeline import export_project
from ngpcraft.project import encode_resource, load_project
from tools import gbstudio_ts

DANGER = "6845dca5-d9d8-472a-93a7-81889d878516"
FONT = {"id": "87d28862-ac4a-4f15-b678-d8d2e3e8787c", "mapping": {}}


def png(path, width, height, value):
    rows = b"".join(b"\x00" + b"".join(bytes(value(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def add_avatar(root, ident, name, width, height=16):
    folder = Path(root) / "assets/avatars"
    png(folder / f"{name}.png", width, height,
        lambda x, y: [(224, 248, 208), (136, 192, 112), (52, 104, 86), (8, 24, 32)][(x * 3 + y * 5) % 4])
    (folder / f"{name}.png.gbsres").write_text(encode_resource(
        {"_resourceType": "avatar", "id": ident, "name": name, "width": width, "height": height,
         "filename": f"{name}.png"}), encoding="utf-8", newline="")


class CompilerTests(unittest.TestCase):
    def test_gbstudio_avatar_command(self):
        """compileEntityEvents.test.js: "should output text with avatar command"."""
        out = []
        b = ScriptBuilder(out, {"scene": {"id": "s", "actors": []}, "fonts": [FONT], "avatars": [{"id": 2}]})
        _event_text({"text": "HELLO WORLD", "avatarId": 2}, b)
        b.script_end()
        self.assertEqual(b.to_script_string("testname", False), """.module testname

.include "vm.i"
.include "data/game_globals.i"

.area _CODE_255


___bank_testname = 255
.globl ___bank_testname

_testname::
        ; Text Dialogue
        VM_OVERLAY_CLEAR        0, 0, 20, 4, .UI_COLOR_WHITE, .UI_DRAW_FRAME
        VM_OVERLAY_MOVE_TO      0, 18, .OVERLAY_SPEED_INSTANT
        VM_OVERLAY_MOVE_TO      0, 14, .OVERLAY_IN_SPEED
        VM_OVERLAY_SET_SCROLL   3, 1, 16, 5, .UI_COLOR_WHITE
        VM_SWITCH_TEXT_LAYER    .TEXT_LAYER_WIN
        VM_LOAD_TEXT            0
        .asciz "\\001\\001\\002\\002@A\\nBC\\001\\003\\004\\001\\377\\002\\001HELLO WORLD"
        VM_DISPLAY_TEXT
        VM_OVERLAY_WAIT         .UI_MODAL, ^/(.UI_WAIT_WINDOW | .UI_WAIT_TEXT | .UI_WAIT_BTN_A)/
        VM_OVERLAY_MOVE_TO      0, 18, .OVERLAY_OUT_SPEED
        VM_OVERLAY_WAIT         .UI_MODAL, ^/(.UI_WAIT_WINDOW | .UI_WAIT_TEXT)/

        ; Stop Script
        VM_STOP
""")

    def test_sdas_newline_escape_only(self):
        self.assertEqual(_asciz(r"@A\nBC", "t"), [64, 65, 10, 66, 67, 0])
        with self.assertRaises(CompileError):
            _asciz(r"A\tB", "t")

    def test_printed_glyphs_follow_font_codes(self):
        text = r'''        .asciz "\001\001\002\002@A\nBC\001\003\004\001\377\002\001DO NOT FEED"
        .asciz "\002\003\104\105\nGH\002\001%d"'''
        self.assertEqual(printed_glyphs([text], 1, "t"),
                         {(0, 64), (0, 65), (0, 66), (0, 67), (1, 68), (1, 69), (1, 71), (1, 72)})
        self.assertEqual(printed_glyphs([r'        .asciz "\001\001HELLO\012"'], 1, "t"), set())

    def test_glyph_outside_the_avatar_data_is_refused(self):
        tile = (0,) * 64
        self.assertEqual(scene_glyphs([(4, [tile] * 4)], {(0, 64), (0, 67)}, "s"), [(0, tile), (3, tile)])
        with self.assertRaisesRegex(CompileError, "outside avatar font"):
            scene_glyphs([(4, [tile])], {(0, 65)}, "s")        # an 8x8 avatar: one tile


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class DataTests(unittest.TestCase):
    def test_tiles_equal_gbstudio_read_file_to_tiles_data(self):
        if not gbstudio_ts.node_available():
            self.skipTest("node not available")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets/avatars").mkdir(parents=True)
            add_avatar(root, "w", "wide", 32)
            add_avatar(root, "s", "small", 8, 8)
            paths = [GBS2 / "assets/avatars/danger.png", root / "assets/avatars/wide.png",
                     root / "assets/avatars/small.png"]
            expected = gbstudio_ts.run([["lib/tiles/readFileToTiles", "readFileToTilesData", [str(p)]]
                                        for p in paths], pngs=[str(p) for p in paths])

            class P:
                pass
            warnings = []
            for path, data in zip(paths, expected):
                P.root = path.parents[2]
                tiles = read_avatar_tiles(P, f"assets/avatars/{path.name}.gbsres", warnings)
                gb = bytes(b for t in tiles for r in range(8)
                           for b in (sum((t[r * 8 + c] & 1) << (7 - c) for c in range(8)),
                                     sum((t[r * 8 + c] >> 1) << (7 - c) for c in range(8))))
                self.assertEqual(list(gb), data, path.name)
            self.assertEqual(warnings, ["Avatar 'small.png' has invalid dimensions and may not appear correctly. "
                                        "Must be 16px tall and a multiple of 16px wide."])

    def test_used_avatars_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(Path(tmp) / "p", [
                event("a", "EVENT_TEXT", text="A", avatarId="second"),
                event("b", "EVENT_TEXT", text="B", avatarId="missing"),
                event("c", "EVENT_TEXT", text="C", avatarId=DANGER),
                event("d", "EVENT_TEXT", text="D", avatarId="second"),
                {**event("e", "EVENT_TEXT", text="E", avatarId="third"), "args": {"avatarId": "third", "__comment": True}}])
            add_avatar(root, "second", "second", 16)
            add_avatar(root, "third", "third", 16)
            self.assertEqual([res["id"] for _, res in used_avatars(load_project(root))], ["second", DANGER])


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class ExportTests(unittest.TestCase):
    def export(self, script):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = make_project(Path(tmp.name) / "p", script)
        manifest = export_project(root, Path(tmp.name) / "out")
        return manifest, (Path(tmp.name) / "out/src/gbvm.c").read_text(encoding="utf-8")

    def test_avatar_glyphs_selected_only_when_printed(self):
        _, plain = self.export([event("t", "EVENT_TEXT", text="HELLO")])
        self.assertNotIn("avatar", plain)
        self.assertNotIn("NGC_FONT_K2GE", plain)
        _, missing = self.export([event("t", "EVENT_TEXT", text="HELLO", avatarId="missing")])
        self.assertNotIn("ngc_ui_avatar", missing)
        manifest, vm = self.export([event("t", "EVENT_TEXT", text="HELLO", avatarId=DANGER)])
        self.assertIn("static const UWORD ngc_ui_avatar_s0[36]", vm)
        self.assertIn("if (vwf_current_font_desc.attr & NGC_FONT_K2GE) ngc_ui_avatar_tile(letter);", vm)
        start = vm.split("void ngc_ui_start(unsigned char scene)\n{", 1)[1].split("\n}", 1)[0]
        self.assertIn("ngc_ui_cell_avatar = 0u;", start)
        self.assertNotIn("avatar", " ".join(manifest.get("warnings", [])).lower())


ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ENV), "requires Toshiba, native emulator and BIOS")
class AvatarRomTests(unittest.TestCase):
    def test_plain_text_before_first_avatar_with_noisy_ram(self):
        """An export with avatars must also render its first ordinary box.

        The original lab starts with an avatar, which initializes the pending
        attribute before its first use and hides the power-on RAM defect.
        """
        from tools import lab_avatar
        from ngpcraft.gbvm import emit
        texts = [lab_avatar.TEXTS[2], *lab_avatar.TEXTS[:2], *lab_avatar.TEXTS[3:]]
        original = emit.ui_support

        def uninitialized(*args, **kwargs):
            support, api = original(*args, **kwargs)
            reset = "    ngc_ui_cell_avatar = 0u;\n"
            self.assertEqual(api.count(reset), 1)
            return support, api.replace(reset, "")

        with tempfile.TemporaryDirectory() as tmp, patch.object(lab_avatar, "TEXTS", texts):
            good = lab_avatar.run(Path(tmp) / "good", *ENV)
            self.assertTrue(good["passed"], good)
            with patch.object(emit, "ui_support", uninitialized):
                bad = lab_avatar.run(Path(tmp) / "uninitialized", *ENV)
            self.assertFalse(bad["passed"], bad)
            self.assertGreater(bad["wrong_count"], 0)


if __name__ == "__main__":
    unittest.main()
