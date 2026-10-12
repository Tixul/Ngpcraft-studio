"""An effect takes only the voices it makes heard (project_sfx._voices)."""
import unittest

from ngpcraft.project_sfx import attach, used_voices

TONE = [{'frames': 2, 'tone_divider': 744, 'tone_attenuation': 0, 'noise': 0, 'noise_attenuation': 15}]
NOISE = [{'frames': 2, 'tone_divider': 1, 'tone_attenuation': 15, 'noise': 4, 'noise_attenuation': 0}]
AUDIO = {'audio.c': 'void ngc_audio_service(void)\n{\n    Sounds_Update();\n}\n'
                    'void ngc_audio_init(void)\n{\n    Sounds_Init();\n}\n',
         'audio.h': '#endif\n'}


def shared(selected):
    return {'nativeSfx': {'beep': TONE, 'crash': NOISE}, 'nativeSfxSelected': set(selected)}


class SfxVoiceTests(unittest.TestCase):
    def test_each_effect_records_its_voices(self):
        source = attach(dict(AUDIO), shared({0, 1}))['audio.c']
        self.assertIn('{ngc_sfx_steps0, 1, 1}', source)
        self.assertIn('{ngc_sfx_steps1, 1, 2}', source)
        self.assertIn('voices & 1u)\n        Sfx_PlayToneEx', source)
        self.assertIn('voices & 2u)\n        Sfx_PlayNoiseEx', source)

    def test_unused_voice_entry_is_not_called(self):
        source = attach(dict(AUDIO), shared({0}))['audio.c']
        self.assertIn('Sfx_PlayToneEx', source)
        self.assertNotIn('Sfx_PlayNoiseEx', source)
        self.assertEqual(used_voices(shared({1})), 2)
        self.assertEqual(used_voices(shared({0, 1})), 3)

    def test_a_voice_silent_on_some_steps_stays_owned(self):
        rows = [dict(TONE[0]), dict(TONE[0], tone_attenuation=15)]
        self.assertEqual(used_voices({'nativeSfx': {'a': rows}, 'nativeSfxSelected': {0}}), 1)


if __name__ == '__main__':
    unittest.main()
