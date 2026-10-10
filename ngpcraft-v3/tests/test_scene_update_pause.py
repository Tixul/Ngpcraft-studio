"""Pause / Resume Scene Type Update: bytecode against the released
ScriptBuilder, then a native ROM where the held player stops while paused
(tools/lab_scene_update_pause.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
FILES = {'EVENT_SCENE_UPDATE_PAUSE': 'eventSceneUpdatePause.js', 'EVENT_SCENE_UPDATE_RESUME': 'eventSceneUpdateResume.js'}
EVENTS = [{'command': 'EVENT_SCENE_UPDATE_PAUSE', 'args': {}}, {'command': 'EVENT_SCENE_UPDATE_RESUME', 'args': {}}]


class SceneUpdatePauseTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': {'scene': {'id': 's', 'actors': []}}, 'events': EVENTS, 'eventFiles': FILES}]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = []
        b = ScriptBuilder(out, {'scene': {'id': 's', 'actors': []}})
        compile_events(b, EVENTS, ['script[0]', 'script[1]'], [])
        clean = lambda lines: [l for l in lines if l.strip() and not l.lstrip().startswith(';')]
        self.assertEqual(clean(out), clean(json.loads(proc.stdout)['results'][0]['output']))


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class SceneUpdatePauseRomTests(unittest.TestCase):
    def test_player_frozen_while_paused(self):
        from tools.lab_scene_update_pause import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['walked_px']['phase2_paused'], 0)

    def test_witness_keeps_walking(self):
        from tools.lab_scene_update_pause import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV, witness=True)
        self.assertGreater(result['walked_px']['phase2_paused'], 0)
        self.assertNotIn('scene_update_pause', result['features'])


if __name__ == '__main__':
    unittest.main()
