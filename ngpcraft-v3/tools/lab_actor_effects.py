"""Actor Effects (split in, flicker, split out) in a native ROM, frame by frame.

Arena of tools/lab_actor_push; push-actor (engine actor 1). The scene script
(unlocked) marks V20 then runs: Split In 16 px at speed 2, Flicker 8 frames,
Split Out 16 px at speed 4. Oracle: the released scriptBuilder's loops, one
VM_IDLE per picture: Split In writes final + k*d then final - k*d for
k = steps..0 (d = 32 * 16 / steps subpixels), Flicker hides 2 pictures and
shows 2, ceil(frames / 4) + 1 times (VM_LOOP runs counter..0), Split Out
writes final +/- (steps - k)*d then returns to final and deactivates.
Compared: the sequence of distinct positions and the hidden runs.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.probe_rom import power_on_ram, verified_tick

MARK = 20


def project_case(root):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor = json.loads(actor_path.read_text(encoding='utf8'))
        actor['startScript'], actor['updateScript'] = [], []
        write_json(actor_path, actor)
    mark = lambda k: ev(f'mark-{k}', 'EVENT_SET_VALUE', variable=str(MARK), value=num(k))
    fx = lambda key, **a: ev(key, 'EVENT_ACTOR_EFFECTS', actorId='push-actor', **a)
    scene['script'] = [ev('unlock', 'EVENT_SCRIPT_UNLOCK'), ev('w', 'EVENT_WAIT', units='frames', frames=num(5)),
                       mark(1), fx('in', effect='splitIn', distance=16, speed=2, units='pixels'),
                       mark(2), fx('flicker', effect='flicker', timeUnits='frames', frames=8),
                       mark(3), fx('out', effect='splitOut', distance=16, speed=4, units='pixels'), mark(4)]
    write_json(path, scene)
    return project


def expected():
    def split(steps, out):
        d = math.floor(16 * 32 / steps)
        seq = []
        for k in range(steps, -1, -1):
            m = (steps - k) if out else k
            seq += [m * d, -m * d]
        return seq + ([0] if out else [])
    def dedup(seq):
        return [v for i, v in enumerate(seq) if i == 0 or v != seq[i - 1]]
    return dedup(split(8, False)), math.ceil(8 / 4) + 1, dedup(split(4, True))


def run(out, toolchain, emulator, bios):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out), out / 'export')
    build = build_rom(out / 'export', toolchain)
    rom = (out / 'export/rom/game.ngc').read_bytes()
    symbols = build['link_audit']['public_symbols']
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, 'noise')
    m.set_breakpoints([verified_tick(rom, build)])
    base = symbols['_script_memory']['address']
    mark = lambda: int.from_bytes(m.read(base + 2 * manifest['variables'][f'VAR_VARIABLE_{MARK}'], 2), 'little')
    x = lambda: int.from_bytes(m.read(symbols['_act_x']['address'] + 2, 2), 'little')
    # Visible sprites (K2GE attribute priority != 0): fewer while the actor is hidden.
    visible = lambda: sum(1 for a in m.read(0x8800, 256)[1::4] if (a >> 3) & 3)
    rows = []
    for _ in range(600):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError('tick not reached')
        rows.append((mark(), x(), visible()))
        if rows[-1][0] == 4:
            break
    else:
        raise ValueError('actor effects lab did not finish')
    # The first write happens in the mark's own picture: the reference is the
    # actor's resting position, at the end of the flicker (phase 2).
    final = [r[1] for r in rows if r[0] == 2][-1]

    def positions(k, extra=0):
        picked = [r[1] - final for r in rows if r[0] == k]
        picked += [r[1] - final for r in rows if r[0] == k + 1][:extra]
        return [v for i, v in enumerate(picked) if i == 0 or v != picked[i - 1]]
    shown = max(r[2] for r in rows if r[0] == 1)
    hidden = [r[2] < shown for r in rows if r[0] == 2]
    runs = sum(1 for i, h in enumerate(hidden) if h and (i == 0 or not hidden[i - 1]))
    want_in, want_runs, want_out = expected()
    got_in = positions(1)
    got_out = positions(3, extra=1)      # the return to final lands in mark 4's picture
    report = {'schema': 'ngpcraft-actor-effects-lab/1',
              'split_in': {'expected': want_in, 'rom': got_in},
              'flicker_hidden_runs': {'expected': want_runs, 'rom': runs},
              'split_out': {'expected': want_out, 'rom': got_out},
              'passed': got_in == want_in and runs == want_runs and got_out == want_out,
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
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if result['passed'] else 1)
