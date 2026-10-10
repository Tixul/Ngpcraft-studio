"""Long music previews preserve all timed rows and the loop order boundary."""
import unittest
from ngpcraft.audio_assets import MusicError, validate_song
from ngpcraft.uge_convert import pack_preview_patterns

class PatternPackingTests(unittest.TestCase):
    def pattern(self, note):
        return {"length": 16, "channels": [[{"n": note, "i": 0, "a": 0}] + [None] * 15,
                                            [None] * 16, [None] * 16, [None] * 16]}

    def test_large_repeated_score_keeps_sequence_and_loop(self):
        source = [self.pattern(i % 4) for i in range(100)]
        patterns, order = pack_preview_patterns(source)
        self.assertEqual(len(patterns), 4)
        self.assertEqual([patterns[i] for i in order], source)
        song = validate_song({"version": 1, "patterns": patterns, "order": order, "loop_point": 3})
        self.assertEqual([song["patterns"][i] for i in song["order"][3:]], source[3:])

    def test_existing_tables_are_unchanged_and_real_limits_are_refused(self):
        source = [self.pattern(1), self.pattern(1)]
        self.assertEqual(pack_preview_patterns(source), (source, [0, 1]))
        with self.assertRaisesRegex(MusicError, "64 distinct"):
            pack_preview_patterns([self.pattern(i) for i in range(65)])
        with self.assertRaisesRegex(MusicError, "256 order"):
            pack_preview_patterns([self.pattern(1)] * 257)
