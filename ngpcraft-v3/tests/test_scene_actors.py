import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.background import compile_background
from ngpcraft.background_rom import write_export
from ngpcraft.compiler import CompileError
from ngpcraft.scene_actors import FLAGS, actors_source, export_actors_rom
from ngpcraft.sprites import NgpcSheet, ngpc_sheet
from ngpcraft.toolchain import build_rom
from tools import demo_actors
from tools.gbvm_actors import ACTIVE, DISABLED, PERSISTENT, ANIM_NOLOOP, Actor, Scene
from tools.reference import REFERENCE
from tools.sprite_png import load_sheets

ROOT = Path(__file__).resolve().parents[1]
GBS2 = REFERENCE / "appData/templates/gbs2"
ANIMS = [(0, 1)] * 4 + [(2, 5)] * 4


def actor(i, tx, ty, flags=0, tick=3):
    return Actor(i, tx << 8, ty << 8, 0, flags, tick, ANIMS)


class GbvmActorModelTests(unittest.TestCase):
    """The oracle against GBVM facts (contract C12-C14, actor.c)."""

    def test_offscreen_check_is_staggered_one_actor_in_four(self):
        far = [actor(i, 200, 200) for i in range(1, 5)]
        scene = Scene([actor(0, 5, 5)] + far, 2048, 2048)
        for a in far:                       # activate them by hand, as a script would
            a.flags &= ~ACTIVE
            scene.draw_x, scene.draw_y = 200 * 8 - 80, 200 * 8 - 76
            scene.activate(a)
            scene.draw_x = scene.draw_y = 0
        self.assertTrue(all(a.flags & ACTIVE for a in far))
        deactivated = []
        for frame in range(4):
            scene.actors_update()
            deactivated.append(sum(not a.flags & ACTIVE for a in far))
            scene.game_time += 1
        self.assertEqual(deactivated[-1], 4)
        self.assertEqual(len(set(deactivated)), len(deactivated))   # progressive, never all at once

    def test_persistent_actor_is_hidden_not_deactivated(self):
        keep, drop = actor(1, 100, 100, PERSISTENT), actor(2, 100, 100)
        scene = Scene([actor(0, 5, 5), keep, drop], 2048, 2048)
        scene.draw_x, scene.draw_y = 100 * 8 - 80, 100 * 8 - 76
        scene.activate(keep)
        scene.activate(drop)
        scene.draw_x = scene.draw_y = 0
        for _ in range(4):
            scene.actors_update()
            scene.game_time += 1
        self.assertTrue(keep.flags & ACTIVE and keep.flags & DISABLED)
        self.assertFalse(drop.flags & ACTIVE)
        self.assertNotIn(keep, [a for a, _, _ in scene.render_list()])

    def test_animation_tick_and_noloop(self):
        loop, once = actor(1, 5, 5, 0, 1), actor(2, 5, 6, ANIM_NOLOOP, 1)
        scene = Scene([actor(0, 5, 5), loop, once], 2048, 2048)
        scene.start(80, 76)
        for a in (loop, once):
            scene.set_dir(a, 0, True)       # moving: frames 2..5
        seen = {1: [], 2: []}
        for _ in range(12):
            scene.actors_update()
            seen[1].append(loop.frame)
            seen[2].append(once.frame)
            scene.game_time += 1
        self.assertEqual(sorted(set(seen[1])), [2, 3, 4, 5])
        self.assertEqual(seen[2][-1], 5)     # stays on the last frame


