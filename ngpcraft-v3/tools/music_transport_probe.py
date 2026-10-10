"""Persist original/candidate mailbox witnesses; host protocol proof only.

Run: python -B -m tools.music_transport_probe --driver PATH --out NEW_DIRECTORY
Requires NGC_TEST_CLANG and Node. No Z80, ROM, hardware timing or sound output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from ngpcraft.audio_resolve import FIELDS
from ngpcraft.music_streams import compile_music
from ngpcraft.project import _atomic_write
from tools.music_lifecycle_probe import run_lifecycle


def proof(driver, out):
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('output directory must be new or empty')
    song = {'version': 1, 'patterns': [{'length': 16,
            'channels': [[None] * 16 for _ in range(4)]}], 'order': [0], 'loop_point': 0}
    song['patterns'][0]['channels'][0][0] = {'n': 58, 'i': 0, 'a': 255}
    definition = dict.fromkeys(FIELDS, 0)
    definition.update(attn=3, env_step=1, env_speed=1, vib_speed=1, sweep_speed=1, sweep_end=1)
    bank = {'version': 2, 'instruments': [{'name': 'Transport witness', 'def': definition}]}
    with tempfile.TemporaryDirectory(prefix='ngc_transport_song_') as temp:
        root = Path(temp)
        _atomic_write(root / 'song.ngps', json.dumps(song))
        _atomic_write(root / 'bank.json', json.dumps(bank))
        plan = compile_music(root / 'song.ngps', root / 'bank.json', 'sound-creator', 'vblank')
    scenarios = {
        'partial_packet': [[0, 'send', 0x8A, 0x0D, 0x93], [0, 'consume', 1],
                           [0, 'send', 0xAC, 0x12, 0xB2], [0, 'drain']],
        'stop_busy': [[0, 'send', 0x8A, 0x0D, 0x93], [0, 'stop'],
                      [1, 'drain'], [2, 'service'], [2, 'drain']],
        'blocking_timeout': [[0, 'send', 0x8A, 0x0D, 0x93], [0, 'consume', 1],
                             [0, 'reset_psg'], [0, 'drain']],
    }
    results = {}
    for name, actions in scenarios.items():
        results[name] = {
            'actions': actions,
            'original': run_lifecycle(plan, driver, actions, deferred=True),
            'staging_only': run_lifecycle(plan, driver, actions, deferred=True, buffered=True),
            'candidate': run_lifecycle(plan, driver, actions, deferred=True,
                                       buffered=True, adapted=True),
        }
    checks = {
        'original_partial_packet_corrupted': results['partial_packet']['original']['trace'][-1]['packets'] == [[0x8A, 0x12, 0xB2]],
        'candidate_partial_packet_intact': results['partial_packet']['candidate']['trace'][-1]['packets'] == [[0x8A, 0x0D, 0x93]],
        'original_blocking_timeout_corrupted': results['blocking_timeout']['original']['trace'][-1]['packets'] == [[0x8A, 0, 0x9F]],
        'candidate_blocking_timeout_intact': results['blocking_timeout']['candidate']['trace'][-1]['packets'] == [[0x8A, 0x0D, 0x93]],
        'staging_alone_loses_stop': results['stop_busy']['staging_only']['trace'][-1]['packets'] == [],
        'candidate_retries_four_silences': sorted(results['stop_busy']['candidate']['trace'][-1]['packets']) ==
                                          [[v, v, v] for v in (0x9F, 0xBF, 0xDF, 0xFF)],
    }
    if not all(checks.values()):
        raise AssertionError('transport witness failed: ' + str(checks))
    project = Path(__file__).resolve().parents[1]
    sources = ['labs/music_transport.c', 'labs/music_stop.c', 'labs/music_lifecycle.c',
               'tools/music_lifecycle_probe.py', 'tools/music_transport_probe.py']
    report = {'format': 'ngpcraft-audio-transport-proof/1', 'proof': 'P1 host protocol',
              'scope': 'C driver with explicitly delayed byte consumer; no Z80 execution, ROM or console proof',
              'runtime_ready': False, 'checks': checks, 'scenarios': results,
              'source_sha256': {p: hashlib.sha256((project / p).read_bytes()).hexdigest() for p in sources},
              'driver_header_sha256': hashlib.sha256((Path(driver) / 'sounds.h').read_bytes()).hexdigest()}
    _atomic_write(out / 'transport.json', json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = proof(args.driver, args.out)
    print(json.dumps({'checks': report['checks'], 'report': str(args.out / 'transport.json')}))


if __name__ == '__main__':
    main()
