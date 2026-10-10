"""GB Studio link events on two cabled NGPC consoles (docs/M6_LINK.md).

One project, two ROMs: the same script starting with Link Host (console A)
or Link Join (console B). Each console sets its own values (variables 50..),
then 6 Link Transfers of 3 variables (send 50+3k, receive into 20+3k) and one
of 16 variables (32 bytes, GBVM's maximum: send 100.., receive into 120..),
then Link Close. The emulator runs both consoles cabled inside the native core
(core.native.run_linked, real BIOS COM path on both sides) and reads the
variables (no ROM probe). Expected: each console holds exactly the values the
other one sent, both reach the end. Alone (--alone): console B without a peer
stays on its first transfer while its frames keep running (GBVM waits for the
peer, no timeout), as on a Game Boy. Unplugged (--unplug): the cable is out
for 90 frames in the middle of the transfers, the bytes sent meanwhile lost;
plugged back, the recovery (docs/M6_LINK.md) completes every exchange with
the right data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.probe_rom import power_on_ram, write_frame

# Variables 0..19 are named in gbs2 (Quest1...): the lab uses unnamed ones.
MARK, SMALL, BIG = 99, 6, 16


def values(side):
    """What console `side` (0 host, 1 join) sends: distinct per side and slot."""
    small = [[(side + 1) * 1000 + k * 10 + i for i in range(3)] for k in range(SMALL)]
    big = [(side + 1) * 5000 + 37 * i for i in range(BIG)]
    return small, big


def project_case(root, side):
    project = arena(root)
    path = next((project / 'project/scenes').rglob('scene.gbsres'))
    scene = json.loads(path.read_text(encoding='utf8'))
    for actor_path in path.parent.glob('actors/*.gbsres'):
        actor_path.unlink()
    small, big = values(side)
    setv = lambda key, var, value: ev(key, 'EVENT_SET_VALUE', variable=str(var), value=num(value))
    script = [ev('mode', 'EVENT_LINK_HOST' if side == 0 else 'EVENT_LINK_JOIN')]
    script += [setv(f's{k}{i}', 50 + 3 * k + i, small[k][i]) for k in range(SMALL) for i in range(3)]
    script += [setv(f'b{i}', 100 + i, big[i]) for i in range(BIG)]
    # GB Studio gives a slot only to a variable some event uses, in id order:
    # a transfer writes the next SIZE slots. Every receiving variable is used.
    script += [setv(f'r{n}', n, 0) for n in [*range(20, 20 + 3 * SMALL), *range(120, 120 + BIG)]]
    script += [ev(f't{k}', 'EVENT_LINK_TRANSFER', sendVariable=str(50 + 3 * k),
                  receiveVariable=str(20 + 3 * k), size=3) for k in range(SMALL)]
    script += [setv('m1', MARK, 1),
               ev('tb', 'EVENT_LINK_TRANSFER', sendVariable='100', receiveVariable='120', size=BIG),
               ev('close', 'EVENT_LINK_CLOSE'), setv('m2', MARK, 2)]
    scene['script'] = script
    write_json(path, scene)
    return project


def machine(native, rom, bios):
    m = native.NativeMachine(rom, bios=bios)
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.serial_set_enabled(True)          # the cable goes in before the console boots
    m.reset(bios_handoff=True)
    power_on_ram(m, 'noise')
    return m


def run(out, toolchain, emulator, bios, alone=False, unplug=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    roms, manifests, builds = [], [], []
    for side in (0, 1):
        manifest = export_project(project_case(out / f'side{side}', side), out / f'side{side}/export')
        builds.append(build_rom(out / f'side{side}/export', toolchain))
        roms.append((out / f'side{side}/export/rom/game.ngc').read_bytes())
        manifests.append(manifest)
    emulator = Path(emulator).resolve()
    sys.path.insert(0, str(emulator))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(emulator):
        raise ValueError('different native binding')
    bios = Path(bios).read_bytes()
    machines = [machine(native, roms[0], bios), machine(native, roms[1], bios)]

    def var(side, n):
        names = manifests[side]['variables']
        base = builds[side]['link_audit']['public_symbols']['_script_memory']['address']
        return int.from_bytes(machines[side].read(base + 2 * names[f'VAR_VARIABLE_{n}'], 2), 'little')
    frames, done = 0, None
    if alone:
        lone = machines[1]
        # game.c ngc_game_time: the game keeps ticking while the VM waits.
        clock = builds[1]['link_audit']['public_symbols']['_ngc_game_time']['address']
        ticks = lambda: int.from_bytes(lone.read(clock, 2), 'little')
        lone.run_frames(60)
        before = ticks()
        for frames in range(1, 601):
            lone.run_frames(1)
        report = {'schema': 'ngpcraft-link-lab/1', 'alone': True, 'frames': frames,
                  'join_mark': var(1, MARK), 'received_first': [var(1, 20 + i) for i in range(3)],
                  'game_ticks_while_waiting': (ticks() - before) & 0xFFFF}
        report['passed'] = report['join_mark'] == 0 and report['game_ticks_while_waiting'] >= 590
    else:
        for frames in range(1, 1201):
            if unplug and 12 <= frames < 102:
                # Cable out for 90 frames, in the middle of the transfers:
                # what either console sends meanwhile is lost.
                for m in machines:
                    m.run_frames(1)
                    m.serial_read_tx(64)
                continue
            native.run_linked(machines[0], machines[1], 1)
            if done is None and var(0, MARK) == 2 and var(1, MARK) == 2:
                done = frames
                break
        for side in (0, 1):
            write_frame(out / f'screen-{side}.ppm', list(machines[side].framebuffer()))
        got = {side: {'small': [[var(side, 20 + 3 * k + i) for i in range(3)] for k in range(SMALL)],
                      'big': [var(side, 120 + i) for i in range(BIG)]} for side in (0, 1)}
        sent = {side: dict(zip(('small', 'big'), values(side))) for side in (0, 1)}
        report = {'schema': 'ngpcraft-link-lab/1', 'alone': False, 'unplug': unplug, 'frames_to_end': done,
                  'marks': [var(0, MARK), var(1, MARK)], 'received': got, 'sent': sent,
                  'features': manifests[0]['features']}
        report['passed'] = done is not None and got[0] == sent[1] and got[1] == sent[0]
    report.update({'rom_sha256': [hashlib.sha256(r).hexdigest() for r in roms], 'silicon_timing': [10, 8],
                   'ram': 'noise', 'console_validated': False,
                   'core_sha256': hashlib.sha256(Path(machines[0]._lib._name).read_bytes()).hexdigest()})
    write_json(out / 'comparison.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('out', 'toolchain', 'emulator', 'bios'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--alone', action='store_true')
    parser.add_argument('--unplug', action='store_true')
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.alone, args.unplug)
    print(json.dumps({k: v for k, v in result.items() if k not in ('received', 'sent')}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
