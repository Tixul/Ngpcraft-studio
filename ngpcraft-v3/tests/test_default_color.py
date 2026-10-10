"""ngpcraft.json ngpc.default_color "native" (docs/M7_NATIVE_COLOR.md): every
background and sprite sheet in its PNG colours unless its own ngpc_color says
"gbs"; ignored in a mono project."""
import json
from pathlib import Path
import tempfile
import unittest

from ngpcraft.native_color import is_native
from ngpcraft.project import SCHEMA, SIDECAR, ProjectError, load_project
from tools.demo_actor_moves import GBS2
from tools.demo_gbs_scene import derive, write_json


@unittest.skipUnless(GBS2.is_dir(), "requires published GB Studio 4.3.2")
class DefaultColorTests(unittest.TestCase):
    def project(self, tmp, ngpc, resources=lambda ids: {}, mono=False):
        project = Path(tmp) / "project"
        derive(GBS2, project, "sample_town", (6, 8), fade=False)
        if mono:
            path = project / "project/settings.gbsres"
            settings = json.loads(path.read_text(encoding="utf-8"))
            settings["colorMode"] = "mono"
            write_json(path, settings)
        loaded = load_project(project)
        ids = {"bg": loaded.scenes[0]["backgroundId"],
               "sheet": next(s["id"] for _, s in loaded.by_type["sprite"])}
        write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": ngpc, "resources": resources(ids)})
        return load_project(project), ids

    def test_default_native_with_per_resource_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, ids = self.project(tmp, {"default_color": "native"},
                                  lambda ids: {ids["sheet"]: {"ngpc_color": "gbs"}})
            self.assertTrue(is_native(p, ids["bg"]))
            self.assertFalse(is_native(p, ids["sheet"]))

    def test_without_default_only_native_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, ids = self.project(tmp, {}, lambda ids: {ids["sheet"]: {"ngpc_color": "native"}})
            self.assertFalse(is_native(p, ids["bg"]))
            self.assertTrue(is_native(p, ids["sheet"]))

    def test_mono_project_ignores_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, ids = self.project(tmp, {"default_color": "native"}, mono=True)
            self.assertFalse(is_native(p, ids["bg"]))

    def test_bad_values_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProjectError, "default_color"):
                self.project(tmp, {"default_color": "gbs"})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ProjectError, "expected \"native\" or \"gbs\""):
                self.project(tmp, {}, lambda ids: {ids["bg"]: {"ngpc_color": "auto"}})

    def test_variants_accept_the_project_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            derive(GBS2, project, "sample_town", (6, 8), fade=False)
            bg = load_project(project).scenes[0]["backgroundId"]
            image = next(p for p in (project / "assets/backgrounds").glob("*.png")).relative_to(project).as_posix()
            variants = {"ngpc_color_variants": [{"name": "v", "image": image}]}
            write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": {"default_color": "native"},
                                           "resources": {bg: variants}})
            load_project(project)
            write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": {}, "resources": {bg: variants}})
            with self.assertRaisesRegex(ProjectError, "needs native colours"):
                load_project(project)


if __name__ == "__main__":
    unittest.main()
