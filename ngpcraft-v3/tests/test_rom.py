import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError, analyze
from ngpcraft.emitter import export, generate
from ngpcraft.toolchain import (STACK_RESERVE_BYTES, audit_link_map, build_rom,
                                check_stack_reserve, validate_rom)

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


def fixture():
    return {"schema": "ngpcraft-ir/0.1", "start": "room", "scenes": [{"id": "room"}]}


class RomContractTests(unittest.TestCase):
    def test_empty_game_excludes_hardware_module(self):
        files = generate(analyze(fixture()), "rom")
        manifest = json.loads(files["export.json"])
        self.assertEqual(manifest["sources"], ["src/cart.c", "src/game.c", "src/startup.c"])
        self.assertNotIn("src/hardware.c", files)
        self.assertNotIn("ngc_palette", "\n".join(files.values()))

    def test_module_export_excludes_cartridge_bootstrap(self):
        files = generate(analyze(fixture()))
        self.assertNotIn("src/startup.c", files)
        self.assertNotIn("src/cart.c", files)
        self.assertNotIn("ngpc.lcf", files)

    def test_rom_without_signature_is_rejected(self):
        with self.assertRaises(CompileError):
            validate_rom(bytes(64))

    def test_header_offsets_and_entry_are_checked(self):
        data = bytearray(256)
        data[:28] = b" LICENSED BY SNK CORPORATION"
        data[28:32] = (0x200040).to_bytes(4, "little")
        data[35] = 0x10
        data[36:48] = b"NGPCRAFT V3 "
        self.assertEqual(validate_rom(data)["entry"], 0x200040)
        data[28:32] = (0x4000).to_bytes(4, "little")
        with self.assertRaisesRegex(CompileError, "outside ROM"):
            validate_rom(data)

    def test_initialized_ram_and_unexpected_objects_fail(self):
        for row in ("ram far_data DATA 4000 2 f_data (game.rel)",
                    "rom far_code CODE 200040 10 f_code (unused_audio.rel)"):
            with self.subTest(row=row), self.assertRaises(CompileError):
                audit_link_map(row, [], ["game.rel"])

    def test_unrecognized_map_is_not_silently_accepted(self):
        with self.assertRaisesRegex(CompileError, "no recognized"):
            audit_link_map("new linker format", [], [])

    def test_stack_reserve_is_enforced_at_link(self):
        ok = check_stack_reserve([{"region": "ram", "address": 0x4000, "bytes": 11}])
        self.assertEqual(ok["stack_available_bytes"], 0x6C00 - 0x400B)
        self.assertEqual(ok["stack_reserved_bytes"], STACK_RESERVE_BYTES)
        # Last byte that still leaves exactly the reserve, then one byte too many.
        edge = 0x6C00 - STACK_RESERVE_BYTES - 0x4000
        check_stack_reserve([{"region": "ram", "address": 0x4000, "bytes": edge}])
        with self.assertRaisesRegex(CompileError, "reserved"):
            check_stack_reserve([{"region": "ram", "address": 0x4000, "bytes": edge + 1}])
        # ROM sections never count against the stack.
        check_stack_reserve([{"region": "rom", "address": 0x200000, "bytes": 0x100000}])


