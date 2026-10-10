"""`ngpcraft-project/1` (docs/FORMAT_PROJET.md): GB Studio model, closed
validation, lossless round trip, NGPC extensions outside GB Studio's files."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from ngpcraft.project import (SIDECAR, ProjectError, encode_resource, load_model, load_project,
                              write_project)
from tools.extract_gbstudio_model import MODEL, extract
from tools.reference import REFERENCE

GBS2 = REFERENCE / "appData/templates/gbs2"


def tree(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class ModelTests(unittest.TestCase):
    def test_frozen_model_matches_the_reference(self):
        self.assertEqual(json.loads(MODEL.read_text(encoding="utf-8")), extract())

    def test_model_covers_every_resource_the_loader_reads(self):
        model = load_model()
        self.assertEqual(len(model["resources"]), 19)
        self.assertEqual(model["resources"]["settings"]["mode"], "partial")
        self.assertEqual(model["defaultProjectSettings"]["spriteMode"], "8x16")


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class Gbs2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = load_project(GBS2)

    def test_official_project_loads_without_repair(self):
        p = self.project
        self.assertEqual((len(p.files), len(p.scenes)), (281, 17))
        self.assertEqual(sum(len(s["actors"]) for s in p.scenes), 81)
        self.assertEqual(sum(len(s["triggers"]) for s in p.scenes), 42)
        self.assertEqual(p.metadata["_version"], "4.2.0")
        self.assertEqual(p.diagnostics, [])

    def test_encoder_reproduces_every_gbstudio_file(self):
        for rel, data in self.project.files.items():
            self.assertEqual(encode_resource(data), (GBS2 / rel).read_text(encoding="utf-8"), rel)

    def test_round_trip_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = write_project(self.project, Path(tmp) / "copy")
            self.assertEqual(tree(out), tree(GBS2))
            again = write_project(load_project(out), Path(tmp) / "again")
            self.assertEqual(tree(again), tree(GBS2))

    def test_writer_refuses_a_non_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "keep.txt").write_text("user file")
            with self.assertRaises(ProjectError):
                write_project(self.project, tmp)
            self.assertEqual((Path(tmp) / "keep.txt").read_text(), "user file")


@unittest.skipUnless(GBS2.is_dir(), "GB Studio reference not present")
class ClosedValidationTests(unittest.TestCase):
    """Every place where GB Studio skips, repairs or drops data is an error."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "p"
        shutil.copytree(GBS2, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, rel, change):
        path = self.root / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(encode_resource(data), encoding="utf-8")

    def assert_refused(self, *fragments):
        with self.assertRaises(ProjectError) as ctx:
            load_project(self.root)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def actor(self):
        return next(p.relative_to(self.root).as_posix() for p in (self.root / "project/scenes").rglob("actors/*.gbsres"))

    def test_unknown_field_is_refused_with_its_path(self):
        rel = self.actor()
        self.edit(rel, lambda d: d.update(hp=3))
        self.assert_refused(f"{rel}.hp: unknown field")

    def test_wrong_type_is_refused_not_cast(self):
        rel = self.actor()
        self.edit(rel, lambda d: d.update(x="12"))
        self.assert_refused(f"{rel}.x: expected number")

    def test_bad_literal_is_refused(self):
        rel = self.actor()
        self.edit(rel, lambda d: d.update(direction="north"))
        self.assert_refused(f"{rel}.direction")

    def test_unknown_field_deep_in_a_script_is_refused(self):
        rel = "project/scenes/caves/cave/scene.gbsres"
        self.edit(rel, lambda d: d["script"][0]["children"]["script"][0].update(extra=1))
        self.assert_refused(".children.script[0].extra: unknown field")

    def test_legacy_event_marker_only_with_its_value(self):
        rel = "project/scenes/caves/cave/scene.gbsres"
        self.edit(rel, lambda d: d["script"][0].update(__type="other"))
        self.assert_refused(f"{rel}.script[0].__type: unknown field")

    def test_unknown_resource_type_is_refused(self):
        (self.root / "project/ngpc.gbsres").write_text('{"_resourceType": "ngpcThing"}', encoding="utf-8")
        self.assert_refused("project/ngpc.gbsres: unknown _resourceType")

    def test_actor_outside_a_scene_is_refused(self):
        rel = self.actor()
        shutil.move(self.root / rel, self.root / "project/lost_actor.gbsres")
        self.assert_refused("project/lost_actor.gbsres: actor outside a scene folder")

    def test_duplicate_id_is_refused(self):
        rel = self.actor()
        shutil.copy(self.root / rel, self.root / rel.replace(".gbsres", "_copy.gbsres"))
        self.assert_refused("already used by")

    def test_unknown_settings_key_is_a_diagnostic_and_kept(self):
        self.edit("project/settings.gbsres", lambda d: d.update(futureSetting=True))
        project = load_project(self.root)
        self.assertEqual(project.diagnostics,
                         ["project/settings.gbsres.futureSetting: ignored by GB Studio 4.3.2 at load; kept for the round trip"])
        self.assertNotIn("futureSetting", project.settings)
        with tempfile.TemporaryDirectory() as tmp:
            out = write_project(project, Path(tmp) / "o")
            self.assertIn('"futureSetting": true', (out / "project/settings.gbsres").read_text(encoding="utf-8"))

    def test_font_metadata_in_gbsres_is_a_diagnostic_and_kept(self):
        rel = next(p.relative_to(self.root).as_posix() for p in (self.root / "assets/fonts").rglob("*.gbsres"))
        self.edit(rel, lambda d: d.update(mapping={"é": 101}, table=[-1]))
        project = load_project(self.root)
        self.assertEqual(project.diagnostics,
                         [f"{rel}.mapping: ignored by GB Studio 4.3.2 (read from the font's .json); kept for the round trip",
                          f"{rel}.table: ignored by GB Studio 4.3.2 (read from the font's .json); kept for the round trip"])
        with tempfile.TemporaryDirectory() as tmp:
            out = write_project(project, Path(tmp) / "o")
            self.assertIn('"mapping"', (out / rel).read_text(encoding="utf-8"))

    def test_instance_fields_on_a_prefab_are_a_diagnostic_and_kept(self):
        # GB Studio's save writes them into a migrated prefab (SuperMarioBrosMini).
        rel = "project/prefabs/triggers/lab.gbsres"
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(encode_resource({
            "_resourceType": "triggerPrefab", "id": "lab-trigger-prefab", "name": "Lab",
            "script": [], "leaveScript": [], "_index": 0, "prefabScriptOverrides": {}}), encoding="utf-8")
        project = load_project(self.root)
        self.assertIn(f"{rel}._index: instance field on a prefab, not compiled by GB Studio 4.3.2; "
                      "kept for the round trip", project.diagnostics)
        data = json.loads((self.root / rel).read_text(encoding="utf-8"))
        data["hp"] = 1
        (self.root / rel).write_text(encode_resource(data), encoding="utf-8")
        self.assert_refused(f"{rel}.hp: unknown field")

    def test_other_unknown_font_field_is_refused(self):
        rel = next(p.relative_to(self.root).as_posix() for p in (self.root / "assets/fonts").rglob("*.gbsres"))
        self.edit(rel, lambda d: d.update(glyphs=1))
        self.assert_refused(f"{rel}.glyphs: unknown field")

    def test_invalid_settings_value_is_refused_not_dropped(self):
        self.edit("project/settings.gbsres", lambda d: d.update(spriteMode="16x16"))
        self.assert_refused("project/settings.gbsres.spriteMode")

    def test_missing_settings_take_gbstudio_defaults(self):
        self.edit("project/settings.gbsres", lambda d: d.pop("spriteMode"))
        self.assertEqual(load_project(self.root).settings["spriteMode"], "8x16")

    def test_sidecar_must_name_the_format(self):
        (self.root / SIDECAR).write_text('{"schema": "ngpcraft-project/2"}', encoding="utf-8")
        self.assert_refused("ngpcraft.json.schema")

    def test_sidecar_extensions_are_closed(self):
        (self.root / SIDECAR).write_text(json.dumps(
            {"schema": "ngpcraft-project/1", "ngpc": {"secondPlane": True}}), encoding="utf-8")
        self.assert_refused("ngpcraft.json.ngpc.secondPlane: unknown NGPC extension")

    def test_sidecar_orphan_id_is_reported(self):
        (self.root / SIDECAR).write_text(json.dumps(
            {"schema": "ngpcraft-project/1", "resources": {"no-such-id": {}}}), encoding="utf-8")
        self.assert_refused("ngpcraft.json.resources.no-such-id: no resource has this id")

    def test_empty_sidecar_is_a_plain_gbstudio_project(self):
        (self.root / SIDECAR).write_text('{"schema": "ngpcraft-project/1"}', encoding="utf-8")
        self.assertEqual(load_project(self.root).ngpc["resources"], {})


if __name__ == "__main__":
    unittest.main()
