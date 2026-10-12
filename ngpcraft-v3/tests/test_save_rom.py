"""Save Data / Load Data / If Data Saved in the ROM, on the emulator (docs/M5_SAVE.md steps 4-5).

Laboratory tools/lab_save.py (gbs2 Sample Town and Cave). Oracles: the GBVM
semantics of core.c EXCEPTION_SAVE / EXCEPTION_LOAD, load_save.c and
data_manager.c load_scene(FALSE), written as the expected variables of each
power-on; the host journal oracle (tools/save_journal.py) and NV1 decoder
(tools/save_vm.py) reading the emulated cartridge's two blocks; the journal
oracle replaying the same saves for the block switches. Witnesses: restore
removed, init run on load, erase stub broken.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ngpcraft.compiler import CompileError

TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")
ARTIFACTS = os.environ.get("NGC_TEST_ARTIFACTS")
ROM = bool(TOSHIBA and EMULATOR and BIOS)


def keep(name, root):
    if ARTIFACTS:
        shutil.copytree(root, Path(ARTIFACTS) / name, dirs_exist_ok=True)


def values(sample):
    return [sample["vars"][f"VAR_VARIABLE_{n}"] for n in (20, 21, 22, 23, 24, 25)]


@unittest.skipUnless(ROM, "NGC_TEST_TOSHIBA, NGC_TEST_EMULATOR and NGC_TEST_BIOS required")
class ResumeRomTests(unittest.TestCase):
    FRAMES = 6

    @classmethod
    def setUpClass(cls):
        from tools.lab_save import build, power_on, read_journal
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.manifest, cls.rom = build(root / "case", "resume", TOSHIBA)
        cls.export = root / "case/export"
        cls.first, cls.blocks = power_on(cls.export, EMULATOR, BIOS, cls.FRAMES,
                                         shots={cls.FRAMES: root / "case/boot1.ppm"})
        cls.second, cls.blocks2 = power_on(cls.export, EMULATOR, BIOS, cls.FRAMES, flash=cls.blocks)
        cls.journal1 = read_journal(cls.export, cls.blocks)
        cls.journal2 = read_journal(cls.export, cls.blocks2)
        scenes = cls.manifest["scene_engine"]["scenes"]
        cls.town = cls.manifest["scene_engine"]["start"]
        cls.cave = next(s["index"] for s in scenes if s["index"] != cls.town)
        keep("save-resume", root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_build_selects_the_save_units_and_gate(self):
        self.assertTrue({"save_state", "save_journal", "save_load"} <= set(self.manifest["features"]))
        save = self.rom["save"]
        # the journal holds the slots the scripts name (save_state.used_slots)
        self.assertIn(save["slots"], (1, 2, 3))
        self.assertEqual(self.manifest.get("save_slots"), save["slots"])
        self.assertEqual(save["slots"] * save["pages_per_slot"] + 1, save["journal_pages"])
        self.assertLessEqual(save["journal_pages"], 32)

    def test_first_power_on_saves_switches_and_loads_without_init(self):
        # Save slot 0 (V20 = 1 then 2), slot 1, Cave (V25 = 5), Load slot 0:
        # back in Sample Town at the save point, On Load path (V20 = 3).
        last = self.first[-1]
        self.assertEqual(values(last), [3, 200, 8, 50, 0, 0])
        self.assertEqual(last["scene"], self.town)
        self.assertEqual(last["player"], [14, 22])
        self.assertEqual(self.journal1[0]["variables"], [1, 200, 7, 0, 0, 0])
        self.assertEqual(self.journal1[1]["variables"], [2, 200, 8, 0, 0, 0])
        self.assertIsNone(self.journal1[2])
        for slot in (0, 1):
            self.assertEqual(self.journal1[slot]["length"], self.rom["save"]["payload_max_bytes"])

    def test_second_power_on_sees_both_slots(self):
        last = self.second[-1]
        self.assertEqual(values(last), [2, 100, 8, 50, 0, 0])
        self.assertEqual(last["player"], [14, 22])
        self.assertEqual(self.journal2[0]["variables"], [1, 100, 7, 0, 0, 0])
        self.assertEqual(self.journal2[1]["variables"], [2, 200, 8, 0, 0, 0])

    def test_scene_visible_after_the_load(self):
        # The On Load path's automatic fade in: the scene is shown, not the
        # faded-out screen EXCEPTION_LOAD leaves.
        from tools.check_text import ppm
        _, _, pixels = ppm(Path(self.tmp.name) / "case/boot1.ppm")
        self.assertGreater(len(set(pixels.values()) if isinstance(pixels, dict) else set(pixels)), 4)


@unittest.skipUnless(ROM, "NGC_TEST_TOSHIBA, NGC_TEST_EMULATOR and NGC_TEST_BIOS required")
class WitnessRomTests(unittest.TestCase):
    def run_case(self, witness, case="resume", frames=6):
        from tools.lab_save import build, power_on
        with tempfile.TemporaryDirectory() as tmp:
            build(Path(tmp) / "case", case, TOSHIBA, witness)
            first, blocks = power_on(Path(tmp) / "case/export", EMULATOR, BIOS, frames)
            keep(f"save-witness-{witness}", tmp)
            return first, blocks

    def test_restore_removed_is_detected(self):
        first, _ = self.run_case("no-restore")
        self.assertNotEqual(values(first[-1]), [3, 200, 8, 50, 0, 0])
        self.assertEqual(values(first[-1])[2], 99)          # Cave's state kept

    def test_init_on_load_is_detected(self):
        first, _ = self.run_case("init-on-load")
        # The actors are reloaded: the player is not at the saved position on
        # the frame after the load.
        self.assertIn([12, 20], [s["player"] for s in first[1:3]])

    def test_broken_erase_stub_is_detected(self):
        with self.assertRaises(SystemExit):
            self.run_case("erase-stub", "cycle", 160)


@unittest.skipUnless(ROM, "NGC_TEST_TOSHIBA, NGC_TEST_EMULATOR and NGC_TEST_BIOS required")
class CycleRomTests(unittest.TestCase):
    def test_block_switches_match_the_journal_oracle(self):
        from tools.lab_save import build, run_cycle
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "case"
            build(out, "cycle", TOSHIBA)
            report = run_cycle(out / "export", EMULATOR, BIOS, 160, out)
            keep("save-cycle", tmp)
        self.assertGreaterEqual(report["saves_slot2"], 120)
        self.assertGreaterEqual(report["oracle_erases"], 10)
        self.assertTrue(report["layout_equal"], json.dumps(report["layout"]))
        self.assertEqual(report["last_v26_in_slot2"], report["saves_slot2"])
        self.assertEqual(report["slots"][0]["variables"][:1], [11])


class SingleSceneTests(unittest.TestCase):
    def test_save_in_a_single_scene_export_is_refused(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gbs_fixture import make_project
        from ngpcraft.pipeline import export_project
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp) / "p", [{"id": "s", "command": "EVENT_SAVE_DATA",
                                                      "args": {"saveSlot": 0}}])
            with self.assertRaisesRegex(CompileError, "delivered with scene changes"):
                export_project(project, Path(tmp) / "out")


if __name__ == "__main__":
    unittest.main()
