"""The music on the Z80 (ngpc.engine_speedups, docs/PLAN_MUSIQUE_Z80.md): stream analysis, the
fallback (a driver it does not transcribe keeps audio.c unchanged), the transform, and on ROM
the ordered PSG writes of the C driver against the Z80 on random streams (tools/lab_music_z80),
with a planted fault as witness. The real corpora (MegaX, gbs2) are run by the lab."""
import json
import os
from pathlib import Path
import re
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
EFFECTS_SEED = 3   # random instruments of tools/lab_music_z80 with a vibrato, an LFO and a pitch curve


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


def native_fixture(root):
    """The same project playing its Sound Creator song only (.ngps: the native mode), its
    instruments with an ADSR and no other effect: the driver variant 0:0, feature "adsr"."""
    from test_music_events import project_fixture
    from tools.demo_scene_switch import ev, num, set_script
    project, folders, _, first, second = project_fixture(root)
    sidecar = json.loads((project / "ngpcraft.json").read_text())
    del sidecar["resources"][second]
    (project / "ngpcraft.json").write_text(json.dumps(sidecar))
    bank = json.loads((project / "native/bank.json").read_text())
    for k, inst in enumerate(bank["instruments"]):
        inst["def"].update(env_on=0, env_curve_id=0, pitch_curve_id=0, macro_id=0, vib_on=0, sweep_on=0,
                           lfo_on=0, lfo2_on=0, adsr_on=1, adsr_attack=1 + k, adsr_decay=2, adsr_sustain=6 + k,
                           adsr_sustain_rate=k, adsr_release=3)
    (project / "native/bank.json").write_text(json.dumps(bank))
    set_script(folders["sample_town"], [ev("play", "EVENT_MUSIC_PLAY", musicId=first),
                                        ev("wait", "EVENT_WAIT", units="frames", frames=num(200))])
    set_script(folders["cave"], [])
    return project, folders


def effects_fixture(root):
    """The native fixture with a pitch curve, an envelope curve, a macro, a vibrato and an LFO in
    its bank (driver variant 7:0, every table); the lab draws its instruments at random
    (RomTests.corpus)."""
    project, folders = native_fixture(root)
    bank = json.loads((project / "native/bank.json").read_text())
    bank["instruments"][0]["def"].update(pitch_curve_id=1, vib_on=1, vib_depth=3, env_on=1, env_curve_id=1)
    bank["instruments"][1]["def"].update(lfo_on=1, lfo_depth=8, lfo_rate=2, macro_id=1)
    (project / "native/bank.json").write_text(json.dumps(bank))
    return project, folders


EFFECTS = {"adsr", "env", "sweep", "vib", "lfo", "pcurve"}
# The envelope curve plays when no ADSR phase runs (BgmVoice_UpdateFx skips the envelope then).
TABLES = {"env", "ecurve", "macro", "vib", "pcurve"}
TABLES_SEED = 7


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
        row = dict.fromkeys(music_z80_asm.INSTRUMENT_COLUMNS, 1)
        code, _ = assemble(music_z80_asm.source([row] * 40, 16, {"adsr", "hold"}))
        self.assertLessEqual(len(code), music_z80_asm.layout(16, {"adsr", "hold"})["code_end"])
        self.assertEqual(code[:3], bytes([0xC3, 0x13, 0x00]))   # the relay's entry kept


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class TransformTests(unittest.TestCase):
    def test_native_song_selects_the_adsr_player(self):
        with tempfile.TemporaryDirectory() as temp:
            project, _ = native_fixture(Path(temp))
            on, manifest = export(project, Path(temp) / "on", True)
        self.assertTrue(manifest["music_z80"]["music_z80"], manifest["music_z80"])
        self.assertEqual(manifest["music_z80"]["features"], ["adsr"])
        self.assertNotIn("BgmVoice_UpdateFx", on)

    def test_untranscribed_stream_falls_back_unchanged(self):
        # A stream that turns an effect on (F2 1 1 0: a vibrato) is not transcribed: audio.c as
        # it was, the reason in the report. The fixture itself (driver 7:8, every table and
        # extension) is transcribed.
        from ngpcraft.music_z80 import fast_music
        from test_music_events import project_fixture
        with tempfile.TemporaryDirectory() as temp:
            project, *_ = project_fixture(Path(temp))
            off, _ = export(project, Path(temp) / "off", False)
        self.assertTrue(fast_music(off)[1]["music_z80"])
        text = re.sub(r"(static const unsigned char ngc_music_track\d+_ch0\[\] = \{\n    )", r"\g<1>242, 1, 1, 0, ", off, count=1)
        self.assertNotEqual(text, off)
        out, report = fast_music(text)
        self.assertFalse(report["music_z80"])
        self.assertIn("effect", report["why"])
        self.assertEqual(out, text)

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
        address = 0x7000 + music_z80_asm.SHARED_LAYOUT["s_sfx_cmd"][0]
        self.assertIn(f"#define s_sfx_cmd (*(PsgCmd (*)[4])0x{address:04X})", on)
        self.assertEqual(manifest["music_z80"]["features"], ["divider", "hold", "level"])


