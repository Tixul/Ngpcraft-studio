"""Scenes over 255 tiles (docs/PLAN_GRANDES_CARTES.md, ngpcraft/world.py).

Python checks: the NGPC limits, a wide game's 32-bit positions and its audit,
a GB Studio-sized game unchanged, refusals with their location. ROM proofs
(toolchain, emulator, BIOS): tools/lab_big_map.py, oracle by translation.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.compiler import CompileError  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.replace_tiles import replace_ops  # noqa: E402
from tools.demo_actor_moves import ev, num  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.lab_big_map import big_case, run  # noqa: E402
from tools.wide_audit import audit  # noqa: E402

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


def scene_file(project):
    return next((project / "project/scenes").rglob("scene.gbsres"))


def edit_scene(project, **changes):
    path = scene_file(project)
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene.update(changes)
    write_json(path, scene)


class BigMapExportTests(unittest.TestCase):
    def export(self, rx, ry, start=(20, 10), **scene):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        project = big_case(root, rx, ry, start)
        if scene:
            edit_scene(project, **scene)
        export_project(project, root / "export")
        return root / "export"

    def refused(self, rx, ry, pattern, start=(20, 10), **scene):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = big_case(root, rx, ry, start)
            if scene:
                edit_scene(project, **scene)
            with self.assertRaisesRegex(CompileError, pattern):
                export_project(project, root / "export")
            self.assertFalse((root / "export").exists())

    def test_gb_studio_sized_game_keeps_16_bit_positions(self):
        src = self.export(1, 1) / "src"
        actors = (src / "scene_actors.c").read_text(encoding="utf-8")
        self.assertIn("unsigned short act_x[", actors)
        self.assertNotIn("unsigned long", actors)
        self.assertIn("extern unsigned short ngc_camera_x;", (src / "game.c").read_text(encoding="utf-8"))

    def test_wide_game_positions_are_32_bit_and_audit_is_clean(self):
        export = self.export(2, 1)
        actors = (export / "src/scene_actors.c").read_text(encoding="utf-8")
        for declaration in ("unsigned long act_x[", "unsigned long ngc_camera_x;", "    unsigned long new_x;",
                            "#define SUBPX_TO_TILE(a) ((UWORD)((a) >> 8))", "#define NGC_COLL_W 322u"):
            self.assertIn(declaration, actors)
        self.assertIn("extern unsigned long ngc_camera_x;", (export / "src/game.c").read_text(encoding="utf-8"))
        self.assertEqual(audit(export), [])

    def test_shaken_camera_left_of_the_scene_is_tile16_minus_one(self):
        # actor.c's uint8_t tile16 wraps a shaken draw_scroll_x of 0 - 2 to -1; at the wide width the
        # unsigned 16-bit value made every actor offscreen (MegaX v21: a boss landing at the room's
        # edge shook the camera, X and the boss vanished for good).
        for rx, kind in ((2, "unsigned short"), (1, "unsigned char")):
            with self.subTest(width=kind):
                actors = (self.export(rx, 1) / "src/scene_actors.c").read_text(encoding="utf-8")
                self.assertIn(f"    {kind} sx = ({kind})(({kind})((short)draw_x >> 4) + 64u);", actors)
                self.assertIn(f"    {kind} sy = ({kind})(({kind})((short)draw_y >> 4) + 64u);", actors)

    def test_ngpc_limits_per_axis_and_in_all(self):
        self.refused(26, 1, r"4186x18 tiles; an NGPC scene has at most 4095 tiles per axis")
        self.refused(2, 12, r"322x216 tiles; .* 65535 tiles in all")

    def test_refusals_of_a_wide_game_are_located(self):
        # A subpixel position read does not fit 16 bits in a wide game.
        spx = ev("spx", "EVENT_SET_VALUE", variable="L0",
                 value={"type": "property", "target": "player", "property": "spxpos"})
        self.refused(2, 1, r"spxpos: an actor position in subpixels", script=[spx])
        raw = ev("raw", "EVENT_GBVM_SCRIPT", script="VM_ACTOR_SET_POS .LOCAL_ACTOR", references=[])
        self.refused(2, 1, r"GBVM script line 1: VM_ACTOR_SET_POS reads or writes positions", script=[raw])

    def test_genres_compile_at_the_wide_width(self):
        # G6: shmup.c, pointnclick.c, adventure.c at 32 bits (scene_genres._widen_genre).
        for kind, marks in (("SHMUP", ("static unsigned long add_signed_clamped_u16(", "act_y[i] -= (long)((short)")),
                            ("ADVENTURE", ("    unsigned long new_x;", "/* wide world: signed (act_x[0] -= ...) */")),
                            ("POINTNCLICK", ("#define image_width_subpx ((unsigned long)NGC_COLL_W << 8)",))):
            with self.subTest(kind=kind):
                export = self.export(2, 1, type=kind)
                actors = (export / "src/scene_actors.c").read_text(encoding="utf-8")
                for mark in marks:
                    self.assertIn(mark, actors)
                self.assertEqual(audit(export), [])

    def test_replace_tile_addresses_bytes_only(self):
        with self.assertRaisesRegex(CompileError, r"Replace Tile at 300,2: .* tiles 0..255 only"):
            replace_ops(["VM_REPLACE_TILE_XY 300, 2, ___bank_ts, _ts, 0"])

    def test_audit_finds_a_16_bit_view_of_a_32_bit_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "a.c").write_text("unsigned long act_x[2];\nunsigned long ngc_camera_x;\n"
                                     "static unsigned short keep_x;\nvoid f(void)\n{\n    keep_x = act_x[0];\n}\n",
                                     encoding="utf-8")
            (src / "b.c").write_text("extern unsigned short ngc_camera_x;\n", encoding="utf-8")
            problems = audit(tmp)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("extern unsigned short ngc_camera_x" in p for p in problems))
        self.assertTrue(any("keep_x = act_x" in p for p in problems))


@unittest.skipUnless(all(ENV), "needs NGC_TEST_TOSHIBA, NGC_TEST_EMULATOR and NGC_TEST_BIOS")
class BigMapRomTests(unittest.TestCase):
    def lab(self, axis, frames, witness=False):
        with tempfile.TemporaryDirectory() as tmp:
            return run(Path(tmp) / "lab", ENV[0], ENV[1], ENV[2], axis, frames, witness)

    def test_across_like_the_original_to_the_pixel_and_witness_differs(self):
        r = self.lab("x", 600)
        self.assertEqual(r["mismatches"], 0, r["first"])
        self.assertGreater(r["ticks_beyond_2048_px"], 200)
        self.assertEqual((r["screens_compared"], r["screen_mismatches"]), (30, []))
        self.assertGreater(self.lab("x", 600, witness=True)["mismatches"], 100)

    def test_down_like_the_original(self):
        r = self.lab("y", 600)
        self.assertEqual(r["mismatches"], 0, r["first"])
        self.assertEqual(r["ticks_beyond_2048_px"], r["ticks"])

    def test_moving_platform_ridden_beyond_2048_px(self):
        r = self.lab("ride", 480)
        self.assertEqual(r["mismatches"], 0, r["first"])
        self.assertEqual(r["actor_x_px_range"], [[2672, 2712]])

    def test_ladder_climbed_like_the_original_and_witness_differs(self):
        # Wilfried, MegaBig 2026-10-12: X vanished at the first ladder (VEL_TO_SUBPX unsigned on 32 bits).
        r = self.lab("ladder", 420)
        self.assertEqual(r["mismatches"], 0, r["first"])
        self.assertIn(4, r["states_seen"])           # platform.c LADDER_STATE
        self.assertGreater(self.lab("ladder", 420, witness=True)["mismatches"], 100)

    def test_genres_like_the_original_and_witnesses_differ(self):
        for genre in ("adventure", "shmup", "pointnclick"):
            with self.subTest(genre=genre):
                r = self.lab(genre, 480)
                self.assertEqual(r["mismatches"], 0, r["first"])
                self.assertGreater(r["ticks_beyond_2048_px"], 150)
                self.assertEqual(r["screen_mismatches"], [])
                self.assertGreater(self.lab(genre, 480, witness=True)["mismatches"], 100)

    def test_topdown_like_the_original_to_the_pixel_and_witness_differs(self):
        r = self.lab("topdown", 480)
        self.assertEqual(r["mismatches"], 0, r["first"])
        self.assertGreater(r["ticks_beyond_2048_px"], 200)
        self.assertEqual(r["screen_mismatches"], [])
        self.assertGreater(self.lab("topdown", 480, witness=True)["mismatches"], 100)

    def test_priority_tiles_mask_a_wide_scene_like_the_original(self):
        # G6: GB priority tiles in a 2240 px scene; the mask rows are over 64 KiB in
        # (sprite_priority._long_mask_source). The player walks behind them.
        r = self.lab("priority", 420)
        self.assertEqual(r["mismatches"], 0, r["first"])
        self.assertEqual(r["screen_mismatches"], [])
        self.assertGreater(r["screens_compared"], 15)
        self.assertGreater(len(self.lab("priority", 420, witness=True)["screen_mismatches"]), 2)


if __name__ == "__main__":
    unittest.main()
