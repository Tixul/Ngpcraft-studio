"""`preview-music`: a ROM that only plays one song, from a copy of the project
(ngpcraft/music_preview.py); the PSG plays when the toolchain is there."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from ngpcraft.compiler import CompileError
from ngpcraft.music_preview import SCENE_ID, build_preview, preview_project
from ngpcraft.project import load_project
from tools import locations

TEMPLATE = locations.STUDIO / "appData/templates/gbs2"
ROM_ENV = [os.environ.get(k) for k in ("NGC_TEST_TOSHIBA", "NGC_TEST_EMULATOR", "NGC_TEST_BIOS")]


def bound_song():
    data = json.loads((TEMPLATE / "ngpcraft.json").read_text(encoding="utf-8"))
    return next(k for k, v in data["resources"].items() if "native_music" in v)


def tree(root):
    return {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}


@unittest.skipUnless((TEMPLATE / "ngpcraft.json").is_file(), "editor gbs2 template absent")
class MusicPreviewTests(unittest.TestCase):
    def test_copy_starts_on_the_preview_scene_source_untouched(self):
        before = tree(TEMPLATE)
        with tempfile.TemporaryDirectory() as tmp:
            dest = preview_project(TEMPLATE, bound_song(), Path(tmp) / "p")
            project = load_project(dest)
            self.assertEqual(project.settings["startSceneId"], SCENE_ID)
            scene = next(s for s in project.scenes if s["id"] == SCENE_ID)
            self.assertEqual(scene["script"][-1]["args"]["musicId"], bound_song())
            # Only the preview scene: the project's scenes would all be compiled.
            self.assertEqual([s["id"] for s in project.scenes], [SCENE_ID])
        self.assertEqual(tree(TEMPLATE), before)

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CompileError, "no music resource"):
                preview_project(TEMPLATE, "nope", Path(tmp) / "a")
            unbound = next(res["id"] for _, res in load_project(TEMPLATE).by_type["music"]
                           if "native_music" not in load_project(TEMPLATE).ngpc["resources"].get(res["id"], {}))
            with self.assertRaisesRegex(CompileError, "no NGPC version"):
                preview_project(TEMPLATE, unbound, Path(tmp) / "b")

    @unittest.skipUnless(all(ROM_ENV), "requires Toshiba, native emulator and BIOS")
    def test_the_rom_plays(self):
        from ngpcraft.toolchain import build_rom
        with tempfile.TemporaryDirectory() as tmp:
            build_preview(TEMPLATE, bound_song(), Path(tmp) / "out")
            build_rom(Path(tmp) / "out/export", Path(ROM_ENV[0]))
            rom = (Path(tmp) / "out/export/rom/game.ngc").read_bytes()
        sys.path.append(str(Path(ROM_ENV[1]).resolve()))
        from core import native
        m = native.NativeMachine(rom, bios=Path(ROM_ENV[2]).read_bytes())
        m.set_timing_silicon(word_wait=10, bios_wait=8)
        m.reset(bios_handoff=True)
        audible = 0
        for _ in range(300):
            m.run_frames(1)
            a = m.apu_state()
            audible += bool(any(a.square_vol_left) or a.noise_vol_left)
        self.assertGreater(audible, 250)


if __name__ == "__main__":
    unittest.main()
