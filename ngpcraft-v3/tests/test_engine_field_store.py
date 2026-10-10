"""EVENT_ENGINE_FIELD_STORE: bytecode against the released ScriptBuilder
(ordinary fields identical; platformer bytes widened to 16 bits read with
VM_GET_INT16, ECARTS 16), then a native ROM (tools/lab_engine_field_store.py)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_events
from ngpcraft.project import load_model
from ngpcraft.scene_platform import widened_fields
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
ARG = {'type': 'argument', 'symbol': '.SCRIPT_ARG_INDIRECT_0_VARIABLE', 'indirect': True}
FIELDS = {k: {**v, 'key': k} for k, v in load_model()['engineFields'].items()}


def cases():
    keys = ['plat_walk_vel', 'plat_jump_vel', 'fade_style', 'topdown_grid', 'plat_coyote_frames']
    return ([({}, k, '5') for k in keys] + [({'argLookup': {'variable': [['V0', ARG]], 'actor': []}}, 'plat_walk_vel', 'V0'),
                                             ({}, 'nope', '5')])


def v3(options, key, variable):
    options = {'scene': {'id': 's', 'actors': []}, **options}
    if 'argLookup' in options:
        options['argLookup'] = {k: dict(v) for k, v in options['argLookup'].items()}
    out = []
    b = ScriptBuilder(out, options)
    compile_events(b, [{'command': 'EVENT_ENGINE_FIELD_STORE', 'args': {'engineFieldKey': key, 'value': variable}}],
                   ['script[0]'], [])
    b._pack_locals()
    return out


class EngineFieldStoreTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_NODE_DEPS'), 'local TypeScript dependencies required')
    def test_released_compiler(self):
        rows = [{'options': {'scene': {'id': 's', 'actors': []}, 'engineFields': FIELDS, **o},
                 'events': [{'command': 'EVENT_ENGINE_FIELD_STORE', 'args': {'engineFieldKey': k, 'value': v}}],
                 'eventFiles': {'EVENT_ENGINE_FIELD_STORE': 'eventEngineFieldStore.js'}} for o, k, v in cases()]
        proc = subprocess.run(['node', str(ROOT / 'tools/actor_coordinates_oracle.cjs'), str(REFERENCE),
                               os.environ['NGC_TEST_NODE_DEPS']], input=json.dumps(rows),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        clean = lambda lines: [l for l in lines if l.strip() and not l.lstrip().startswith(';')]
        for (o, k, v), reference in zip(cases(), json.loads(proc.stdout)['results']):
            with self.subTest(field=k, variable=v):
                expected = clean(reference['output'])
                if k in widened_fields():
                    expected = [l.replace('VM_GET_INT8 ', 'VM_GET_INT16') for l in expected]  # ECARTS 16
                self.assertEqual(clean(v3(o, k, v)), expected)


ROM_ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


@unittest.skipUnless(all(ROM_ENV), 'requires Toshiba, native emulator and BIOS')
class EngineFieldStoreRomTests(unittest.TestCase):
    def test_written_fields_read_back(self):
        from tools.lab_engine_field_store import run
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / 'lab', *ROM_ENV)
        self.assertEqual(result['mismatches'], {})


if __name__ == '__main__':
    unittest.main()
