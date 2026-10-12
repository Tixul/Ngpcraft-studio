"""Text tiles reserved per scene (M2_TEXT.md D1, BILAN_MEGAX 2.1).

Only a scene whose scripts (and the scripts they call) use the UI keeps tiles
384..511 for the text; the UI stays off in the others (no tiles, palettes,
window update or SCR1 scroll written). When every scene has text, the export
is the one of before (no slot table)."""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_scene_switch import CAVE, TOWN, TOWN_ARRIVAL, ev, num, set_script, switch
from tools.demo_text_switch import project_case as switch_project

TOOLCHAIN = os.environ.get("NGC_TEST_TOSHIBA")


def cave_without_text(root):
    project, ids = switch_project(root)
    folder = next(p.parent for p in (project / "project/scenes").rglob("scene.gbsres") if p.parent.name == CAVE)
    set_script(folder,
               [ev("cave-pause", "EVENT_WAIT", units="frames", frames=num(40)),
                switch("cave-switch", ids[TOWN], TOWN_ARRIVAL, "0")])
    return project


def scene_reports(manifest):
    return {r["id"]: r for r in manifest["scene_engine"]["scenes"]}


class PerSceneTextTests(unittest.TestCase):
    def test_every_scene_with_text_keeps_the_previous_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = switch_project(root)
            manifest = export_project(project, root / "export")
            gbvm = (root / "export/src/gbvm.c").read_text()
            self.assertNotIn("ngc_ui_slot", gbvm)
            self.assertNotIn("ngc_ui_live", gbvm)
            self.assertTrue(all("ui_palette_base" in r for r in manifest["scene_engine"]["scenes"]))

    def test_a_scene_without_text_has_no_reserve_and_no_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = cave_without_text(root)
            manifest = export_project(project, root / "export")
            reports = manifest["scene_engine"]["scenes"]
            names = {r["index"]: r["scene"] for r in reports}
            with_text = {r["index"] for r in reports if "ui_palette_base" in r}
            self.assertEqual(len(reports), 2)
            self.assertEqual(len(with_text), 1)
            gbvm = (root / "export/src/gbvm.c").read_text()
            table = re.search(r"ngc_ui_slot\[\d*\]\s*=\s*\{([^}]*)\}", gbvm)
            self.assertIsNotNone(table, "slot table missing")
            slots = [int(v.strip().rstrip("u"), 0) for v in table.group(1).split(",") if v.strip()]
            self.assertEqual([k for k, s in enumerate(slots) if s != 0xFF], sorted(with_text), names)
            self.assertEqual(sorted(s for s in slots if s != 0xFF), [0])
            # The UI is switched off on load, and its frame work is skipped while off.
            self.assertIn("ngc_ui_live = 0u;", gbvm)
            self.assertEqual(gbvm.count("if (!ngc_ui_live) return;"), 2)
            if TOOLCHAIN:
                report = build_rom(root / "export", Path(TOOLCHAIN))
                self.assertGreater(report["header"]["size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
