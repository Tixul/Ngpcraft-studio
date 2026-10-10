import argparse
import copy
import hashlib
import unittest

from tools.probe_rom import positive_int, verified_tick
from tools.probe_stream import scroll_planes, registers_match_scroll


class ProbeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rom = bytes(128)
        self.build = {"stage": "linked-rom", "complete_rom": True,
                      "header": {"sha256": hashlib.sha256(self.rom).hexdigest(), "size_bytes": 128},
                      "link_audit": {"public_symbols": {"_ngc_game_tick":
                          {"address": 0x200040, "section": "f_code"}}}}

    def test_matching_rom_uses_linked_tick_address(self):
        self.assertEqual(verified_tick(self.rom, self.build), 0x200040)

    def test_modified_or_truncated_rom_cannot_use_stale_report(self):
        for rom in (self.rom[:-1], b"x" + self.rom[1:]):
            with self.subTest(rom=rom), self.assertRaisesRegex(ValueError, "does not match"):
                verified_tick(rom, self.build)

    def test_incomplete_report_is_rejected(self):
        self.build["complete_rom"] = False
        with self.assertRaises(ValueError):
            verified_tick(self.rom, self.build)

    def test_breakpoint_must_be_inside_rom_code(self):
        for address in (0x4000, 0x200080, True, "0x200040"):
            build = copy.deepcopy(self.build)
            build["link_audit"]["public_symbols"]["_ngc_game_tick"]["address"] = address
            with self.subTest(address=address), self.assertRaises(ValueError):
                verified_tick(self.rom, build)
        self.build["link_audit"]["public_symbols"]["_ngc_game_tick"]["section"] = "f_area"
        with self.assertRaises(ValueError):
            verified_tick(self.rom, self.build)

    def test_observation_budgets_must_be_positive(self):
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                positive_int(value)


class ScrollPlaneEvidenceTests(unittest.TestCase):
    def test_allocation_comes_from_the_manifest(self):
        self.assertEqual(scroll_planes({"background": {"planes": [1, 2]}}), (1, 2))
        self.assertEqual(scroll_planes({"scene_engine": {"background": {"planes": [2]}}}), (2,))
        for planes in ([], [True], [3], [1, 1]):
            with self.subTest(planes=planes), self.assertRaises(ValueError):
                scroll_planes({"background": {"planes": planes}})

    def test_each_used_plane_is_checked_without_demanding_scroll_from_the_free_plane(self):
        rows = [{"scroll": [257, 258], "regs_low": [0, 4, 1, 2]}]
        self.assertTrue(registers_match_scroll(rows, (2,)))
        self.assertFalse(registers_match_scroll(rows, (1,)))
        self.assertFalse(registers_match_scroll(rows, (1, 2)))
        rows[0]["regs_low"] = [1, 2, 1, 2]
        self.assertTrue(registers_match_scroll(rows, (1, 2)))
        rows[0]["regs_low"][3] = 3
        self.assertFalse(registers_match_scroll(rows, (1, 2)))
