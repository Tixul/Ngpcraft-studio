"""A real GB Studio scene exported through the scene engine (M2)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, sprite_palettes
from ngpcraft.scene_build import build_engine_scene, engine_blocker
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import derive
from tools.gbvm_actors import engine_frames
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
GBS2 = REFERENCE / "appData/templates/gbs2"


class SceneBuildTests(unittest.TestCase):
    def test_every_gbs2_scene_translates_144_px_ones_with_bands(self):
        from ngpcraft.scene_engine import engine_files
        project = load_project(GBS2)
        banded = []
        for scene in project.scenes:
            self.assertIsNone(engine_blocker(project, scene))
            engine = build_engine_scene(project, scene)
            self.assertEqual(engine.names[0], "player")
            self.assertEqual(len(engine.actors), len(scene["actors"]) + 1)
            _, report = engine_files(engine)
            if report["view"]["top_band"]:
                self.assertEqual(report["view"], {"height": 144, "top_band": 4})
                self.assertIn("replace or adapt it to 152 px", report["warnings"][0])
                banded.append(scene["name"])
            else:
                self.assertEqual(report["warnings"], [])
        self.assertEqual(len(banded), 14)   # 14 of the 17 gbs2 scenes are one GB screen high

    def test_actor_fields_follow_gb_studio(self):
        project = load_project(GBS2)
        scene = next(s for s in project.scenes if s["name"] == "town/Sample Town")
        engine = build_engine_scene(project, scene)
        for actor, source in zip(engine.actors[1:], scene["actors"]):
            if not source["prefabId"]:
                self.assertEqual(actor["x"], source["x"] * 256)          # tileToSubpx
                self.assertEqual(actor["anim_tick"], source["animSpeed"])
                self.assertEqual(actor["move_speed"], int(source["moveSpeed"] * 32))   # pxToSubpx
        broken = json.loads(json.dumps(scene))
        broken["actors"][0]["spriteSheetId"] = "missing"
        broken["actors"][0]["prefabId"] = ""
        with self.assertRaisesRegex(CompileError, "unknown sprite sheet 'missing'"):
            build_engine_scene(project, broken)

    def test_raw_gbs2_template_requires_explicit_native_audio(self):
        # Every gbs2 event and scene type is delivered (engine_blocker is None for
        # all scenes): the raw template, without ngpcraft.json, now stops on its
        # first file sound effect, which needs an explicit native binding
        # (docs/M5_BUILTIN_SFX.md): a located refusal, no partial export.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CompileError, r"on_knockback\.gbsres:script\[0\]: .*native_sfx"):
                export_project(GBS2, Path(tmp) / "out")
            self.assertFalse((Path(tmp) / "out").exists())


def export_case(root, mutate=None, ticks=60, scene="sample_town", start=(12, 20)):
    derive(GBS2, root / "project", scene, start, fade=False)
    manifest = export_project(root / "project", root / "export")
    if mutate:
        path = root / "export/src/scene_actors.c"
        text = mutate(path.read_text(encoding="utf-8"))
        path.write_bytes(text.encode("utf-8"))
        export = json.loads((root / "export/export.json").read_text(encoding="utf-8"))
        import hashlib
        export["sha256"]["src/scene_actors.c"] = hashlib.sha256(text.encode()).hexdigest()
        (root / "export/export.json").write_text(json.dumps(export), encoding="utf-8")
    build_rom(root / "export", os.environ["NGC_TEST_TOSHIBA"])
    project = load_project(root / "project")
    scene = next(s for s in project.scenes if s["id"] == project.settings["startSceneId"])
    engine = build_engine_scene(project, scene)
    width, height, pixels = background_pixels(project, scene)
    frames = engine_frames(engine, ticks + 10)
    (root / "scene.rgb444").write_bytes(b"".join(v.to_bytes(2, "little") for v in pixels))
    (root / "source.json").write_text(json.dumps({
        "size_px": [width, height], "probe_frames": ticks, "step_from_game_time": True,
        "view": list(view_of(engine.background))[::-1],
        "expected_scroll": [list(p) for p, _ in frames], "actors": [d for _, d in frames],
        "project": str(root / "project"), "default_sprite_mode": project.settings["spriteMode"],
        "sprite_palettes": [list(p) for p in sprite_palettes(project, scene)]}))
    return manifest


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")),
                     "requires Toshiba, native emulator and BIOS")
class SceneExportRomTests(unittest.TestCase):
    def probe(self, root):
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_stream.py"), str(root),
                               "--emulator", os.environ["NGC_TEST_EMULATOR"], "--bios", os.environ["NGC_TEST_BIOS"],
                               "--out", str(root / "probe")], capture_output=True, text=True, timeout=600)
        return proc, json.loads((root / "probe/probe.json").read_text())

    def test_exported_scene_matches_the_gbvm_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = export_case(root)
            self.assertTrue(manifest["scene_engine"]["built"])
            # gbvm_scene: VM_ISLOCKED for actors_update; no script moves an actor here.
            self.assertEqual(manifest["features"], ["gbvm", "streamed_background", "sprites", "actors",
                                                    "gbvm_scene", "input", "topdown"])
            self.assertIsNone(manifest["scene_engine"]["movement"])
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(result["frames_with_mismatch"], [])
            self.assertLess(result["max_commit_lines"], result["vblank_lines"])

    def test_scene_of_144_px_is_shown_between_black_bands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = export_case(root, scene="cave", start=(8, 10))
            self.assertEqual(manifest["scene_engine"]["view"], {"height": 144, "top_band": 4})
            self.assertIn("replace or adapt it to 152 px", manifest["scene_engine"]["warnings"][0])
            proc, result = self.probe(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(result["frames_with_mismatch"], [])

    def test_probe_detects_a_camera_without_the_gbvm_offset(self):
        """Mutation witness: camera = player (no CAMERA_FIXED_OFFSET of 8 px)."""
        def no_offset(source):
            self.assertEqual(source.count("act_x[0] + 256u"), 1)
            return source.replace("act_x[0] + 256u", "act_x[0] + 0u")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_case(root, no_offset)
            proc, result = self.probe(root)
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
