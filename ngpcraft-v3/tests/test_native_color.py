"""ngpc_color "native" backgrounds (docs/M7_NATIVE_COLOR.md): one plan for the
preview and the build, located refusals, closed sidecar key, CLI format."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ngpcraft.compiler import CompileError
from ngpcraft.native_color import hex_color, palette_plan, plan_native
from ngpcraft.pipeline import export_project
from ngpcraft.project import SCHEMA, SIDECAR, ProjectError, load_project
from ngpcraft.scene_assets import background_source
from tools.demo_gbs_scene import write_json
from tools.lab_native_color import BACKDROP, picture, project_case, write_png

V3 = Path(__file__).resolve().parents[1]


def edit_picture(project, bg_id, change):
    rows = picture()
    change(rows)
    for res in (project / "assets/backgrounds").glob("*.gbsres"):
        if json.loads(res.read_text(encoding="utf-8"))["id"] == bg_id:
            write_png(res.with_suffix(""), rows)


class NativeColorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project, self.bg = project_case(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_matches_the_build(self):
        plan = palette_plan(self.project, self.bg)
        self.assertEqual(plan["format"], "ngpcraft-palette-plan/1")
        self.assertEqual((plan["width_tiles"], plan["height_tiles"]), (20, 18))
        self.assertEqual(plan["backdrop"], "%02x%02x%02x" % BACKDROP)
        self.assertEqual((len(plan["palettes"]), plan["errors"]), (16, []))
        self.assertEqual(len(plan["tile_palettes"]), 20 * 18)
        manifest = export_project(self.project, self.root / "export")
        self.assertEqual(manifest["scene_engine"]["background"]["palette_counts"], [16])
        self.assertEqual(manifest["scene_engine"]["background"]["backdrop_rgb444"], 0x412)
        # The scene map's palette field (bits 9..12) is the plan's tile_palettes.
        source = background_source(load_project(self.project), load_project(self.project).scenes[0])
        self.assertEqual(source.backdrop, 0x412)
        from ngpcraft.scene_planes import compile_scene_background
        asset = compile_scene_background(160, 144, source.pixels, where="t", backdrop=source.backdrop)
        self.assertEqual([(cell >> 9) & 15 for cell in asset.maps[0]], plan["tile_palettes"])
        self.assertEqual([[hex_color(c) for c in p] for p in asset.palettes[0]], plan["palettes"])

    def test_without_the_key_gb_colours_are_unchanged(self):
        (self.project / SIDECAR).unlink()
        source = background_source(load_project(self.project), load_project(self.project).scenes[0])
        self.assertIsNone(source.backdrop)

    def test_gb_studio_automatic_palettes_take_the_native_colours(self):
        # GB Studio's "Automatic" (autoColor) reads the PNG's colours: on the
        # NGPC, the native plan (it was refused before 2026-10-10).
        (self.project / SIDECAR).unlink()
        for res in (self.project / "assets/backgrounds").glob("*.gbsres"):
            data = json.loads(res.read_text(encoding="utf-8"))
            if data["id"] == self.bg:
                write_json(res, {**data, "autoColor": True})
        project = load_project(self.project)
        self.assertEqual(background_source(project, project.scenes[0]).backdrop, 0x412)
        manifest = export_project(self.project, self.root / "export")
        self.assertEqual(manifest["scene_engine"]["background"]["palette_counts"], [16])

    def test_native_background_with_a_native_second_background(self):
        # Both NGPC planes in native colours: the scene's own image on SCR2, the
        # same image as the second background on SCR1 (transparent on its
        # backdrop colour), sharing the backdrop register.
        project = load_project(self.project)
        scene = project.scenes[0]
        sidecar = json.loads((self.project / SIDECAR).read_text(encoding="utf-8"))
        sidecar["resources"][scene["id"]] = {"second_background": {
            "background_id": self.bg, "scroll_x": "fixed", "scroll_y": "fixed"}}
        write_json(self.project / SIDECAR, sidecar)
        manifest = export_project(self.project, self.root / "export")
        self.assertIn("second_background", manifest["features"])
        engine = manifest["scene_engine"]
        self.assertEqual(engine["planes"]["front_usage"], "second_background")
        self.assertEqual(engine["planes"]["backdrop_rgb444"], 0x412)
        self.assertEqual(engine["second_background"]["status"], "emitted")

    def test_tile_with_four_colours_is_refused_with_its_place(self):
        def four(rows):
            rows[9][17] = (0xFF, 0xFF, 0xFF)       # tile (2, 1) gets a 4th colour
        edit_picture(self.project, self.bg, four)
        plan = palette_plan(self.project, self.bg)
        self.assertEqual([(e["x"], e["y"], len(e["colors"])) for e in plan["errors"]], [(2, 1, 4)])
        self.assertEqual(plan["tile_palettes"][1 * 20 + 2], -1)
        with self.assertRaisesRegex(CompileError, r"tile \(2, 1\): 4 colours besides the backdrop.*ffffff"):
            export_project(self.project, self.root / "export")

    def test_seventeenth_palette_is_refused(self):
        pixels = tuple(([1, 2, 3, 0] * 2 + [4, 5, 6, 0] * 2) * 8)
        plan = plan_native(16, 8, pixels, 0, palette_limit=1)
        self.assertEqual([(e[0], e[1], e[3]) for e in plan["errors"]], [(1, 0, "palette_limit")])

    def test_transparent_pixels_take_the_backdrop(self):
        from ngpcraft.native_color import native_pixels
        pixels, backdrop = native_pixels(2, 1, [(255, 0, 0, 255), (0, 0, 0, 0)])
        self.assertEqual((pixels, backdrop), ((0x00F, 0x00F), 0x00F))

    def test_backdrop_tie_takes_the_smallest_word(self):
        from ngpcraft.native_color import native_pixels
        _, backdrop = native_pixels(2, 1, [(0, 0, 255, 255), (255, 0, 0, 255)])
        self.assertEqual(backdrop, 0x00F)

    def test_key_is_closed(self):
        write_json(self.project / SIDECAR, {"schema": SCHEMA, "resources": {self.bg: {"ngpc_color": "auto"}}})
        with self.assertRaisesRegex(ProjectError, "ngpc_color: 'auto', expected \"native\""):
            load_project(self.project)
        scene = next((self.project / "project/scenes").rglob("scene.gbsres"))
        sid = json.loads(scene.read_text(encoding="utf-8"))["id"]
        write_json(self.project / SIDECAR, {"schema": SCHEMA, "resources": {sid: {"ngpc_color": "native"}}})
        with self.assertRaisesRegex(ProjectError, "ngpc_color: extension belongs to a background or sprite sheet resource"):
            load_project(self.project)

    def test_priority_tiles_are_refused_not_dropped(self):
        for res in (self.project / "assets/backgrounds").glob("*.gbsres"):
            data = json.loads(res.read_text(encoding="utf-8"))
            if data["id"] == self.bg:
                data["tileColors"] = "80!"
                write_json(res, data)
        with self.assertRaisesRegex(CompileError, "GB priority tiles in tileColors with ngpc_color"):
            export_project(self.project, self.root / "export")

    def test_cli_help_and_output(self):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        self.assertEqual(subprocess.run([sys.executable, "-B", "-m", "ngpcraft", "palette-plan", "--help"],
                                        cwd=V3, capture_output=True, env=env).returncode, 0)
        done = subprocess.run([sys.executable, "-B", "-m", "ngpcraft", "palette-plan", str(self.project),
                               "--background", self.bg], cwd=V3, capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), palette_plan(self.project, self.bg))
        done = subprocess.run([sys.executable, "-B", "-m", "ngpcraft", "palette-plan", str(self.project),
                               "--background", "nope"], cwd=V3, capture_output=True, text=True, env=env)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no background resource has this id", done.stderr)


if __name__ == "__main__":
    unittest.main()


class NativeSpriteTests(unittest.TestCase):
    """Phase 2: native sprite sheets, palettes placed in the free K2GE slots (8..15 first)."""

    def test_palettes_per_tile_and_refusal(self):
        from ngpcraft.sprites import NATIVE_BASE, native_palettes
        a = [NATIVE_BASE + c for c in (1, 2, 3)] * 21 + [0]
        b = [NATIVE_BASE + c for c in (4, 5, 6)] * 21 + [0]
        palettes, slots, encoded = native_palettes([a, b, a], "t")
        self.assertEqual(palettes, ((1, 2, 3), (4, 5, 6)))
        self.assertEqual(slots, (0, 1, 0))
        self.assertEqual(set(encoded[0]), {0, 1, 2, 3})
        with self.assertRaisesRegex(CompileError, "tile 0: 4 opaque colours"):
            native_palettes([[NATIVE_BASE + c for c in (1, 2, 3, 4)] * 16], "t")

    def test_script_sprite_palette_changes_reserve_their_slots(self):
        # A script rewriting GB sprite palettes keeps those slots from native sheets.
        from ngpcraft.palette_load import changed_sprite_slots
        self.assertEqual(changed_sprite_slots(
            ['        VM_LOAD_PALETTE         5, ^/(.PALETTE_SPRITE | .PALETTE_COMMIT)/',
             '        VM_LOAD_PALETTE         255, ^/(.PALETTE_BKG | .PALETTE_COMMIT)/'], 's'), {0, 2})
        with self.assertRaisesRegex(CompileError, "only constant masks"):
            changed_sprite_slots(['        VM_LOAD_PALETTE         .MASK, ^/(.PALETTE_SPRITE)/'], 's')

    def test_scene_places_and_shares_palettes(self):
        from ngpcraft.sprites import HardwareSprite, NgpcSheet, place_native_palettes
        spr = lambda p: HardwareSprite(0, 0, 0, False, False, p)
        gb = NgpcSheet("gb", "8x16", (), ((spr(3),),), (0,), ())
        one = NgpcSheet("one", "8x16", (), ((spr(0), spr(1)),), (0,), (), palettes=((1, 2, 3), (4, 5, 6)))
        two = NgpcSheet("two", "8x16", (), ((spr(0),),), (0,), (), palettes=((4, 5, 6),))
        sheets, table = place_native_palettes((gb, one, two), "t")
        self.assertEqual(table, ((8, (1, 2, 3)), (9, (4, 5, 6))))
        self.assertEqual([s.palette for s in sheets[0].frames[0]], [3])          # GB Studio slot kept
        self.assertEqual([s.palette for s in sheets[1].frames[0]], [8, 9])
        self.assertEqual([s.palette for s in sheets[2].frames[0]], [9])           # shared
        # Up to 16: after 8..15, the GB slots no GB sheet, emote or script uses.
        def many(n):
            return NgpcSheet("m", "8x16", (), (tuple(spr(i) for i in range(n)),), (0,), (),
                             palettes=tuple((i, i, i) for i in range(n)))
        sheets, table = place_native_palettes((gb, many(14)), "t", reserved=frozenset({7}))
        slots = [s.palette for s in sheets[1].frames[0]]
        self.assertEqual(slots, [8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 4, 5, 6])
        self.assertNotIn(3, slots)                                               # the GB sheet's slot
        self.assertNotIn(7, slots)                                               # reserved (emotes)
        self.assertEqual(len(place_native_palettes((many(16),), "t")[1]), 16)    # whole native scene
        with self.assertRaisesRegex(CompileError, "15 palettes, 14 at most.*palettes 3, 7 in use"):
            place_native_palettes((gb, many(15)), "t", reserved=frozenset({7}))
        with self.assertRaisesRegex(CompileError, "17 palettes, 16 at most"):
            place_native_palettes((many(17),), "t")

    def test_sprite_plan_cli(self):
        from tools.lab_native_sprites import project_case as sprite_case
        with tempfile.TemporaryDirectory() as tmp:
            project, sheet = sprite_case(Path(tmp))
            done = subprocess.run([sys.executable, "-B", "-m", "ngpcraft", "palette-plan", str(project),
                                   "--sprite", sheet], cwd=V3, capture_output=True, text=True,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(done.returncode, 0, done.stderr)
        plan = json.loads(done.stdout)
        self.assertEqual((len(plan["palettes"]), plan["errors"], plan["palette_limit"]), (4, [], 16))


ROM_ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


@unittest.skipUnless(all(ROM_ENV), "requires Toshiba, native emulator and BIOS")
class NativeColorRomTests(unittest.TestCase):
    def test_background_and_sprites_on_screen(self):
        from tools.lab_native_color import run as background_run
        from tools.lab_native_sprites import run as sprite_run
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(background_run(Path(tmp) / "bg", *ROM_ENV)["wrong_frames"], 0)
            self.assertTrue(background_run(Path(tmp) / "bgw", *ROM_ENV, witness=True)["wrong_frames"])
            self.assertTrue(sprite_run(Path(tmp) / "spr", *ROM_ENV)["passed"])
            self.assertFalse(sprite_run(Path(tmp) / "sprw", *ROM_ENV, witness=True)["passed"])

    def test_twelve_native_sprite_palettes_use_the_free_gb_slots(self):
        # 3 native sheets x 4 palettes: K2GE 8..15, then GB slots 0..3 (no GB
        # sheet in the scene uses them). Every picture exact on every frame.
        from tools.lab_native_sprites import run as sprite_run
        with tempfile.TemporaryDirectory() as tmp:
            result = sprite_run(Path(tmp) / "spr", *ROM_ENV, extra=2)
            self.assertTrue(result["passed"], result)
            self.assertEqual(len(result["positions"]), 3)
            table = (Path(tmp) / "spr/export/src").glob("*.c")
            source = "\n".join(f.read_text(encoding="utf-8") for f in table)
            self.assertIn("spr_pal[64]", source)
            self.assertFalse(sprite_run(Path(tmp) / "sprw", *ROM_ENV, witness=True, extra=2)["passed"])
