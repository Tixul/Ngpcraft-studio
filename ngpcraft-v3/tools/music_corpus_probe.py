"""Read-only research corpus -> fresh data exports and original-C attack proof.

Bank convention is that of recherche_bgm/tools/export_rom.py: song-local
instruments_corpus.json when present, otherwise corpus-root bank. The paths
and hashes of both are recorded. MIDI encoding is explicit for this corpus.
"""
import argparse
import hashlib
import json
from pathlib import Path

from ngpcraft.audio_assets import MusicError, read_song
from ngpcraft.music_streams import compile_music, generate_music
from ngpcraft.project import _atomic_write
from tools.music_driver_probe import run_driver


def expected_attacks(song):
    patterns = song['patterns']
    speed, frame, events = 8, 0, []
    # Independent row-by-row oracle, never decodes generated stream bytes.
    for orders in (song['order'], song['order'][song['loop_point']:], song['order'][song['loop_point']:]):
        for pi in orders:
            pat = patterns[pi]
            for row in range(pat['length']):
                cells = [ch[row] for ch in pat['channels']]
                for cell in cells:
                    if cell is not None and cell.get('f') == 11 and cell['p']:
                        speed = cell['p']
                for ch, cell in enumerate(cells):
                    if cell is not None and cell['n'] not in (0, 255):
                        events.append((frame, ch, cell['n'], cell['i'], cell['a']))
                frame += speed
    return events, frame


def probe_corpus(corpus, driver, out):
    corpus, driver, out = Path(corpus), Path(driver), Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('proof destination must be new or empty')
    results = []
    for path in sorted(corpus.rglob('*.ngps')):
        bank = path.parent / 'instruments_corpus.json'
        if not bank.exists():
            bank = corpus / 'instruments_corpus.json'
        record = {'song': str(path), 'bank': str(bank), 'note_encoding': 'midi',
                  'song_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'bank_sha256': hashlib.sha256(bank.read_bytes()).hexdigest()}
        try:
            plan = compile_music(path, bank, 'midi', 'vblank')
        except MusicError as exc:
            results.append({**record, 'status': 'refused', 'reason': str(exc)})
            continue
        song, _ = read_song(path)
        expected, frames = expected_attacks(song)
        trace = run_driver(plan, driver, frames)
        notes = {n['compiled_id']: n['source_note'] for n in plan['notes']}
        instruments = {i: inst['source_id'] for i, inst in enumerate(plan['instruments'])}
        defaults = {inst['source_id']: inst['def']['attn'] for inst in plan['instruments']}
        actual = []
        for row in trace['trace']:
            frame, ch, cmd, note, next_frame, inst, divider, attn, *psg = row
            if cmd == 1:
                actual.append((frame, ch, notes[note] if ch < 3 else note, instruments[inst], attn))
        expected = [(frame, ch, n if ch < 3 else (n-1)%8+1, inst, defaults[inst] if attn == 255 else attn)
                    for frame, ch, n, inst, attn in expected]
        if actual != expected:
            raise AssertionError(f'{path}: original C differs from row oracle (expected {len(expected)}, got {len(actual)})')
        dest = out / path.relative_to(corpus).with_suffix('')
        for name, text in generate_music(plan).items():
            _atomic_write(dest/name, text)
        _atomic_write(dest/'driver-trace.json', json.dumps(trace, indent=2)+'\n')
        results.append({**record, 'status': 'verified', 'attacks': len(actual), 'frames': frames,
                        'instruments': len(plan['instruments']), 'driver_sha256': trace['driver_sha256'],
                        'stream_bytes': sum(len(ch['stream']) for ch in plan['channels'] if ch is not None)})
    report = {'format': 'ngpcraft-music-corpus-proof/1', 'driver': str(driver),
              'verified': sum(r['status'] == 'verified' for r in results),
              'refused': sum(r['status'] == 'refused' for r in results),
              'scope': 'P1 host original-C sequencer: attacks, pitches, instruments, attenuation over 3 passes; not ROM or console audio',
              'results': results}
    _atomic_write(out/'corpus.json', json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('corpus', type=Path)
    parser.add_argument('--driver', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = probe_corpus(args.corpus, args.driver, args.out)
    print(json.dumps({'verified': result['verified'], 'refused': result['refused']}, indent=2))