@unittest.skipUnless(TOSHIBA, "set NGC_TEST_TOSHIBA for real Toshiba link tests")
class ToshibaRomTests(unittest.TestCase):
    def test_empty_rom_has_only_required_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            export(analyze(fixture()), tmp, "rom")
            result = build_rom(tmp, TOSHIBA)
            self.assertEqual(result["objects"], ["cart.rel", "game.rel", "startup.rel"])
            self.assertNotIn("_ngc_backdrop", result["link_audit"]["public_symbols"])
            stack = result["link_audit"]["stack"]
            self.assertGreaterEqual(stack["stack_available_bytes"], STACK_RESERVE_BYTES)
            self.assertNotIn("_ngc_palette", result["link_audit"]["public_symbols"])
            self.assertTrue(result["complete_rom"])

    def test_changed_export_is_rejected_before_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            export(analyze(fixture()), tmp, "rom")
            (Path(tmp) / "ngpc.lcf").write_text("modified")
            with self.assertRaisesRegex(CompileError, "export changed"):
                build_rom(tmp, TOSHIBA)
            self.assertFalse((Path(tmp) / "objects").exists())


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS, "set Toshiba/emulator/BIOS paths for ROM execution")
class EmulatorRomTests(unittest.TestCase):
    def observe(self, p, frames):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "export"
            export(analyze(p), dest, "rom")
            build = build_rom(dest, TOSHIBA)
            out = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_rom.py"), str(dest),
                            "--emulator", EMULATOR, "--bios", BIOS, "--out", str(out),
                            "--frames", str(frames)], capture_output=True, check=True, timeout=90)
            return build, json.loads((out / "probe.json").read_text())

    def test_failed_bios_boot_retains_evidence_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "export"
            export(analyze(fixture()), dest, "rom")
            build = build_rom(dest, TOSHIBA)
            out = Path(tmp) / "probe"
            result = subprocess.run([
                sys.executable, "-B", str(ROOT / "tools/probe_rom.py"), str(dest),
                "--emulator", EMULATOR, "--bios", BIOS, "--out", str(out),
                "--boot", "bios", "--boot-budget", "1"], capture_output=True, timeout=90)
            self.assertNotEqual(result.returncode, 0)
            probe = json.loads((out / "probe.json").read_text())
            self.assertFalse(probe["game_entry_reached"])
            self.assertEqual(probe["rom_sha256"], build["header"]["sha256"])
            self.assertEqual(probe["boot_instruction_budget"], 1)
            for key in ("bios_sha256", "emulator_core_sha256", "build_report_sha256"):
                self.assertEqual(len(probe[key]), 64)
            screenshot = (out / probe["screenshot"]).read_bytes()
            self.assertTrue(screenshot.startswith(b"P6\n160 152\n255\n"))
            self.assertEqual(len(screenshot.split(b"\n", 3)[3]), 160 * 152 * 3)

    def test_colors_and_waits_on_tlcs900(self):
        p = json.loads((ROOT / "examples/rom_colors.json").read_text())
        build, probe = self.observe(p, 220)
        self.assertEqual(probe["palette_transitions"], [
            {"frame": 0, "color": 15}, {"frame": 60, "color": 240},
            {"frame": 120, "color": 3840}, {"frame": 181, "color": 4095}])
        for frame, color in [(10, 15), (70, 240), (130, 3840), (200, 4095)]:
            self.assertEqual(probe["samples"][frame]["pixel_colors"], [color])
        self.assertNotIn("_ngc_palette", build["link_audit"]["public_symbols"])

    def test_used_palette_loaded_without_backdrop_handler(self):
        p = fixture()
        p["palettes"] = {"used": [0, 15, 240, 3840], "dead": [1, 2, 3, 4]}
        p["scenes"][0]["on_init"] = [{"op": "palette", "asset": "used", "slot": 2}]
        build, probe = self.observe(p, 3)
        self.assertNotIn("_ngc_backdrop", build["link_audit"]["public_symbols"])
        self.assertEqual(probe["scr1_palette_bytes"][16:24], [0, 0, 15, 0, 240, 0, 0, 15])

    def test_actor_locals_and_transition_on_tlcs900(self):
        p = json.loads((ROOT / "examples/two_scenes.json").read_text())
        _, probe = self.observe(p, 10)
        self.assertEqual(probe["palette_transitions"], [
            {"frame": 0, "color": 15}, {"frame": 2, "color": 240},
            {"frame": 3, "color": 3840}, {"frame": 8, "color": 4095}])


if __name__ == "__main__":
    unittest.main()
