"""The music on the Z80 (ngpc.engine_speedups, docs/PLAN_MUSIQUE_Z80.md): stream analysis, the
fallback (a driver it does not transcribe keeps audio.c unchanged), the transform, and on ROM
the ordered PSG writes of the C driver against the Z80 on random streams (tools/lab_music_z80),
with a planted fault as witness. The real corpora (MegaX, gbs2) are run by the lab."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from gbs_fixture import GBS2
from ngpcraft import music_z80_asm
from ngpcraft.music_z80 import Z80_OWNED, frame_bytes, ops
from ngpcraft.pipeline import export_project
from ngpcraft.z80asm import assemble

sys.path.insert(0, str(Path(__file__).resolve().parent))
TOOLS = ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")


def flat_fixture(root):
    """tests/test_music_events.project_fixture playing its held score only, with flat
    instruments and no noise note: the driver variant 0:4 (level and divider holds)."""
    from test_music_events import project_fixture
    from tools.demo_scene_switch import ev, num, set_script
    project, folders, _, first, second = project_fixture(root)
    # Only the held score is declared: the .ngps song needs another driver variant.
    sidecar = json.loads((project / "ngpcraft.json").read_text())
    del sidecar["resources"][first]
    (project / "ngpcraft.json").write_text(json.dumps(sidecar))
    bank = json.loads((project / "native/second-bank.json").read_text())
    for inst in bank["instruments"]:
        for key in ("env_on", "env_curve_id", "pitch_curve_id", "macro_id", "vib_on", "sweep_on", "adsr_on",
                    "lfo_on", "lfo2_on"):
            inst["def"][key] = 0
    (project / "native/second-bank.json").write_text(json.dumps(bank))
    score = json.loads((project / "native/score.json").read_text())
    for channel in score["channels"]:
        for part in ("first", "repeat") if channel else ():
            for event in channel.get(part, []):
                event.pop("noise_note", None)
                if event["kind"] == "hold" and "divider" not in event and "attenuation" not in event:
                    event["attenuation"] = 4
    (project / "native/score.json").write_text(json.dumps(score))
    set_script(folders["sample_town"], [ev("play", "EVENT_MUSIC_PLAY", musicId=second),
                                        ev("wait", "EVENT_WAIT", units="frames", frames=num(200))])
    set_script(folders["cave"], [])
    return project, folders


def export(project, out, speedups):
    from tools.lab_speedups import set_speedups
    set_speedups(project, speedups)
    export_project(project, out)
    return (out / "src/audio.c").read_text(encoding="utf-8"), json.loads((out / "export.json").read_text(encoding="utf-8"))


class StreamTests(unittest.TestCase):
    def test_ops_and_frame_bytes(self):
        # F6 01 01 | FE 03 05 | F4 00 F0 03 11 01 | FE 04 07 FE 05 2C 01 FE 03 02 | 00
        data = [0xF6, 1, 1, 0xFE, 3, 5, 0xF4, 0, 0xF0, 3, 17, 1, 0xFE, 4, 7, 0xFE, 5, 0x2C, 1, 0xFE, 3, 2, 0]
        self.assertEqual([k for _, _, k in ops(data)],
                         ["zero", "timed", "zero", "zero", "timed", "zero", "zero", "timed", "end"])
        # The longest frame: FE 04 07 FE 05 2C 01 FE 03 02 = 10 bytes; through the end into the
        # loop (offset 6): 00, F4 00, F0 03, 11 01 = 7 bytes.
        self.assertEqual(frame_bytes(data, 6), 10)
        self.assertEqual(frame_bytes(data, 12), 11)       # 00 + the 10 bytes again
        self.assertEqual(frame_bytes([0xFE, 3, 1, 0], 3), 3)   # loop on the end itself: 00 + its peek = 2
        self.assertIsNone(frame_bytes([0xF0, 3, 0], 0))   # a loop without a timed opcode
        self.assertEqual(ops([0xF2, 1, 2, 3, 0])[0][2], "effect")
        self.assertEqual(ops([0xFE, 1, 0, 0, 0, 0, 0, 0])[0][2], "effect")

    def test_program_fits(self):
        code, symbols = assemble(music_z80_asm.source([0] * 32, 40))
        self.assertLessEqual(symbols["inst_attn"] + 32, music_z80_asm.RING)
        self.assertEqual(code[:3], bytes([0xC3, 0x13, 0x00]))   # the relay's entry kept


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class TransformTests(unittest.TestCase):
    def test_untranscribed_driver_falls_back_unchanged(self):
        from test_music_events import project_fixture
        with tempfile.TemporaryDirectory() as temp:
            project, *_ = project_fixture(Path(temp))
            off, _ = export(project, Path(temp) / "off", False)
            on, manifest = export(project, Path(temp) / "on", True)
        self.assertFalse(manifest["music_z80"]["music_z80"])
        self.assertIn("differs from the transcribed one", manifest["music_z80"]["why"])
        self.assertEqual(off, on)

    def test_flat_held_score_runs_on_the_z80(self):
        with tempfile.TemporaryDirectory() as temp:
            project, _ = flat_fixture(Path(temp))
            off, manifest_off = export(project, Path(temp) / "off", False)
            on, manifest = export(project, Path(temp) / "on", True)
        self.assertNotIn("music_z80", manifest_off)
        self.assertNotIn("NGC_Z80B", off)
        self.assertTrue(manifest["music_z80"]["music_z80"])
        self.assertIn("music_z80", manifest["features"])
        for name in ("BgmVoice_Step", "BgmVoice_CommandFromState", "BufferReplayPending", "s_bgm_instruments"):
            self.assertNotIn(name, on)
        for name in Z80_OWNED:
            self.assertNotRegex(on, rf"\b{name}\b")
        self.assertIn("static void ngc_z80_wait(void)", on)
        self.assertIn("#define s_sfx_cmd (*(PsgCmd (*)[4])0x7D20)", on)


@unittest.skipUnless(all(os.environ.get(k) for k in TOOLS) and GBS2.is_dir(), "toolchain, emulator and BIOS required")
class RomTests(unittest.TestCase):
    def corpus(self, seed):
        from tools.lab_music_z80 import corpus
        root = Path(tempfile.mkdtemp(prefix="ngc_z80music_"))
        project, folders = flat_fixture(root / "fixture")
        scene = json.loads((folders["sample_town"] / "scene.gbsres").read_text(encoding="utf-8"))["name"]
        return corpus(project, root / "lab", scene, *(Path(os.environ[k]) for k in ("NGC_TEST_EMULATOR", "NGC_TEST_BIOS",
                                                                                   "NGC_TEST_TOSHIBA")),
                      song_frames=900, random_seed=seed, effects=False)

    def test_random_streams_write_as_the_c_driver(self):
        result = self.corpus(5)
        self.assertTrue(result["music_z80"]["music_z80"])
        self.assertTrue(result["c_is_prefix"])
        self.assertGreater(result["writes"][0], 300)

    def test_witness_planted_fault_is_seen(self):
        body = music_z80_asm.BODY.replace("cfs_div:\n    ld a, l\n    and 0x0f\n", "cfs_div:\n    ld a, l\n    and 0x07\n", 1)
        self.assertNotEqual(body, music_z80_asm.BODY)
        with mock.patch.object(music_z80_asm, "BODY", body):
            result = self.corpus(5)
        self.assertFalse(result["c_is_prefix"])


if __name__ == "__main__":
    unittest.main()
