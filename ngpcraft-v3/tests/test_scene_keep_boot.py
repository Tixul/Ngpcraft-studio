"""player_iframes at boot (GBVM actor.c actors_init: 0 once at boot, then kept across load_scene).

Found 2026-10-15: the V3 kept collision_iframes from scene to scene (ngc_scene_keep) without the
boot reset; a first scene without collisions handed the next one the random byte of a console's
power-on RAM (up to 255 ticks of invincibility). ngc_scene_keep_boot is that reset, called once by
ngc_game_init before the first scene load."""
import inspect
import re
import unittest

from ngpcraft.gbvm import emit


class SceneKeepBootTests(unittest.TestCase):
    def test_reset_exists_with_collisions_only(self):
        with_hits = emit.keep_source(True)
        self.assertIn("void ngc_scene_keep_boot(void)\n{\n    collision_iframes = 0u;\n}", with_hits)
        self.assertNotIn("ngc_scene_keep_boot", emit.keep_source(False))

    def test_game_init_resets_before_the_first_load(self):
        # The generator's ngc_game_init: the reset after the engine boots, before ngc_scene_load.
        source = inspect.getsource(emit)
        init = source[source.index('"void ngc_game_init(void)", "{",'):]
        init = init[:init.index('/* {resources[table[start]]')]
        self.assertRegex(init, re.compile(r'_boot\(\);.*ngc_scene_keep_boot\(\);.*ngc_scene_load\(', re.S))
        self.assertIn('"void ngc_scene_keep_boot(void);"] if used["hits"]', source)


if __name__ == "__main__":
    unittest.main()
