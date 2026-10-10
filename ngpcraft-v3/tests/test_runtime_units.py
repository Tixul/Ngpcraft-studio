import hashlib
from dataclasses import replace
from unittest.mock import patch
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest

from ngpcraft.compiler import CompileError, analyze
from ngpcraft.emitter import export, generate
from ngpcraft.runtime_units import UNITS, select_units
from ngpcraft.toolchain import audit_link_map, build_rom, compile_objects


def project(features):
    events = {"backdrop": {"op": "backdrop", "value": 15},
              "palette": {"op": "palette", "asset": "p", "slot": 0}}
    return {"schema": "ngpcraft-ir/0.1", "start": "room",
            "palettes": {"p": [0, 15, 240, 3840]},
            "scenes": [{"id": "room", "on_init": [events[f] for f in features]}]}


class RuntimeUnitTests(unittest.TestCase):
    def test_migration_preserves_all_example_export_payloads(self):
        root = Path(__file__).resolve().parents[1]
        baseline = json.loads((root / "tests/runtime_migration_hashes.json").read_text())
        for case, expected in baseline.items():
            example, target = case.split(":")
            p = json.loads((root / "examples" / example).read_text())
            files = generate(analyze(p), target)
            actual = {name: hashlib.sha256(data.encode()).hexdigest()
                      for name, data in files.items() if name != "export.json"}
            with self.subTest(case=case):
                self.assertEqual(actual, expected)

    def test_unit_evidence_references_exist_and_export_is_self_describing(self):
        root = Path(__file__).resolve().parents[1]
        for unit in UNITS:
            self.assertTrue(unit.provenance)
            self.assertTrue(unit.license)
            for test in unit.tests:
                self.assertTrue((root / test.split(":")[0]).is_file(), test)
        manifest = json.loads(generate(analyze(project(["backdrop", "palette"])), "rom")["export.json"])
        self.assertEqual(len(manifest["runtime_units"]), len([u for u in UNITS if u.backend == "ir"]))
        for unit in manifest["runtime_units"]:
            self.assertTrue(unit["tests"])
            self.assertTrue(unit["provenance"])
            self.assertTrue(unit["license"])
            for file in unit["files"]:
                self.assertIn(file, manifest["sha256"])

    def test_invalid_unit_registry_fails_closed(self):
        bad_registries = (
            UNITS + (UNITS[0],),
            (replace(UNITS[0], provenance=()),) + UNITS[1:],
            (replace(UNITS[0], dependencies=("missing",)),) + UNITS[1:],
            (replace(UNITS[0], dependencies=("cartridge",)),) + UNITS[1:],
            (replace(UNITS[0], files=(("cart.c", "src/cart.c"),)),) + UNITS[1:],
        )
        for registry in bad_registries:
            with self.subTest(registry=registry), patch("ngpcraft.runtime_units.UNITS", registry):
                with self.assertRaises(CompileError):
                    select_units([], "rom")

    def test_dependencies_and_target_boundary(self):
        self.assertEqual([u.name for u in select_units([], "module")], ["game"])
        self.assertEqual([u.name for u in select_units([], "rom")], ["game", "startup", "cartridge"])
        with self.assertRaises(CompileError):
            select_units([], "unknown")

    def test_all_hardware_subsets_export_only_selected_routines(self):
        # IR hardware units; the gbvm unit is covered by tests/test_gbvm_backend.py.
        optional = [u for u in UNITS if u.feature and u.backend == "ir"]
        for bits in itertools.product((False, True), repeat=len(optional)):
            features = [u.feature for u, enabled in zip(optional, bits) if enabled]
            files = generate(analyze(project(features)), "rom")
            manifest = json.loads(files["export.json"])
            selected = {u["id"] for u in manifest["runtime_units"]}
            c = "\n".join(value for name, value in files.items() if name.endswith(".c"))
            for unit, enabled in zip(optional, bits):
                self.assertEqual(unit.name in selected, enabled)
                for symbol in unit.symbols:
                    self.assertEqual(symbol[1:] in c, enabled)

    @unittest.skipUnless(os.environ.get("NGC_TEST_TOSHIBA"), "requires Toshiba")
    def test_real_link_all_subsets_and_unselected_symbol_injection(self):
        optional = [u for u in UNITS if u.feature and u.backend == "ir"]
        for bits in itertools.product((False, True), repeat=len(optional)):
            features = [u.feature for u, enabled in zip(optional, bits) if enabled]
            with self.subTest(features=features), tempfile.TemporaryDirectory() as tmp:
                export(analyze(project(features)), tmp, "rom")
                result = build_rom(tmp, os.environ["NGC_TEST_TOSHIBA"])
                actual = result["link_audit"]["public_symbols"]
                link_map = (Path(tmp) / "rom/game.map").read_text()
                for unit, enabled in zip(optional, bits):
                    for symbol in unit.symbols:
                        self.assertEqual(symbol in actual, enabled)
                        if not enabled:
                            poisoned = link_map + "\n" + symbol + " 200040 f_code\n"
                            with self.assertRaisesRegex(CompileError, "public symbols"):
                                audit_link_map(poisoned, features, result["objects"])
                extra_object_map = "rom far_code CODE 200040 1 f_code (unused.rel)\n" + link_map
                with self.assertRaisesRegex(CompileError, "object list"):
                    audit_link_map(extra_object_map, features, result["objects"] + ["unused.rel"])
                if not features:
                    self.assertEqual(result["header"]["sha256"],
                                     "65f62f498294960cad19c93392cb1bc17549dc8c7777ce45bbc6f0a33cbc23ec")


