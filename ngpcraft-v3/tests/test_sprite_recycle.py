"""Sprite palette recycling (ngpcraft/sprite_recycle.py, docs/PALETTE_RECYCLE.md)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.compiler import CompileError  # noqa: E402
from ngpcraft.gbvm.emit import load_model  # noqa: E402
from ngpcraft.scene_sprites import sprites_source  # noqa: E402
from ngpcraft.sprite_recycle import assign, moved_actors, together  # noqa: E402
from ngpcraft.sprites import HardwareSprite, NgpcSheet, place_native_palettes  # noqa: E402

ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]
CONSTANTS = load_model()["constants"]


def module(name, body, args=()):
    head = [f".module {name}", "", *(f".SCRIPT_ARG_{k}_{kind} = {-11 - k}" for k, kind in enumerate(args)),
            ".LOCAL_ACTOR = -4", ".LOCAL_TMP0_WAIT_ARGS = -4", ".LOCAL_TMP1_OTHER_ACTOR = -7", "",
            f"_{name}::"]
    return "\n".join(head + ["        " + line if not line.endswith(":") else line for line in body])


class MovedActorsTests(unittest.TestCase):
    def moved(self, *texts):
        return moved_actors(list(texts), CONSTANTS)

    def test_constant_actor_and_reads(self):
        text = module("a", ["VM_SET_CONST            .LOCAL_ACTOR, 3", "VM_ACTOR_GET_POS        .LOCAL_ACTOR",
                            "VM_ACTOR_SET_POS        .LOCAL_ACTOR", "VM_SET_CONST            .LOCAL_ACTOR, 5",
                            "VM_ACTOR_SET_HIDDEN     .LOCAL_ACTOR, 1"])
        self.assertEqual(self.moved(text), (frozenset({3}), None))

    def test_prefab_argument_through_call_sites(self):
        callee = module("script_patrol", ["VM_RESERVE              8",
                                          "VM_SET                  .LOCAL_ACTOR, .SCRIPT_ARG_2_ACTOR",
                                          "VM_ACTOR_MOVE_TO_X      .LOCAL_ACTOR, 0"],
                        args=("VARIABLE", "VARIABLE", "ACTOR"))
        caller = module("actor_u", ["VM_PUSH_CONST           8 ; Actor 0", "VM_PUSH_VALUE           VAR_A",
                                    "VM_PUSH_VALUE           VAR_B",
                                    "VM_CALL_FAR             ___bank_script_patrol, _script_patrol"])
        other = module("actor_v", ["VM_PUSH_CONST           12 ; Actor 0", "VM_PUSH_VALUE           VAR_A",
                                   "VM_PUSH_VALUE           VAR_B",
                                   "VM_CALL_FAR             ___bank_script_patrol, _script_patrol"])
        self.assertEqual(self.moved(callee, caller, other), (frozenset({8, 12}), None))

    def test_move_relative_rpn_actor(self):
        text = module("m", ["VM_RESERVE              1", "1$:", "VM_RPN", "    .R_INT16    11", "    .R_INT16    -64",
                            "    .R_INT16    0", "    .R_STOP", "VM_ACTOR_MOVE_TO_INIT   .ARG2, 0",
                            "VM_ACTOR_MOVE_TO_X      .ARG2, 0", "VM_POP                  3", "VM_JUMP                 1$"])
        self.assertEqual(self.moved(text), (frozenset({11}), None))

    def test_unresolved_target_means_every_actor(self):
        variable = module("v", ["VM_SET                  .LOCAL_ACTOR, VAR_WHO", "VM_ACTOR_SET_POS        .LOCAL_ACTOR"])
        moved, why = self.moved(variable)
        self.assertIsNone(moved)
        self.assertIn("VM_ACTOR_SET_POS on an actor chosen at run time", why)
        # a label between the set and the move: another path may join there
        joined = module("j", ["VM_SET_CONST            .LOCAL_ACTOR, 2", "1$:", "VM_ACTOR_SET_POS        .LOCAL_ACTOR"])
        self.assertIsNone(self.moved(joined)[0])
        # an aliased slot rewritten in between (.LOCAL_TMP0_WAIT_ARGS is .LOCAL_ACTOR's slot)
        alias = module("w", ["VM_SET_CONST            .LOCAL_ACTOR, 2", "VM_GET_INT8             .LOCAL_TMP0_WAIT_ARGS, _x",
                             "VM_ACTOR_SET_POS        .LOCAL_ACTOR"])
        self.assertIsNone(self.moved(alias)[0])
        # a script argument with no call site, and a native call
        orphan = module("script_o", ["VM_SET                  .LOCAL_ACTOR, .SCRIPT_ARG_0_ACTOR",
                                     "VM_ACTOR_SET_POS        .LOCAL_ACTOR"], args=("ACTOR",))
        self.assertIsNone(self.moved(orphan)[0])
        native = module("n", ["VM_CALL_NATIVE          b_x, _x"])
        self.assertEqual(self.moved(native), (None, "a native call (VM_CALL_NATIVE          b_x, _x)"))

    def test_pinning_counts_as_moving(self):
        pin = module("p", ["VM_SET_CONST            .LOCAL_ACTOR, 4",
                           "VM_ACTOR_SET_FLAGS      .LOCAL_ACTOR, .ACTOR_FLAG_PINNED, .ACTOR_FLAG_PINNED"])
        loop = module("l", ["VM_SET_CONST            .LOCAL_ACTOR, 6", "VM_ACTOR_SET_FLAGS      .LOCAL_ACTOR, 0, 4"])
        self.assertEqual(self.moved(pin, loop), (frozenset({4}), None))


def sheet(name, palettes, frames=None):
    frames = frames or ((HardwareSprite(0, 0, 0, False, False, 0), HardwareSprite(8, 0, 1, False, False, 1)),)
    return NgpcSheet(name, "8x16", (tuple([1] * 64), tuple([2] * 64)), frames, (0,), ((0, 0),) * 8,
                     palettes=tuple(palettes))


def colours(n):
    return tuple((n * 3 + k) & 0xFFF or 1 for k in range(3))


class AssignTests(unittest.TestCase):
    def test_screen_rule(self):
        a = (0, 15, 0, 15)
        self.assertTrue(together(a, (174, 190, 0, 15)))         # camera at 15 shows x 15..174
        self.assertFalse(together(a, (175, 190, 0, 15)))
        self.assertTrue(together(a, (0, 15, 166, 180)))         # 152 lines: y 15..166
        self.assertFalse(together(a, (0, 15, 167, 180)))

    def test_far_apart_share_near_do_not(self):
        presence = [None, ((0, 15, 0, 15),), ((400, 415, 0, 15),), ((100, 115, 0, 15),)]
        slots = assign(presence, [8, 9, 10], "t")
        self.assertEqual(slots[1], slots[2])                    # never on screen together
        self.assertEqual(len({slots[0], slots[1], slots[3]}), 3)

    def test_located_error_and_moving_error(self):
        near = [((40 * k, 40 * k + 15, 0, 15),) for k in range(4)]
        with self.assertRaisesRegex(CompileError, r"around \(\d+, 0\) px 4 native sprite palettes can be on screen"):
            assign(near, [8, 9, 10], "t")
        with self.assertRaisesRegex(CompileError, "4 native sprite palettes are used by the player.*every actor "
                                                  "counts as moving: a native call"):
            assign([None] * 4, [8, 9, 10], "t", "a native call")


class PlaceTests(unittest.TestCase):
    def test_recycled_slots_and_frame_lists(self):
        player = sheet("player", (colours(0),), ((HardwareSprite(0, 0, 0, False, False, 0),),))
        statics = [sheet(f"s{k}", (colours(2 * k + 1), colours(2 * k + 2))) for k in range(1, 12)]
        far = tuple(((300 * k, 300 * k + 15, 0, 15),) for k in range(1, 12))
        with self.assertRaisesRegex(CompileError, "need 23 palettes, 16 at most"):
            place_native_palettes((player, *statics), "t")
        out, pairs, recycle = place_native_palettes((player, *statics), "t", places=(None, *far))
        logical, slots = recycle
        self.assertEqual(len(logical), 23)
        self.assertEqual(sum(1 for s in slots if s == slots[0]), 1)     # the player's palette: its own slot
        self.assertLessEqual(len(set(slots)), 16)
        self.assertEqual(out[0].recycled, ())
        self.assertTrue(all(s.recycled for s in out[1:]))
        self.assertEqual(len(pairs), len(set(slots)))
        source = sprites_source(out, 0, [p for _, p in sorted(dict(pairs).items())] +
                                [(0, 0, 0)] * (16 - len(pairs)), recycle=recycle, patch=True)
        for line in ("    if (ngc_spal_dirty != 0u) ngc_spal_publish();",
                     "        ngc_palette_patch((unsigned char)(s << 2), src, 4u);",
                     "    for (i = 0u; i < 16u; ++i) ngc_spal_now[i] = 0xFFu;",
                     "            ngc_spal_dirty |= (unsigned short)(1u << ps);"):
            self.assertIn(line, source)
        plain = sprites_source(out, 0, [p for _, p in sorted(dict(pairs).items())] +
                               [(0, 0, 0)] * (16 - len(pairs)))
        self.assertNotIn("ngc_spal", plain)


@unittest.skipUnless(all(ENV), "needs NGC_TEST_TOSHIBA, NGC_TEST_EMULATOR and NGC_TEST_BIOS")
class RecycleRomTests(unittest.TestCase):
    def test_every_frame_and_witnesses_differ(self):
        from tools.lab_sprite_recycle import run
        with tempfile.TemporaryDirectory() as tmp:
            for scenes in (False, True):
                r = run(Path(tmp) / f"ok{scenes}", *ENV, scenes=scenes)
                self.assertTrue(r["passed"], r)
                self.assertGreater(r["palettes"], 16)
                self.assertEqual(r["frames_wrong"], 0)
            for witness in ("load", "fade"):
                w = run(Path(tmp) / witness, *ENV, witness=witness)
                self.assertFalse(w["passed"])
                self.assertGreater(w["frames_wrong"], 10)


if __name__ == "__main__":
    unittest.main()
