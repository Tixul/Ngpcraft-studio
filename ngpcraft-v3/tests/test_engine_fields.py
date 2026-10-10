"""Engine fields: every field of the GB Studio 4.3.2 engine is classified and
proved (tools/lab_engine_fields.py, docs/M6_ENGINE_FIELDS.md); the defects the
campaign found stay fixed."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.pipeline import export_project
from ngpcraft.projectile_runtime import projectile_source
from ngpcraft.scene_genres import widened_fields as genre_widened
from ngpcraft.scene_platform import widened_fields as platform_widened
from tools.demo_actor_moves import GBS2
from tools.lab_engine_fields import CAMPAIGNS, ELSEWHERE, proved_fields, run_campaign
from tools.lab_projectile_pool import FRAMES, definitions

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = json.loads((ROOT / "ngpcraft/parity/gbstudio.json").read_text(encoding="utf-8"))["engine_fields"]
ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS", "NGC_TEST_CLANG")]


class EngineFieldCatalogueTests(unittest.TestCase):
    def test_every_field_is_delivered_and_proved(self):
        proved = proved_fields()
        for key, entry in CATALOGUE.items():
            with self.subTest(field=key):
                self.assertIn(entry["status"], ("identique", "adapte"))
                self.assertTrue(key in proved or key in ELSEWHERE)
        self.assertEqual(set(proved) - set(CATALOGUE), set())

    def test_widened_bytes_are_the_adapted_fields(self):
        # ECARTS §16: UBYTE/BYTE fields are 16-bit on the NGPC; fade_style keeps §13.
        widened = platform_widened() | genre_widened()
        for key, entry in CATALOGUE.items():
            with self.subTest(field=key):
                if key in widened:
                    self.assertEqual((entry["status"], entry["ecart"]), ("adapte", "§16"))
                elif key != "fade_style":
                    self.assertEqual(entry["status"], "identique")

    def test_campaign_variants_name_real_fields(self):
        for name, campaign in CAMPAIGNS.items():
            for key, variant in campaign["variants"].items():
                with self.subTest(variant=f"{name}/{key}"):
                    self.assertTrue(set(variant["fields"]) <= set(CATALOGUE))
                    self.assertTrue(variant["vs"] is None or variant["vs"] in campaign["variants"])


class ProjectileSpreadTests(unittest.TestCase):
    def test_spread_selects_one_collision_rhythm(self):
        source = lambda spread: "\n".join(projectile_source(definitions(), FRAMES, capacity=5, width_tiles=20,
                                                            height_tiles=19, collision_spread=spread))
        self.assertIn("(iterator & 3u) == 0u", source("SPREAD_4"))
        self.assertIn("(iterator & 1u) == 0u", source("SPREAD_2"))
        every = source("EVERY_FRAME")
        self.assertNotIn("iterator", every)            # no counter left in the generated C
        with self.assertRaisesRegex(Exception, "SPREAD_8"):
            source("SPREAD_8")


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class EngineFieldExportTests(unittest.TestCase):
    def test_forced_trigger_buttons_compile(self):
        from tools.platform_oracle import project_case
        for value in ("INPUT_A_PRESSED", "INPUT_B_PRESSED"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                export_project(project_case(root, fields={"INPUT_PLATFORM_FORCE_TRIGGER": value}), root / "out")
                source = (root / "out/src/scene_actors.c").read_text(encoding="utf-8")
                self.assertIn(f"#define {value} (ngc_joy_pressed & J_", source)

    def test_adventure_animation_states_link_their_loader(self):
        from tools.genre_oracle import project_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root, "adventure", keep_actors=False, fields={"ADVENTURE_KNOCKBACK_ANIM": "Hurt"})
            manifest = export_project(project, root / "out")
            self.assertIn("anim_states", manifest["features"])
            self.assertNotIn("platform", manifest["features"])
            self.assertTrue(manifest["scene_engine"]["adventure"]["animation_states"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root, "adventure", keep_actors=False)
            self.assertNotIn("anim_states", export_project(project, root / "out")["features"])

    def test_projectile_spread_reaches_the_rom_source(self):
        from tools.genre_oracle import project_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = project_case(root, "shmup", keep_actors=False, shots=True,
                                      fields={"PROJECTILES_COLLISION_SPREAD": "EVERY_FRAME"})
            export_project(project, root / "out")
            source = (root / "out/src/scene_actors.c").read_text(encoding="utf-8")
            update = source[source.index("void ngc_projectiles_update("):]
            self.assertNotIn("iterator", update[:update.index("\n}\n")])


@unittest.skipUnless(GBS2.is_dir() and all(ENV), "native prerequisites missing")
class EngineFieldNativeTests(unittest.TestCase):
    def test_every_variant_matches_the_original_and_is_exercised(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in CAMPAIGNS:
                rows = run_campaign(Path(tmp), name, ENV[0], ENV[1], ENV[2], ENV[3])
                for row in rows:
                    with self.subTest(variant=f"{name}/{row['variant']}"):
                        self.assertTrue(row["ok"], row)


if __name__ == "__main__":
    unittest.main()