class ExportSelectionTests(unittest.TestCase):
    def test_unselected_source_rejected_before_compiler_even_with_valid_hash(self):
        for target in ("module", "rom"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                manifest = export(analyze(project([])), tmp, target)
                extra = Path(tmp) / "src/unused.c"
                extra.write_text("static void unused(void) {}\n", encoding="utf-8")
                manifest["sources"].append("src/unused.c")
                manifest["sources"].sort()
                manifest["sha256"]["src/unused.c"] = hashlib.sha256(extra.read_bytes()).hexdigest()
                (Path(tmp) / "export.json").write_text(json.dumps(manifest), encoding="utf-8")
                with patch("ngpcraft.toolchain.locate", return_value={"ready": True}), patch("ngpcraft.toolchain.subprocess.run") as compiler:
                    with self.assertRaisesRegex(CompileError, "sources do not match"):
                        compile_objects(tmp)
                    compiler.assert_not_called()
                self.assertFalse((Path(tmp) / "objects").exists())

    def test_missing_repeated_and_falsely_described_units_rejected(self):
        from ngpcraft.runtime_units import validate_export_selection
        from copy import deepcopy
        original = json.loads(generate(analyze(project(["palette"])), "rom")["export.json"])
        bad = []
        for source_list in (original["sources"][:-1], original["sources"] + [original["sources"][0]]):
            m = deepcopy(original)
            m["sources"] = source_list
            bad.append(m)
        m = deepcopy(original)
        m["runtime_units"][-1]["public_symbols"] = ["_unused"]
        bad.append(m)
        m = deepcopy(original)
        m["target"] = "unknown"
        bad.append(m)
        for m in bad:
            with self.subTest(manifest=m), self.assertRaises(CompileError):
                validate_export_selection(m)

    def test_legacy_manifest_without_unit_metadata_keeps_source_checks(self):
        from ngpcraft.runtime_units import validate_export_selection
        for target in ("module", "rom"):
            manifest = json.loads(generate(analyze(project([])), target)["export.json"])
            del manifest["runtime_units"]
            validate_export_selection(manifest)
            manifest["sources"].append("src/unused.c")
            with self.assertRaisesRegex(CompileError, "sources do not match"):
                validate_export_selection(manifest)
