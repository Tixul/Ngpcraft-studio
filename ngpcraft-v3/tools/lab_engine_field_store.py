"""EVENT_ENGINE_FIELD_STORE in a native ROM (platformer scene of
tools/platform_oracle.project_case): engine fields written by Engine Field Set,
then read back into variables. plat_walk_vel and plat_min_vel are WORD;
plat_coyote_frames and plat_air_control are bytes widened to 16 bits on the
NGPC (ECARTS 16), read with VM_GET_INT16."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_event_gaps import set_value
from tools.platform_oracle import project_case as platform_project
from tools.probe_rom import power_on_ram, verified_tick

# (field, value written, variable read into)
FIELDS = (("plat_walk_vel", 3000, 80), ("plat_min_vel", -5, 81), ("plat_coyote_frames", 7, 82),
          ("plat_air_control", 1, 83))


def project_case(root):
    project = platform_project(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    script = []
    for field, value, var in FIELDS:
        script += [ev(f'set-{field}', 'EVENT_ENGINE_FIELD_SET', engineFieldKey=field, value=num(value)),
                   ev(f'store-{field}', 'EVENT_ENGINE_FIELD_STORE', engineFieldKey=field, value=str(var))]
    scene['script'] = script + [set_value(44, 1)]
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out), out / 'export')
    build = build_rom(out / 'export', toolchain)
    rom = (out / 'export/rom/game.ngc').read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, 'noise')
    m.set_breakpoints([verified_tick(rom, build)])
    base = build['link_audit']['public_symbols']['_script_memory']['address']
    read = lambda i: int.from_bytes(m.read(base + 2 * manifest['variables'][f'VAR_VARIABLE_{i}'], 2), 'little')
    for _ in range(300):
        stop, _ = m.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        if read(44) == 1:
            break
    else:
        raise ValueError('engine field lab did not finish')
    expected = {var: value & 0xFFFF for _, value, var in FIELDS}
    actual = {var: read(var) for var in expected}
    report = {'schema': 'ngpcraft-engine-field-store-lab/1', 'expected': expected, 'actual': actual,
              'mismatches': {i: [v, actual[i]] for i, v in expected.items() if v != actual[i]},
              'rom_sha256': hashlib.sha256(rom).hexdigest(), 'silicon_timing': [10, 8], 'ram': 'noise',
              'console_validated': False}
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios)
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result['mismatches'] else 0)