class ActorsSourceTests(unittest.TestCase):
    def test_refusals_are_localized(self):
        sheet = NgpcSheet("t", "8x8", ((0,) * 64,), ((),), (0,), ((0, 0),) * 8)
        one = {"sheet": 0, "x": 0, "y": 0, "dir": 0, "flags": 0, "anim_tick": 3, "move_speed": 0}
        with self.assertRaisesRegex(CompileError, "0 actors"):
            actors_source([], [sheet], 256)
        from ngpcraft.scene_actors import MAX_ACTORS     # actor_capacity 64 + the player
        with self.assertRaisesRegex(CompileError, f"{MAX_ACTORS + 1} actors, NGPC allows 1..{MAX_ACTORS}"):
            actors_source([one] * (MAX_ACTORS + 1), [sheet], 256)
        with self.assertRaisesRegex(CompileError, "actor 0 uses an unknown sheet 3"):
            actors_source([dict(one, sheet=3)], [sheet], 256)
        with self.assertRaisesRegex(CompileError, "actor 0 direction"):
            actors_source([dict(one, dir=4)], [sheet], 256)
        source = actors_source([one, dict(one, flags=FLAGS["persistent"])], [sheet], 256)
        self.assertIn("act_init_flags[2] = {\n    0, 16,", source)


def build_case(root, steps=120, mutate=None):
    w, h = 448, 448
    from tools.demo_backgrounds import scene_pixels
    _, _, pixels = scene_pixels(GBS2, "sample_town")
    background = compile_background(w, h, pixels)
    loaded = load_sheets(GBS2)
    sheets = [ngpc_sheet(*loaded[name]) for name in demo_actors.SHEETS]
    script = demo_actors.script(w, h, steps)
    defs = [{"sheet": demo_actors.SHEETS.index(n), "flags": f, "anim_tick": t, "move_speed": 0}
            for n, _, _, _, f, t in demo_actors.ACTORS]
    palettes = [(0x00F + i, 0x0F0 + i, 0xF00 + i) for i in range(8)]
    export_actors_rom(background, sheets, palettes, defs, script, root / "export", loop=True)
    if mutate:
        files = {p: (root / "export" / p).read_text(encoding="utf-8") for p in
                 ("src/game.h", "src/game.c", "src/scene_stream.c", "src/scene_sprites.c", "src/scene_actors.c")}
        files["src/scene_actors.c"] = mutate(files["src/scene_actors.c"])
        (root / "export").rename(root / "reference")
        write_export(["streamed_background", "sprites", "actors"], files, "mutation witness", {"background": background.report()},
                     root / "export", "test")
    build_rom(root / "export", os.environ["NGC_TEST_TOSHIBA"])
    frames = demo_actors.simulate(sheets, [a[0] for a in demo_actors.ACTORS], script, w, h, 2 * steps + 10)
    (root / "scene.rgb444").write_bytes(b"".join(v.to_bytes(2, "little") for v in pixels))
    (root / "source.json").write_text(json.dumps({
        "size_px": [w, h], "probe_frames": 2 * steps, "expected_scroll": [list(p) for p, _ in frames],
        "actors": [d for _, d in frames], "project": str(GBS2), "default_sprite_mode": "8x16",
        "sprite_palettes": [list(p) for p in palettes]}))


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                     "requires Toshiba, native emulator and BIOS")
class ActorsRomTests(unittest.TestCase):
    def probe(self, root):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_stream.py"), str(root),
                               "--emulator", os.environ["NGC_TEST_EMULATOR"], "--bios", os.environ["NGC_TEST_BIOS"],
                               "--out", str(root / "probe")], capture_output=True, text=True, timeout=600)
        return proc, json.loads((root / "probe/probe.json").read_text())

    def test_rom_matches_gbvm_oracle_every_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root)
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(result["frames_with_mismatch"], [])
            self.assertEqual(result["dropped_frames"], [])
            # Everything written to the hardware fits the vertical blank, except
            # the re-blit of a camera jump (exact, ahead of the beam).
            commits = [r["next_commit_lines"] for r in result["rows"]]
            self.assertLess(sorted(commits)[-5], result["vblank_lines"])

    def test_probe_detects_missing_column_activation(self):
        """Mutation witness: actors entering from the side are activated by
        the incoming column (scroll.c); without it they appear late or never."""
        def no_columns(source):
            self.assertEqual(source.count("activate_in_col((unsigned char)"), 2)
            return source.replace("activate_in_col((unsigned char)", "(void)((unsigned char)")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, mutate=no_columns)
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(result["frames_with_mismatch"])


if __name__ == "__main__":
    unittest.main()
