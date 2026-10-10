"""Maintainer-only snapshot of proven Over Rev audio declarations.

Clang resolves references and preprocessor branches here, never at game export.
The product selector consumes the closed registry, not the user's driver/tools.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from ngpcraft.audio_resolve import FIELDS
from ngpcraft.music_streams import _compile_instruments, generate_music
from ngpcraft.project import _atomic_write
from tools.music_candidate import candidate_source
from tools.music_extract import TYPES, walk

ROOT = Path(__file__).resolve().parents[1]


def declarations(source, clang):
    with tempfile.TemporaryDirectory(prefix='ngc_audio_freeze_ast_') as temp:
        path = Path(temp) / 'source.c'
        _atomic_write(path, source)
        proc = subprocess.run([clang, '--target=wasm32', '-std=c89', '-pedantic-errors',
                               '-fsyntax-only', '-Xclang', '-ast-dump=json', str(path)],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise ValueError(proc.stderr)
        tree = json.loads(proc.stdout)
    nodes, global_ids, typedefs = {}, set(), []

    def body(node):
        return any(c['kind'] == 'CompoundStmt' for c in node.get('inner', []))

    def text_of(node):
        span = node['range']
        return source[span['begin']['offset']:span['end']['offset'] + span['end']['tokLen']]

    for node in tree['inner']:
        if 'offset' not in node.get('range', {}).get('begin', {}):
            continue
        kind = node['kind']
        if kind in ('RecordDecl', 'EnumDecl'):
            if not any(other['kind'] == 'TypedefDecl'
                       and 'offset' in other.get('range', {}).get('begin', {})
                       and other['range']['begin']['offset'] <= node['range']['begin']['offset']
                       and other['range']['end']['offset'] >= node['range']['end']['offset']
                       for other in tree['inner']):
                typedefs.append(text_of(node) + ';')
        elif kind == 'TypedefDecl':
            typedefs.append(text_of(node) + ';')
        elif kind in ('VarDecl', 'FunctionDecl'):
            global_ids.add(node['id'])
            name = node['name']
            if name not in nodes or body(node) or (kind == 'VarDecl' and node.get('storageClass') != 'extern'):
                nodes[name] = node
        elif kind != 'EmptyDecl':
            raise ValueError('unexpected audio declaration: ' + kind)
    units = {}
    for name, node in sorted(nodes.items(), key=lambda item: item[1]['range']['begin']['offset']):
        if node['kind'] == 'FunctionDecl' and not body(node):
            continue
        text = text_of(node)
        external = node.get('storageClass') == 'extern'
        mutable = False
        if not external and not text.startswith('static '):
            text = 'static ' + text
        if node['kind'] == 'VarDecl' and not external:
            qual = node['type']['qualType']
            immutable = ('*' not in qual and qual.startswith('const ')) or bool(re.search(r'\*const(?:\s|$)', qual))
            mutable = not immutable
            if mutable and 'init' in node:
                if name not in ('s_bgm_note_table', 's_freq_base', 's_attn_base'):
                    raise ValueError('unsupported initialized audio RAM: ' + name)
                text = text.split('=', 1)[0].rstrip()
        deps = set()
        for child in walk(node):
            if child.get('kind') != 'DeclRefExpr':
                continue
            ref = child['referencedDecl']
            if ref['kind'] not in ('FunctionDecl', 'VarDecl'):
                continue
            if ref['kind'] == 'VarDecl' and ref['id'] not in global_ids:
                continue
            if ref['name'] != name:
                deps.add(ref['name'])
        fn = node['kind'] == 'FunctionDecl'
        units[name] = {'kind': 'function' if fn else 'variable', 'source': text + ('' if fn else ';'),
                       'dependencies': sorted(deps), 'mutable': mutable,
                       'prototype': text[:text.index('{')].rstrip() + ';' if fn else ''}
    for name, unit in units.items():
        missing = set(unit['dependencies']) - units.keys()
        if missing:
            raise ValueError(f'{name}: unresolved declarations {sorted(missing)}')
    return typedefs, units


def freeze(driver, clang, out):
    driver = Path(driver)
    variants, chunks = {}, {}

    def intern(value):
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        chunks[digest] = value
        return digest

    for factory in range(8):
        for updates in range(9):
            definition = dict.fromkeys(FIELDS, 0)
            definition.update(attn=3, env_step=1, env_speed=1, vib_speed=1, sweep_speed=1, sweep_end=1)
            for bit, field in enumerate(('env_curve_id', 'pitch_curve_id', 'macro_id')):
                definition[field] = int(bool(factory & (1 << bit)))
            instruments = [{'source_id': 0, 'def': definition}]
            tables = _compile_instruments(instruments)
            plan = {'driver_profile': 'bgm-legacy-mono-vblank', 'notes': [], 'channels': [None]*4,
                    'instruments': instruments, 'tables': tables}
            features = []
            if updates:
                features = ['hold'] + [name for bit, name in enumerate(('level', 'divider', 'noise_note'))
                                       if (updates - 1) & (1 << bit)]
                plan.update(driver_profile='bgm-hold-mono-vblank/1', stream_extensions=features)
            files = generate_music(plan)
            source = candidate_source(driver, files)
            # The laboratory single-song controller is not part of the snapshot.
            boundary = '\n/* Candidate V3 single-song controller;'
            if source.count(boundary) != 1:
                raise ValueError('unrecognized laboratory lifecycle boundary')
            source = source.split(boundary)[0] + '\nconst u8 NOTE_TABLE[] = {1, 0};\n'
            files['source.c'] = source
            files['sounds.h'] = (driver/'sounds.h').read_text(encoding='utf-8-sig')
            for name in ('ngpc_types.h', 'ngpc_hw.h', 'ngpc_sys.h'):
                files[name] = TYPES
            with tempfile.TemporaryDirectory(prefix='ngc_audio_freeze_pp_') as temp:
                root = Path(temp)
                for name, text in files.items():
                    _atomic_write(root/name, text)
                proc = subprocess.run([clang, '--target=wasm32', '-E', '-P', '-DBGM_DEBUG=0',
                                       str(root/'source.c')], capture_output=True, text=True, timeout=60)
                if proc.returncode:
                    raise ValueError(proc.stderr)
            proc.stdout.encode('ascii')
            types, units = declarations(proc.stdout, clang)
            variants[f'{factory}:{updates}'] = {'typedefs': intern(types),
                'units': {name: intern(unit) for name, unit in units.items()},
                'source_sha256': hashlib.sha256(proc.stdout.encode()).hexdigest(),
                'factory_mask': factory, 'stream_extensions': features}
    evidence = {str(name): hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in [('Over Rev/src/audio/sounds.c', driver/'sounds.c'),
                                   ('Over Rev/src/audio/sounds.h', driver/'sounds.h'),
                                   ('Clang', Path(clang))]}
    for name in ('tools/freeze_music_runtime.py', 'tools/music_candidate.py', 'tools/music_driver_probe.py',
                 'labs/music_transport.c', 'labs/music_stop.c', 'ngpcraft/music_hold.py',
                 'ngpcraft/runtime/music_hold.c'):
        evidence[name] = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    registry = {'schema': 'ngpcraft-native-audio-registry/1', 'variants': variants, 'chunks': chunks,
                'source_sha256': evidence,
                'license': 'MIT; MUSIC_NOTICE.txt; Copyright (c) 2026 NGPC Template NgpCraft Tixu',
                'adaptations': ['selected Sound Creator factory tables', 'staging transport and queued Stop',
                                'idle guard accounts for pending PSG silence', 'selected held-stream extensions',
                                'static linkage; three redundant RAM initializers removed'],
                'scope': 'conservative declaration closure; reached function branches retained'}
    _atomic_write(out, json.dumps(registry, indent=2) + '\n')
    return {'variants': len(variants), 'unique_chunks': len(chunks), 'bytes': Path(out).stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--driver', type=Path, required=True)
    parser.add_argument('--clang', default=os.environ.get('NGC_TEST_CLANG'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.driver, args.clang, args.out)))
