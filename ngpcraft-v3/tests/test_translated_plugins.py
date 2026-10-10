"""Translated engine plugins (docs/M8_PLUGINS_TRADUITS.md): recognised by their
files, refused when modified or unknown, a translation's conditions refused
where the project breaks them, and NO effect on a project without plugins (its
export is the same byte for byte with or without the translations registry).

The plugins come from the corpus project DungeonTrials (its own copies, read
from the zip; Platformer+ = becomingplural/GBS_PlatformerPlus@eb7488b)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from ngpcraft import translated
from tools import locations
from ngpcraft.compiler import CompileError

ROOT = Path(__file__).resolve().parents[1]
CORPUS = locations.CORPUS / "DungeonTrials_25.10.20.zip"
PP = "DungeonTrials/plugins/PlatformerPlus/"
APT = "DungeonTrials/plugins/Actors Pass Through Actors (4.1.2)/"


def extract(prefix, dest, engine_only=True):
    """The plugin folder `prefix` of the corpus zip into dest (its engine/ only by default)."""
    with zipfile.ZipFile(CORPUS) as z:
        for name in z.namelist():
            if name.startswith(prefix) and not name.endswith("/"):
                rel = name[len(prefix):]
                if engine_only and not rel.startswith("engine/"):
                    continue
                target = Path(dest) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
    return Path(dest)


def platform_project(tmp):
    from tools.platform_oracle import project_case
    return project_case(Path(tmp))


@unittest.skipUnless(CORPUS.is_file(), "corpus project DungeonTrials required")
class RecognitionTests(unittest.TestCase):
    def test_tools_output_is_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            pp = extract(PP, Path(tmp) / "pp", engine_only=False)
            apt = extract(APT, Path(tmp) / "apt", engine_only=False)
            for tool, plugin in (("tools.translate_platformer_plus", pp), ("tools.translate_actors_pass_through", apt)):
                with self.subTest(tool=tool):
                    run = subprocess.run([sys.executable, "-B", "-m", tool, "check", "--plugin", str(plugin)],
                                         cwd=ROOT, capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_recognised_by_files_not_by_folder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extract(PP, root / "plugins" / "AnyName")
            extract(APT, root / "plugins" / "Other")
            found = translated.recognize(root, ["AnyName", "Other"])
            self.assertEqual(sorted(found), ["actors_pass_through", "platformer_plus"])
            self.assertEqual(found["platformer_plus"]["folder"], "AnyName")

    def test_line_endings_are_not_a_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = extract(PP, Path(tmp) / "plugins" / "PlatformerPlus")
            path = plugin / "engine/src/states/platform.c"
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            self.assertIn("platformer_plus", translated.recognize(Path(tmp), ["PlatformerPlus"]))

    def test_modified_or_unknown_plugin_refused_with_its_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = extract(PP, root / "plugins" / "PlatformerPlus")
            with open(plugin / "engine/src/states/platform.c", "a", encoding="utf-8") as f:
                f.write("\n/* local change */\n")
            (plugin / "engine/src/extra.c").write_text("", encoding="utf-8")
            (root / "plugins/Mystery/engine").mkdir(parents=True)
            (root / "plugins/Mystery/engine/engine.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(CompileError) as caught:
                translated.recognize(root, ["Mystery", "PlatformerPlus"])
            message = str(caught.exception)
            self.assertIn("plugins/PlatformerPlus: differs from the translated PlatformerPlus", message)
            self.assertIn("engine/src/states/platform.c (modified)", message)
            self.assertIn("engine/src/extra.c (not in the translated plugin)", message)
            self.assertIn("plugins/Mystery: engine plugin not translated", message)

    def test_same_plugin_twice_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extract(PP, root / "plugins" / "A")
            extract(PP, root / "plugins" / "B")
            with self.assertRaisesRegex(CompileError, "plugins/B: same plugin as plugins/A"):
                translated.recognize(root, ["A", "B"])


@unittest.skipUnless(CORPUS.is_file(), "corpus project DungeonTrials required")
class ExportTests(unittest.TestCase):
    def export(self, root, out):
        from ngpcraft.pipeline import export_project
        return export_project(root, out)

    def test_platformer_plus_replaces_the_platformer_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = platform_project(Path(tmp) / "p")
            extract(PP, project / "plugins" / "PlatformerPlus")
            manifest = self.export(project, Path(tmp) / "out")
            self.assertIn("plugin:platformer_plus", manifest["features"])
            unit = next(u for u in manifest["runtime_units"] if u["id"] == "scene_platform")
            self.assertIn("becomingplural/GBS_PlatformerPlus@eb7488b", " ".join(unit["provenance"]))
            source = (Path(tmp) / "out/src/scene_actors.c").read_text(encoding="utf-8")
            self.assertIn("void ngc_platform_update(void) { pp_sync_in(); platform_update(); pp_sync_out(); }", source)
            self.assertIn("pp_overlap_player", source)
            self.assertNotIn("plat_coyote_frames", source)          # GB Studio's platform.c is gone

    def test_actors_pass_requires_zero_and_no_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = platform_project(Path(tmp) / "p")
            extract(APT, project / "plugins" / "Actors Pass Through Actors (4.1.2)")
            with_plugin = self.export(project, Path(tmp) / "a")
            values = project / "project/engine_field_values.gbsres"
            data = json.loads(values.read_text(encoding="utf-8"))
            data["engineFieldValues"].append({"id": "actors_pass", "value": 1})
            values.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(CompileError, "engine field actors_pass = 1: actors passing through"):
                self.export(project, Path(tmp) / "b")
            data["engineFieldValues"].pop()
            values.write_text(json.dumps(data), encoding="utf-8")
            scene_path = next((project / "project/scenes").rglob("scene.gbsres"))
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
            scene["script"] = [{"command": "EVENT_ENGINE_FIELD_SET", "id": "e1",
                                "args": {"engineFieldKey": "actors_pass", "value": {"type": "number", "value": 1}}}]
            scene_path.write_text(json.dumps(scene), encoding="utf-8")
            with self.assertRaisesRegex(CompileError, "EVENT_ENGINE_FIELD_SET writes actors_pass"):
                self.export(project, Path(tmp) / "c")
            self.assertNotIn("plugin:actors_pass_through", with_plugin["features"])   # no code: actors_pass = 0

    def test_no_effect_without_plugins(self):
        """Same export, file by file, with the translations registry emptied (fresh process:
        the engine memory table is built at import)."""
        with tempfile.TemporaryDirectory() as tmp:
            project = platform_project(Path(tmp) / "p")
            outputs = []
            for empty in (False, True):
                out = Path(tmp) / f"out{int(empty)}"
                code = ("import sys; from pathlib import Path; import ngpcraft.translated as t; "
                        + (f"t.TRANSLATED = Path({str(Path(tmp) / 'none')!r}); " if empty else "")
                        + "from ngpcraft.pipeline import export_project; export_project(sys.argv[1], sys.argv[2])")
                run = subprocess.run([sys.executable, "-B", "-c", code, str(project), str(out)],
                                     cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                outputs.append({p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()})
            self.assertEqual(sorted(outputs[0]), sorted(outputs[1]))
            for name in outputs[0]:
                self.assertEqual(outputs[0][name], outputs[1][name], name)


class LinkerTests(unittest.TestCase):
    """scene_link.py cases met by DungeonTrials (Adventure + Platformer in one ROM)."""

    def test_same_name_types_get_a_variant_per_scene_engine(self):
        from ngpcraft.scene_link import link_file
        a = "typedef enum\n{\n    GROUND_STATE = 0,\n    DASH_STATE\n} state_e;\nstate_e adv_state;\nvoid ngc_x(void)\n{\n    adv_state = GROUND_STATE;\n}\n"
        b = "typedef enum\n{\n    FALL_STATE = 0,\n    GROUND_STATE\n} state_e;\nstate_e plat_state;\nvoid ngc_x(void)\n{\n    plat_state = GROUND_STATE;\n}\n"
        text, _ = link_file("x.c", [a, b])
        self.assertIn("} state_e__v1;", text)
        self.assertIn("} state_e__v2;", text)
        self.assertIn("state_e__v1 adv_state;", text)
        self.assertIn("plat_state = GROUND_STATE__v2;", text)
        self.assertIn("adv_state = GROUND_STATE__v1;", text)

    def test_boot_runs_whatever_the_first_scene(self):
        from ngpcraft.scene_link import link_file
        boot = "short v;\nvoid ngc_p_boot(void)\n{\n    v = 5;\n}\n"
        other = "short w;\nvoid ngc_q(void)\n{\n    w = 1;\n}\n"
        text, _ = link_file("x.c", [other, boot, boot])
        self.assertIn("void ngc_p_boot(void)\n{\n    s1_ngc_p_boot();", text)
        with self.assertRaisesRegex(CompileError, "ngc_p_boot differs between scenes"):
            link_file("x.c", [boot, boot.replace("v = 5", "v = 6")])

    def test_pointer_variables(self):
        from ngpcraft.scene_link import link_file
        a = "short *edge;\nvoid ngc_x(void)\n{\n    edge = 0;\n}\n"
        text, report = link_file("x.c", [a, a])
        self.assertIn("short * edge;", text)
        self.assertEqual(report["ram_bytes"], 4)


@unittest.skipUnless(os.environ.get("NGC_TEST_TOSHIBA"), "requires the Toshiba toolchain (NGC_TEST_TOSHIBA)")
class ScriptMemoryTests(unittest.TestCase):
    def test_cc900_sizes_script_memory_as_gbvm(self):
        """cc900 folds 164 + (25 * 64) to 228 (measured 2026-10-10): the emitted declaration,
        compiled by cc900, has VM_HEAP_SIZE + VM_MAX_CONTEXTS * VM_CONTEXT_STACK_SIZE words."""
        from ngpcraft.gbvm import emit
        source = Path(emit.__file__).read_text(encoding="utf-8")
        start = source.index('"UWORD script_memory[')
        line = source[start:source.index("];", start) + 3]
        declaration = "".join(part.strip().strip('"') for part in line.split('"\n'))
        toolchain = Path(os.environ["NGC_TEST_TOSHIBA"])
        env = dict(os.environ, THOME=str(toolchain), PATH=str(toolchain / "bin") + os.pathsep + os.environ["PATH"])
        for heap, contexts, stride in ((164, 25, 64), (164, 5, 16), (164, 1, 16), (8, 2, 120)):
            with self.subTest(contexts=contexts, stride=stride), tempfile.TemporaryDirectory() as tmp:
                c = Path(tmp) / "m.c"
                c.write_text(f"#define UWORD unsigned short\n#define VM_HEAP_SIZE {heap}\n#define VM_MAX_CONTEXTS "
                             f"{contexts}\n#define VM_CONTEXT_STACK_SIZE {stride}\n{declaration}\n",
                             encoding="utf-8")
                subprocess.run(["cc900", "-S", "m.c"], cwd=tmp, env=env, capture_output=True, check=True)
                asm = (Path(tmp) / "m.asm").read_text(encoding="latin-1")
                self.assertIn(f"dsb {2 * (heap + contexts * stride)}", asm)


if __name__ == "__main__":
    unittest.main()
