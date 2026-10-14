"""scene_link.direct_calls (docs/ETUDE_CADENCE.md, ngpc.engine_speedups)."""
import unittest

from ngpcraft.scene_link import link_scenes

SPRITES = """void ngc_sprite_draw(unsigned short frame, short x, short y)
{
    ngc_oam = frame + x + y + {k}u;
}
"""
ACTORS = """void ngc_sprite_draw(unsigned short frame, short x, short y);
void ngc_actors_render(void)
{
    ngc_sprite_draw(1u, 2, 3);
}
"""


def scenes(n=2):
    return [{"src/scene_sprites.c": "unsigned short ngc_oam;\n" + SPRITES.replace("{k}", str(k)),
             "src/scene_actors.c": ACTORS} for k in range(n)]


class DirectCallsTests(unittest.TestCase):
    def test_off_is_the_dispatcher_as_before(self):
        files, report = link_scenes(scenes())
        self.assertIn("    ngc_sprite_draw(1u, 2, 3);", files["src/scene_actors.c"])
        self.assertNotIn("direct_publics", report["src/scene_sprites.c"])

    def test_on_calls_the_scene_function_while_the_scene_is_current(self):
        files, report = link_scenes(scenes(), direct=True)
        actors, sprites = files["src/scene_actors.c"], files["src/scene_sprites.c"]
        for k in (0, 1):
            # The scene may change while its function runs: else the dispatcher, as before.
            self.assertIn(f"(ngc_scene == {k}u ? s{k}_ngc_sprite_draw : ngc_sprite_draw)(1u, 2, 3);", actors)
            self.assertIn(f"void s{k}_ngc_sprite_draw(unsigned short frame, short x, short y);   /*", actors)
            self.assertIn(f"\nvoid s{k}_ngc_sprite_draw(unsigned short frame, short x, short y)\n{{", sprites)
            self.assertNotIn(f"static void s{k}_ngc_sprite_draw", sprites)
        # The dispatcher itself stays (game.c and the other units call it).
        self.assertIn("void ngc_sprite_draw(unsigned short frame, short x, short y)\n{\n    switch (ngc_scene)", sprites)
        self.assertEqual(report["src/scene_sprites.c"]["direct_publics"], ["s0_ngc_sprite_draw", "s1_ngc_sprite_draw"])
        self.assertNotIn("direct_publics", report["src/scene_actors.c"])


if __name__ == "__main__":
    unittest.main()
