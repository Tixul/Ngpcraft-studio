"""Sound effect files -> native timelines (docs/M7_SFX_CONVERT.md): GB Studio's
reading of FX Hammer and VGM, the APU model, losses, refusals, the effect
index soundPlay plays, and the ROM lab when the toolchain is there."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ngpcraft.compiler import CompileError
from ngpcraft.project_sfx import key
from ngpcraft.sfx_convert import convert, fxhammer_effects
from tools.reference import REFERENCE

V3 = Path(__file__).resolve().parents[1]
BANK = REFERENCE / "appData/templates/gbs2/assets/sounds/Tronimal_Sound_Effects.sav"
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


def vgm(path, commands):
    """A minimal DMG VGM 1.61: header, data at 0x100, then the commands."""
    head = bytearray(0x100)
    head[0:4] = b"Vgm "
    head[8:12] = (0x161).to_bytes(4, "little")
    head[0x34:0x38] = (0x100 - 0x34).to_bytes(4, "little")
    path.write_bytes(bytes(head) + bytes(commands) + b"\x66")


def write(reg, value):
    return [0xB3, reg - 0x10, value]


class ConvertTests(unittest.TestCase):
    @unittest.skipUnless(BANK.is_file(), "GB Studio 4.3.2 reference absent")
    def test_fxhammer_bank_of_gbs2(self):
        self.assertEqual(len(fxhammer_effects(BANK.read_bytes())), 10)
        first = convert(BANK, 0)
        self.assertEqual(first["format"], "ngpcraft-native-sfx/1")
        # 4 frames per FX Hammer duration unit, the trailing silence trimmed.
        self.assertEqual(first["frames"], sum(r["frames"] for r in first["rows"]))
        self.assertTrue(all(1 <= r["frames"] <= 254 for r in first["rows"]))
        clamped = convert(BANK, 20)
        self.assertEqual(clamped["rows"], convert(BANK, 9)["rows"])
        self.assertIn("effect 20 clamped to 9 as GB Studio does (10 effects)", clamped["losses"])

    def test_vgm_square_trigger_and_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "beep.vgm"
            freq = 1750                          # 131072 / 298 = 439.8 Hz
            vgm(path, write(0x16, 0x80) + write(0x17, 0xF1) + write(0x18, freq & 0xFF) +
                write(0x19, 0x80 | freq >> 8) + [0x62] * 30)
            result = convert(path)
        divider = round(96000 / (131072 / (2048 - freq)))
        rows = result["rows"]
        self.assertEqual(rows[0]["tone_divider"], divider)
        self.assertEqual(rows[0]["tone_attenuation"], 0)
        # NR22 = 0xF1: one volume step down every 60/64 frame.
        self.assertEqual([r["tone_attenuation"] for r in rows[:4]], [0, 1, 2, 3])
        self.assertEqual(result["losses"], [])

    def test_vgm_quirks_follow_gbstudio(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.vgm"
            # A wave channel write and a square row without its NRx4 write.
            vgm(path, write(0x1A, 0x80) + write(0x1E, 0x87) + write(0x17, 0xF0) + [0x62] * 3)
            result = convert(path)
        self.assertIn("channel 3 (wave) writes ignored, as GB Studio's VGM path drops them", result["losses"])
        self.assertEqual([r["tone_attenuation"] for r in result["rows"]], [15])

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            wav.write_bytes(b"RIFF")
            with self.assertRaisesRegex(CompileError, "sample playback"):
                convert(wav)
            bad = Path(tmp) / "a.vgm"
            bad.write_bytes(b"nope")
            with self.assertRaisesRegex(CompileError, "not a VGM file"):
                convert(bad)
            one = Path(tmp) / "b.vgm"
            vgm(one, [0x62])
            with self.assertRaisesRegex(CompileError, "holds one effect"):
                convert(one, 2)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.vgm"
            vgm(path, write(0x21, 0xF0) + write(0x22, 0x00) + write(0x23, 0x80) + [0x62] * 5)
            done = subprocess.run([sys.executable, "-B", "-m", "ngpcraft", "convert-sfx", str(path)],
                                  cwd=V3, capture_output=True, text=True,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual(result["rows"][0]["noise"] & 4, 4)          # white noise
        self.assertEqual(result["rows"][0]["noise_attenuation"], 0)


class EffectKeyTests(unittest.TestCase):
    SOUNDS = {"bank": ("fxhammer", 10), "song": ("vgm", 0)}

    def test_play_follows_soundplay(self):
        self.assertEqual(key({"type": "bank", "effect": 99}, self.SOUNDS), "bank:9")
        self.assertEqual(key({"type": "bank", "effect": -3}, self.SOUNDS), "bank:0")
        self.assertEqual(key({"type": "song", "effect": 4}, self.SOUNDS), "song:0")
        self.assertEqual(key({"type": "bank"}, self.SOUNDS), "bank:0")

    def test_text_sound_takes_the_index_as_given(self):
        self.assertEqual(key({"type": "bank", "effect": 3}, self.SOUNDS, text=True), "bank:3")
        self.assertEqual(key({"type": "song", "effect": 3}, self.SOUNDS, text=True), "song:0")
        with self.assertRaisesRegex(CompileError, "would not link"):
            key({"type": "bank", "effect": 12}, self.SOUNDS, text=True)


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and BANK.is_file(),
                     "NGC_TEST_TOSHIBA, NGC_TEST_EMULATOR, NGC_TEST_BIOS, GB Studio reference")
class SfxRomTests(unittest.TestCase):
    def test_psg_plays_the_converted_timelines(self):
        from tools.lab_sfx_convert import run
        with tempfile.TemporaryDirectory() as tmp:
            sound = Path(tmp) / "effect.vgm"
            freq = 1600
            vgm(sound, write(0x16, 0x80) + write(0x17, 0xF3) + write(0x18, freq & 0xFF) +
                write(0x19, 0xC0 | freq >> 8) + write(0x21, 0xA1) + write(0x22, 0x31) + write(0x23, 0x80) +
                [0x62] * 40)
            report, _, _ = run(Path(tmp) / "lab", TOSHIBA, EMULATOR, BIOS, sound)
            self.assertTrue(report["passed"], report["compared"])
            witness, _, _ = run(Path(tmp) / "witness", TOSHIBA, EMULATOR, BIOS, sound, witness=True)
            self.assertFalse(witness["passed"])


if __name__ == "__main__":
    unittest.main()
