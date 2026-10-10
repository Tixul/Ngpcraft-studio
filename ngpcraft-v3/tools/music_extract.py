"""Extract a conservative C dependency closure for disposable music ROMs.

Clang resolves preprocessor branches and declaration references; Toshiba builds
the resulting C89. This selects functions/data, not individual VM opcodes or
instrument effects. It is not yet an exporter for arbitrary games.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from ngpcraft.music_streams import generate_music
from ngpcraft.project import _atomic_write
from tools.music_candidate import candidate_source

TYPES = '''#ifndef AUDIO_TYPES_H
#define AUDIO_TYPES_H
typedef unsigned char u8; typedef signed char s8;
typedef unsigned short u16; typedef signed short s16;
typedef unsigned long u32; typedef signed long s32;
extern volatile u8 g_vb_counter;
#define HW_SOUNDCPU_CTRL (*(volatile u16 *)0x00B8)
#endif
'''


def walk(node):
    yield node
    for child in node.get('inner', []):
        yield from walk(child)


def select_c(source, roots, clang):
    """Select source declarations through Clang's resolved references.

    Input is already preprocessed ASCII. All defined functions/data are made
    static, external variables stay external. Reject missing roots/dependencies.
    """
    with tempfile.TemporaryDirectory(prefix='ngc_music_ast_') as temp:
        path = Path(temp) / 'source.c'
        _atomic_write(path, source)
        proc = subprocess.run([clang, '--target=wasm32', '-std=c89', '-pedantic-errors',
                               '-fsyntax-only', '-Xclang', '-ast-dump=json', str(path)],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise ValueError(proc.stderr)
        tree = json.loads(proc.stdout)
    declarations = {}
    global_ids = set()
    typedefs = []

    def body(node):
        return any(c['kind'] == 'CompoundStmt' for c in node.get('inner', []))

    def text_of(node):
        span = node['range']
        return source[span['begin']['offset']:span['end']['offset'] + span['end']['tokLen']]

    for node in tree['inner']:
        if 'range' not in node or 'offset' not in node['range']['begin']:
            continue
        kind = node['kind']
        if kind in ('EnumDecl', 'RecordDecl'):
            if not any(other['kind'] == 'TypedefDecl' and 'offset' in other.get('range', {}).get('begin', {})
                       and other['range']['begin']['offset'] <= node['range']['begin']['offset']
                       and other['range']['end']['offset'] >= node['range']['end']['offset']
                       for other in tree['inner']):
                typedefs.append(text_of(node) + ';')
        elif kind == 'TypedefDecl':
            typedefs.append(text_of(node) + ';')
        elif kind in ('VarDecl', 'FunctionDecl'):
            global_ids.add(node['id'])
            name = node['name']
            if name not in declarations or body(node) or (kind == 'VarDecl' and node.get('storageClass') != 'extern'):
                declarations[name] = node
        elif kind != 'EmptyDecl':
            raise ValueError('unsupported top-level audio declaration: ' + kind)
    selected, pending = set(), list(roots)
    reasons = {name: ['requested root'] for name in roots}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in declarations:
            raise ValueError('undefined audio dependency: ' + name)
        node = declarations[name]
        if node['kind'] == 'FunctionDecl' and not body(node):
            raise ValueError('audio function has no definition: ' + name)
        selected.add(name)
        for child in walk(node):
            if child.get('kind') != 'DeclRefExpr':
                continue
            ref = child['referencedDecl']
            dep = ref.get('name')
            if dep in declarations and ref['kind'] in ('FunctionDecl', 'VarDecl'):
                # Match declaration identities as well as names: a local that
                # shadows a global must not retain that global.
                if ref['kind'] == 'VarDecl' and ref['id'] not in global_ids:
                    continue
                pending.append(dep)
                reasons.setdefault(dep, []).append(name)
    functions, variables, mutable = [], [], []
    for name, node in sorted(declarations.items(), key=lambda item: item[1]['range']['begin']['offset']):
        if name not in selected:
            continue
        text = text_of(node)
        if node['kind'] == 'FunctionDecl':
            if not text.startswith('static '):
                text = 'static ' + text
            functions.append(text)
        else:
            if node.get('storageClass') != 'extern':
                if not text.startswith('static '):
                    text = 'static ' + text
                # Clang's type of a pointer-to-const is not a const pointer.
                qual = node['type']['qualType']
                immutable = ('*' not in qual and qual.startswith('const ')) or bool(re.search(r'\*const(?:\s|$)', qual))
                if not immutable:
                    mutable.append(name)
                    # All current initializers are redundantly set by ResetState.
                    # Fail closed if the reference introduces a new one.
                    if 'init' in node:
                        if name not in ('s_bgm_note_table', 's_freq_base', 's_attn_base'):
                            raise ValueError('unsupported initialized audio RAM: ' + name)
                        text = text.split('=', 1)[0].rstrip()
            variables.append(text + ';')
    prototypes = [fn[:fn.index('{')].rstrip() + ';' for fn in functions]
    code = '\n'.join(typedefs + variables + prototypes + functions) + '\n'
    report = {'roots': list(roots), 'selected': sorted(selected),
              'removed': sorted(declarations.keys() - selected),
              'reasons': {name: sorted(set(reasons[name])) for name in sorted(selected)},
              'mutable_state': mutable, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'selected_sha256': hashlib.sha256(code.encode()).hexdigest(),
              'scope': 'conservative declaration closure; all reached function branches retained'}
    return code, report


def extract(plan, driver, roots, clang=None, buffered=True):
    clang = clang or os.environ['NGC_TEST_CLANG']
    driver = Path(driver)
    files = generate_music(plan)
    files['sounds.h'] = (driver / 'sounds.h').read_text(encoding='utf-8-sig')
    for name in ('ngpc_types.h', 'ngpc_hw.h', 'ngpc_sys.h'):
        files[name] = TYPES
    files['source.c'] = (candidate_source(driver, files, buffered=buffered) +
                         '\n#include "music.c"\nconst u8 NOTE_TABLE[] = {1, 0};\n')
    with tempfile.TemporaryDirectory(prefix='ngc_music_pp_') as temp:
        root = Path(temp)
        for name, content in files.items():
            _atomic_write(root / name, content)
        proc = subprocess.run([clang, '--target=wasm32', '-E', '-P', '-DBGM_DEBUG=0',
                               str(root / 'source.c')], capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise ValueError(proc.stderr)
    # Preprocessor removes the comments containing legacy mojibake.
    proc.stdout.encode('ascii')
    code, report = select_c(proc.stdout, roots, clang)
    report['driver_sha256'] = hashlib.sha256((driver / 'sounds.c').read_bytes()).hexdigest()
    report['driver_header_sha256'] = hashlib.sha256((driver / 'sounds.h').read_bytes()).hexdigest()
    report['clang_sha256'] = hashlib.sha256(Path(clang).read_bytes()).hexdigest()
    report['adaptations'] = [
        'shared candidate_source: queued Stop, idle guard, lifecycle and optional staging transport',
        'Clang preprocessing with BGM_DEBUG=0 and only selected factory tables',
        'static linkage for selected functions/data; unused declarations omitted',
        'three redundant RAM initializers removed; selected mutable state cleared before Sounds_Init',
    ]
    project = Path(__file__).resolve().parents[1]
    report['candidate_sources_sha256'] = {
        name: hashlib.sha256((project/name).read_bytes()).hexdigest()
        for name in ('tools/music_candidate.py', 'tools/music_extract.py', 'labs/music_stop.c',
                     'labs/music_transport.c', 'labs/music_lifecycle.c')}
    if 'stream_extensions.json' in files:
        for name in ('tools/music_driver_probe.py', 'ngpcraft/music_hold.py', 'ngpcraft/runtime/music_hold.c'):
            report['candidate_sources_sha256'][name] = hashlib.sha256((project/name).read_bytes()).hexdigest()
        report['adaptations'].append('selected V3 hold/level/divider/noise-note stream units')
    return code, report
