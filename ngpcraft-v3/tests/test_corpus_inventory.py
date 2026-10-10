import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.inventory_gbstudio import corpus, inventory


class CorpusInventoryTests(unittest.TestCase):
    def test_legacy_duplicates_comments_and_previews(self):
        data = {"_version": "2.0.0", "scenes": [{"script": [
            {"command": "EVENT_WEAPON_ATTACK"},   # 2.0 event, not delivered (EVENT_IF_TRUE is, 2026-10-09)
            {"command": "COMMENTED", "args": {"__comment": True}, "children": {"true": [{"command": "HIDDEN"}]}},
            {"command": "EVENT_CALL_CUSTOM_EVENT", "children": {"script": [{"command": "PREVIEW"}]}}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a.zip", "b.zip"):
                with zipfile.ZipFile(root / name, "w") as archive:
                    archive.writestr("game.gbsproj", json.dumps(data))
                    archive.writestr("build/ignored.gbsres", "not json")
            result = corpus(root)
        self.assertEqual(result["modern_priorities"], {})
        self.assertFalse(result["sources"][0]["duplicate_project_source"])
        self.assertTrue(result["sources"][1]["duplicate_project_source"])
        row = result["sources"][0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["legacy_migration_candidates"], {"EVENT_WEAPON_ATTACK": 1})
        self.assertNotIn("PREVIEW", row["events_raw"])

    def test_modern_unimplemented_and_bad_resource_are_localized(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "modern.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("game.gbsproj", json.dumps({"_resourceType": "project", "_version": "4.1.0"}))
                archive.writestr("project/scene.gbsres", json.dumps({"_resourceType": "scene", "script": [{"command": "EVENT_PRINT"}]}))
                archive.writestr("project/bad.gbsres", "invalid")
                archive.writestr("plugins/example/events/foo.js", "not executed")
            row = inventory(source)
        self.assertEqual(row["modern_unimplemented"], {"EVENT_PRINT": 1})
        self.assertEqual(row["errors"][0]["path"], "project/bad.gbsres")
        self.assertEqual(len(row["plugins"]), 1)
