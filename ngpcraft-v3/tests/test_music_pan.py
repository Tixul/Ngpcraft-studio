"""Stereo offsets (music_pan.py) and redundant instrument reloads (music_library)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from ngpcraft.audio_assets import MusicError
from ngpcraft.audio_resolve import FIELDS
from ngpcraft.music_hold import compile_score, encode_events
from ngpcraft.music_library import _elide_instruments, generate_library, merge_plans
from ngpcraft import music_pan

TONE = dict.fromkeys(FIELDS, 0)
TONE.update(attn=3, env_step=1, env_speed=1, vib_speed=1, sweep_speed=1, sweep_end=1)


def attack(note=58, frames=4, attenuation=3, **extra):
    return {'kind': 'attack', 'duration_frames': frames, 'note': note, 'instrument': 0,
            'attenuation': attenuation, **extra}


def plan(channels, definition=TONE):
    score = {'schema': 'ngpcraft-held-score/1', 'duration_unit_frames': 1,
             'notes': [{'id': 58, 'divider': 218}, {'id': 60, 'divider': 194}], 'channels': channels}
    with tempfile.TemporaryDirectory() as temp:
        sp, bp = Path(temp)/'score.json', Path(temp)/'bank.json'
        sp.write_text(json.dumps(score))
        bp.write_text(json.dumps({'version': 2, 'instruments': [{'name': 'Tone', 'def': definition}]}))
        return compile_score(sp, bp)


class ElisionTests(unittest.TestCase):
    def test_same_stateless_instrument_is_loaded_once_and_loop_entry_keeps_it(self):
        # 246 1 1 | F4 0 F0 3 n1 4 | F4 0 F0 3 n2 4 | loop: F4 0 F0 3 n1 4 | 0
        stream = [246, 1, 1] + [244, 0, 240, 3, 1, 4] + [244, 0, 240, 3, 2, 4] + [244, 0, 240, 3, 1, 4] + [0]
        out, loop = _elide_instruments(stream, 15, [TONE])
        self.assertEqual(out, [246, 1, 1, 244, 0, 240, 3, 1, 4, 240, 3, 2, 4, 244, 0, 240, 3, 1, 4, 0])
        self.assertEqual(loop, 13)
        self.assertEqual(out[loop:loop + 2], [244, 0])

    def test_kept_when_the_reload_changes_the_voice(self):
        base = [246, 1, 1, 244, 0, 240, 3, 1, 4]
        for definition in (dict(TONE, sweep_on=1), dict(TONE, lfo_on=1, lfo_depth=2, lfo_rate=1),
                           dict(TONE, lfo2_on=1, lfo2_depth=2, lfo2_rate=1)):
            stream = base + [244, 0, 240, 3, 2, 4, 0]
            self.assertEqual(_elide_instruments(stream, 3, [definition])[0], stream)
        # The level set after the first attack is restored only by a reload.
        stream = base + [244, 0, 2, 4, 0]
        self.assertEqual(_elide_instruments(stream, 3, [TONE])[0], stream)
        stream = base + [254, 4, 9, 254, 3, 2] + [244, 0, 2, 4, 0]
        self.assertEqual(_elide_instruments(stream, 3, [TONE])[0], stream)
        # A different instrument is always loaded.
        stream = base + [244, 1, 240, 3, 2, 4, 0]
        self.assertEqual(_elide_instruments(stream, 3, [TONE, TONE])[0], stream)

    def test_inactive_lfo_settings_do_not_keep_the_reload(self):
        definition = dict(TONE, lfo_on=1, lfo_depth=0, lfo_rate=3)
        stream = [246, 1, 1, 244, 0, 240, 3, 1, 4, 244, 0, 240, 3, 2, 4, 0]
        self.assertNotIn(244, _elide_instruments(stream, 3, [definition])[0][5:])


class PanStreamTests(unittest.TestCase):
    def test_pan_is_f5_before_the_event_and_closed(self):
        stream, _, _ = encode_events([attack(pan={'left': 0, 'right': 15})], 0, {58: 1}, {0: 0}, 1)
        self.assertEqual(stream[:2], [245, 0x0F])
        hold, _, _ = encode_events([{'kind': 'hold', 'duration_frames': 1, 'pan': {'left': 4, 'right': 0}}],
                                   0, {58: 1}, {0: 0}, 1)
        self.assertEqual(hold[:2], [245, 0x40])
        for pan in ({'left': 16, 'right': 0}, {'left': 0}, {'left': 0, 'right': 0, 'centre': 0}):
            with self.assertRaises(MusicError):
                encode_events([attack(pan=pan)], 0, {58: 1}, {0: 0}, 1)
        with self.assertRaises(MusicError):
            encode_events([{'kind': 'off', 'duration_frames': 1, 'pan': {'left': 0, 'right': 0}}],
                          0, {58: 1}, {0: 0}, 1)

    def test_relay_is_selected_only_for_a_panned_library(self):
        mono = generate_library(merge_plans([('A', plan([{'first': [attack()], 'repeat': [attack(60)]},
                                                           None, None, None]))]))
        self.assertNotIn('Ngc_PanSync', mono['audio.c'])
        self.assertNotIn('ngc_pan', mono['audio.c'])
        self.assertFalse(json.loads(mono['audio-selection.json'])['stereo'])
        panned = generate_library(merge_plans([('A', plan([
            {'first': [attack(pan={'left': 0, 'right': 15})], 'repeat': [attack(60)]}, None, None, None]))]))
        source = panned['audio.c']
        self.assertEqual(source.count('Ngc_PanSync();'), 1)
        self.assertIn('ngc_pan[(v->freq_base >> 5) & 3u] = *v->ptr++;', source)
        self.assertTrue(json.loads(panned['audio-selection.json'])['stereo'])
        self.assertEqual(len(music_pan.RELAY), music_pan.PAN_TABLE + 4)


@unittest.skipUnless(os.environ.get('NGC_TEST_TOSHIBA') and os.environ.get('NGC_TEST_EMULATOR')
                     and os.environ.get('NGC_TEST_BIOS'), 'Toshiba toolchain, emulator and BIOS required')
class PanRomTests(unittest.TestCase):
    def test_panned_voice_is_silent_on_one_side_and_mono_track_is_centred(self):
        from tools.music_library_rom import build_library_rom
        from tools.probe_rom import power_on_ram, verified_tick
        library = merge_plans([
            ('Left', plan([{'first': [attack(frames=40, pan={'left': 0, 'right': 15})],
                            'repeat': [attack(60, frames=40)]},
                           {'first': [attack(frames=40, pan={'left': 15, 'right': 2})],
                            'repeat': [attack(60, frames=40)]}, None, None])),
            ('Mono', plan([{'first': [attack(frames=40)], 'repeat': [attack(60, frames=40)]},
                           None, None, None]))])
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)/'rom'
            build_library_rom(library, dest, os.environ['NGC_TEST_TOSHIBA'])
            emulator = Path(os.environ['NGC_TEST_EMULATOR']).resolve()
            sys.path.insert(0, str(emulator))
            from core import native
            build = json.loads((dest/'rom/build-report.json').read_text())
            rom = (dest/'rom/game.ngc').read_bytes()
            tick = verified_tick(rom, build)
            m = native.NativeMachine(rom, bios=Path(os.environ['NGC_TEST_BIOS']).read_bytes())
            m.set_timing_silicon(word_wait=10, bios_wait=8)
            m.reset(bios_handoff=True)
            power_on_ram(m, 'noise')
            m.set_breakpoints([tick])
            states = []
            for frame in range(130):
                stop, _ = m.run(3_000_000, record=False)
                self.assertEqual(stop.stop_status, 40)
                s = m.apu_state()
                states.append((list(s.square_vol_left), list(s.square_vol_right)))
                m.set_breakpoints([])
                m.run(1, record=False)
                m.set_breakpoints([tick])
            z80 = m.z80()
            self.assertTrue(z80.running and not z80.trapped)
        # Library lab: track 0 from frame 0, track 1 from frame 80 (music_library_rom actions).
        left, right = states[40]
        self.assertNotEqual(left[0], right[0])
        self.assertNotEqual(left[1], right[1])
        quiet = min(right[0], left[1])
        self.assertTrue(left[0] != quiet and right[1] != quiet, states[40])
        left, right = states[120]
        self.assertEqual(left[0], right[0], states[120])


if __name__ == '__main__':
    unittest.main()
