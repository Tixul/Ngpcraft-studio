"""Faults found by passing the 41 corpus projects through the chain (2026-10-09):
project file names, plugin events, empty start scene, built-in sound effects,
4.x resource projects saved by older versions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project, plan_project
from ngpcraft.project import ProjectError, load_project, project_file, write_project
from ngpcraft.project_sfx import default_rows
from tools.demo_actor_moves import ev
from tools.demo_gbs_scene import write_json

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gbs_fixture import make_project  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class ProjectFileTests(unittest.TestCase):
    def test_any_single_gbsproj_name_opens_and_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp) / 'p', [])
            (p / 'project.gbsproj').rename(p / 'My Game.gbsproj')
            self.assertEqual(project_file(p)[1].name, 'My Game.gbsproj')
            self.assertEqual(project_file(p / 'My Game.gbsproj')[0], p)
            project = load_project(p)
            out = write_project(project, Path(tmp) / "copy")
            self.assertTrue((out / 'My Game.gbsproj').is_file())
            self.assertFalse((out / 'project.gbsproj').exists())
            shutil.copyfile(p / 'My Game.gbsproj', p / 'Other.gbsproj')
            with self.assertRaisesRegex(ProjectError, 'several project files'):
                load_project(p)

    def test_empty_start_scene_starts_from_the_first_scene_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp) / 'p', [])
            path = next((p / 'project').rglob('settings.gbsres'))
            settings = json.loads(path.read_text(encoding='utf8'))
            settings['startSceneId'] = ''
            write_json(path, settings)
            project = load_project(p)
            self.assertEqual(project.settings['startSceneId'], project.scenes[0]['id'])
            self.assertTrue(any('startSceneId' in d for d in project.diagnostics))


class PluginEventTests(unittest.TestCase):
    def test_a_plugin_event_is_refused_by_name_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp) / 'p', [ev('x', 'PT_EVENT_SOMETHING', value=1)])
            self.assertEqual(plan_project(p)['events']['PT_EVENT_SOMETHING']['status'], 'plugin_ou_inconnu')
            # No plugin of the project defines it (docs/M7_PLUGINS.md).
            with self.assertRaisesRegex(CompileError, "PT_EVENT_SOMETHING .*neither a GB Studio 4.3.2 event nor an event of the project's plugins"):
                export_project(p, Path(tmp) / 'out')


class BuiltinSfxTests(unittest.TestCase):
    def test_tone_beep_and_crash_follow_the_game_boy_data(self):
        tone = default_rows({'type': 'tone', 'frequency': 200, 'duration': 0.5})
        self.assertEqual(tone, [{'frames': 30, 'tone_divider': 480, 'tone_attenuation': 0,
                                 'noise': 0, 'noise_attenuation': 15}])
        # Beep pitches: 7-bit LFSR buzz at f_shift / 127. Pitches 1 and 2 (74 and
        # 86 Hz) are below the K2GE tone floor (96000 / 1023 = 94 Hz): both 1023.
        dividers = [default_rows({'type': 'beep', 'pitch': p})[0]['tone_divider'] for p in range(1, 9)]
        self.assertEqual(dividers[:2], [1023, 1023])
        self.assertEqual(len(set(dividers[1:])), 7)
        beep = default_rows({'type': 'beep', 'pitch': 4, 'duration': 1})
        self.assertEqual(sum(r['frames'] for r in beep), 60)
        audible = sum(r['frames'] for r in beep if r['tone_attenuation'] < 15)
        self.assertEqual(audible, 15)                       # NR41 length: 63/256 s
        crash = default_rows({'type': 'crash'})
        self.assertTrue(all(r['noise'] == 4 and r['tone_attenuation'] == 15 for r in crash))
        self.assertEqual([r['noise_attenuation'] for r in crash][:3], [0, 1, 2])
        self.assertIsNone(default_rows({'type': 'some-sound-asset-id'}))

    def test_explicit_binding_wins_and_default_is_warned(self):
        from ngpcraft.gbvm.builder import ScriptBuilder
        from ngpcraft.project_sfx import compile_event
        explicit = [{'frames': 3, 'tone_divider': 9, 'tone_attenuation': 2, 'noise': 0, 'noise_attenuation': 15}]
        b = ScriptBuilder([], {'nativeSfx': {'tone:200:0.5': explicit}})
        compile_event({'type': 'tone', 'frequency': 200, 'duration': 0.5}, b)
        compile_event({'type': 'crash', 'duration': 0.5}, b)
        self.assertIs(b.options['nativeSfx']['tone:200:0.5'], explicit)
        self.assertIn('crash:0.5', b.options['nativeSfx'])
        self.assertEqual(len(b.options['warnings']), 1)


@unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'pinned migration dependencies required')
class ResourceMigrationTests(unittest.TestCase):
    def test_an_older_4x_save_loads_after_the_published_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_project(Path(tmp) / 'src', [])
            shutil.rmtree(p / 'plugins', ignore_errors=True)   # plugins are refused by the core tool
            scene = next((p / 'project/scenes').rglob('scene.gbsres'))
            data = json.loads(scene.read_text(encoding='utf8'))
            del data['colorModeOverride']                    # absent from 4.1 saves
            write_json(scene, data)
            font = next((p / 'assets/fonts').glob('*.gbsres'))
            fdata = json.loads(font.read_text(encoding='utf8'))
            fdata['mapping'] = {}                            # kept by 4.1 saves
            write_json(font, fdata)
            with self.assertRaises(ProjectError):
                load_project(p)
            out = Path(tmp) / 'migrated'
            proc = subprocess.run([sys.executable, '-B', '-m', 'tools.migrate_gbstudio', str(p / 'project.gbsproj'),
                                   str(out), '--dependencies', os.environ['NGC_TEST_NODE_DEPS']],
                                  cwd=ROOT, capture_output=True, text=True, timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            load_project(out)
            report = json.loads((out / 'migration-report.json').read_text(encoding='utf8'))
            self.assertEqual(report['source_kind'], 'resources')
            self.assertTrue(any(h.get('field') == 'mapping' for h in report['asset_hydration']))


if __name__ == '__main__':
    unittest.main()