@unittest.skipUnless(all(os.environ.get(k) for k in TOOLS) and GBS2.is_dir(), "toolchain, emulator and BIOS required")
class RomTests(unittest.TestCase):
    def corpus(self, seed, fixture=None, instruments=None):
        from tools.lab_music_z80 import corpus
        root = Path(tempfile.mkdtemp(prefix="ngc_z80music_"))
        project, folders = (fixture or flat_fixture)(root / "fixture")
        scene = json.loads((folders["sample_town"] / "scene.gbsres").read_text(encoding="utf-8"))["name"]
        return corpus(project, root / "lab", scene, *(Path(os.environ[k]) for k in ("NGC_TEST_EMULATOR", "NGC_TEST_BIOS",
                                                                                   "NGC_TEST_TOSHIBA")),
                      song_frames=900, random_seed=seed, effects=False, random_instruments=instruments)

    def test_random_streams_write_as_the_c_driver(self):
        result = self.corpus(5)
        self.assertTrue(result["music_z80"]["music_z80"])
        self.assertTrue(result["c_is_prefix"])
        self.assertGreater(result["writes"][0], 300)

    def test_native_adsr_writes_as_the_c_driver(self):
        result = self.corpus(7, native_fixture)
        self.assertEqual(result["music_z80"]["features"], ["adsr"])
        self.assertTrue(result["c_is_prefix"])
        self.assertGreater(result["writes"][0], 300)

    def test_witness_adsr_fault_is_seen(self):
        # The release paced by the decay: the release slower or faster.
        body = music_z80_asm.BODY.replace("uf_release:\n    ld d, (ix+v_rel)", "uf_release:\n    ld d, (ix+v_dec)", 1)
        self.assertNotEqual(body, music_z80_asm.BODY)
        with mock.patch.object(music_z80_asm, "BODY", body):
            result = self.corpus(7, native_fixture)
        self.assertFalse(result["c_is_prefix"])

    def test_random_effects_write_as_the_c_driver(self):
        # Instruments drawn at random: every effect BgmVoice_UpdateFx runs, every LFO wave and
        # algorithm, the corrections of BgmVoice_ApplyInstrument, the pitch curves.
        result = self.corpus(EFFECTS_SEED, effects_fixture, EFFECTS)
        self.assertTrue(result["music_z80"]["music_z80"], result["music_z80"])
        self.assertLessEqual({"vib", "lfo", "pcurve"}, set(result["music_z80"]["features"]))
        self.assertTrue(result["c_is_prefix"])
        self.assertGreater(result["writes"][0], 300)

    def test_witness_lfo_fault_is_seen(self):
        # The LFO counter reloaded with its depth instead of its rate.
        body = music_z80_asm.BODY.replace("lt_step:\n    ld a, (iy+1)", "lt_step:\n    ld a, (iy+2)", 1)
        self.assertNotEqual(body, music_z80_asm.BODY)
        with mock.patch.object(music_z80_asm, "BODY", body):
            result = self.corpus(EFFECTS_SEED, effects_fixture, EFFECTS)
        self.assertFalse(result["c_is_prefix"])

    def test_witness_vibrato_fault_is_seen(self):
        # The vibrato's direction reversed in the divider.
        body = music_z80_asm.BODY.replace("bit 7, (ix+v_vdir)\n    jr z, cfs_vadd", "bit 7, (ix+v_vdir)\n    jr nz, cfs_vadd", 1)
        self.assertNotEqual(body, music_z80_asm.BODY)
        with mock.patch.object(music_z80_asm, "BODY", body):
            result = self.corpus(EFFECTS_SEED, effects_fixture, EFFECTS)
        self.assertFalse(result["c_is_prefix"])

    def test_random_tables_write_as_the_c_driver(self):
        # Envelope curves and macros drawn at random (their tables' steps too).
        result = self.corpus(TABLES_SEED, effects_fixture, TABLES)
        self.assertLessEqual({"ecurve", "macro"}, set(result["music_z80"]["features"]))
        self.assertTrue(result["c_is_prefix"])
        self.assertGreater(result["writes"][0], 300)

    def test_witness_macro_fault_is_seen(self):
        # A macro step one frame longer.
        body = music_z80_asm.BODY.replace("macro_take:\n    ld a, (hl)\n", "macro_take:\n    ld a, (hl)\n    inc a\n", 1)
        self.assertNotEqual(body, music_z80_asm.BODY)
        with mock.patch.object(music_z80_asm, "BODY", body):
            result = self.corpus(TABLES_SEED, effects_fixture, TABLES)
        self.assertFalse(result["c_is_prefix"])

    def test_witness_envelope_curve_fault_is_seen(self):
        # The envelope curve read backwards from its first step.
        body = music_z80_asm.BODY.replace("ec_next:\n    inc (ix+v_eidx)", "ec_next:\n    dec (ix+v_eidx)", 1)
        self.assertNotEqual(body, music_z80_asm.BODY)
        with mock.patch.object(music_z80_asm, "BODY", body):
            result = self.corpus(TABLES_SEED, effects_fixture, TABLES)
        self.assertFalse(result["c_is_prefix"])

    def test_routine_and_mute_write_as_the_c_driver(self):
        # Set Music Routine and Mute Channel's own projects (their events kept), every driver
        # table and held-stream extension: the C's writes in order.
        from tools.lab_music_z80_project import case, compare
        for name in ("routine", "mute"):
            with self.subTest(name):
                root = Path(tempfile.mkdtemp(prefix=f"ngc_z80{name}_"))
                result = compare(case(name, root / "project"), root, 1500,
                                 *(Path(os.environ[k]) for k in ("NGC_TEST_EMULATOR", "NGC_TEST_BIOS", "NGC_TEST_TOSHIBA")))
                self.assertIn(name, result["music_z80"]["features"])
                self.assertTrue(result["c_is_prefix"])
                self.assertGreater(result["writes"][0], 1000)

    def test_witness_planted_fault_is_seen(self):
        body = music_z80_asm.BODY.replace("cfs_div:\n    ld a, l\n    and 0x0f\n", "cfs_div:\n    ld a, l\n    and 0x07\n", 1)
        self.assertNotEqual(body, music_z80_asm.BODY)
        with mock.patch.object(music_z80_asm, "BODY", body):
            result = self.corpus(5)
        self.assertFalse(result["c_is_prefix"])


if __name__ == "__main__":
    unittest.main()
