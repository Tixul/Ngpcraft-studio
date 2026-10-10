"""M1 baseline: the import report of the official gbs2 project is exact."""
import unittest

from tools.import_report_gbsproj import report
from tools.parity_gbstudio import REFERENCE

GBS2 = REFERENCE / "appData/templates/gbs2"


@unittest.skipUnless((GBS2 / "project.gbsproj").is_file(), "GB Studio reference not present")
class Gbs2ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = report(GBS2)

    def test_counts_match_the_parity_catalogue(self):
        s = self.r["summary"]
        self.assertEqual(s["scenes"], 17)
        self.assertEqual(s["distinct_events"], 54)
        self.assertEqual(s["events_unknown"], 0)
        self.assertEqual(sum(e["uses"] for e in self.r["events"].values()), 909)

    def test_hardware_adaptations_are_reported(self):
        s = self.r["summary"]
        self.assertEqual(s["one_screen_high_scenes"], 14)
        self.assertEqual(len(s["scenes_using_select"]), 3)
        self.assertEqual(s["worst_sprite_hw_per_frame"], 38)   # elephant, 8x16 sheets
        self.assertEqual(s["music_files"], 13)

    def test_only_delivered_events_are_accepted(self):
        s = self.r["summary"]
        # EVENT_SET_VALUE and EVENT_WAIT (2026-09-29, tests/test_gbvm_backend.py),
        # EVENT_IF, EVENT_LOOP and EVENT_GROUP (2026-09-29, tests/test_gbvm_flow.py),
        # EVENT_DEC_VALUE (EVENT_INC_VALUE is not in gbs2; tests/test_gbvm_values.py),
        # and 8 actor events (2026-09-30, tests/test_actor_events.py): ACTIVATE,
        # DEACTIVATE, SET_POSITION, SET_DIRECTION, SET_FRAME, SET_MOVEMENT_SPEED,
        # SET_ANIMATION_SPEED, COLLISIONS_DISABLE (HIDE, SHOW, COLLISIONS_ENABLE are not in gbs2),
        # and the 2 movement events (2026-09-30, tests/test_actor_moves.py): MOVE_TO, MOVE_RELATIVE.
        # EVENT_SWITCH_SCENE and EVENT_CALL_CUSTOM_EVENT (2026-10-01, tests/test_scene_switch.py,
        # tests/test_custom_events.py). EVENT_SET_INPUT_SCRIPT (2026-10-01, tests/test_input_scripts.py;
        # EVENT_REMOVE_INPUT_SCRIPT is not in gbs2).
        # EVENT_SCENE_PUSH_STATE, EVENT_SCENE_POP_STATE (2026-10-01, tests/test_scene_stack.py;
        # POP_ALL and RESET are not in gbs2).
        # EVENT_PLATFORMER_STATE_SET, EVENT_PLAYER_BOUNCE, EVENT_ENGINE_FIELD_SET (2026-10-05,
        # tests/test_platform.py).
        # EVENT_ACTOR_STOP_UPDATE (2026-10-05, tests/test_actor_stop_update.py).
        # EVENT_ACTOR_SET_STATE (tests/test_actor_states.py).
        # EVENT_LAUNCH_PROJECTILE (2026-10-06, tests/test_projectile_events.py).
        # EVENT_CHOICE (2026-10-06, tests/test_choice.py).
        # EVENT_REPLACE_TILE_XY_SEQUENCE (2026-10-07, tests/test_replace_tiles.py).
        # Then save (SAVE/LOAD/IF_SAVED, docs/M5_SAVE.md), audio (MUSIC_PLAY/STOP,
        # SOUND_PLAY_EFFECT, docs/M5_AUDIO*.md), overlay and the rest: every gbs2
        # event is delivered (tools.parity_gbstudio report: gbs2_delivered 54/54).
        self.assertEqual(s["events_accepted"], 54)
        self.assertEqual(s["events_refused_until_delivered"], 0)


if __name__ == "__main__":
    unittest.main()
