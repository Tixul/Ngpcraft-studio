"""Compare exported audio against freshly read Over Rev C, with a host sink.

The reference executes original id_music Play/Stop/frame for non-race music.
Both paths use the proven V3 post-update deferred start and VBlank guard.
No Z80 execution or timing claim is made by this host probe.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from ngpcraft.music_library import generate_library
from ngpcraft.music_streams import generate_music
from ngpcraft.project import _atomic_write
from tools.music_candidate import candidate_source
from tools.music_extract import TYPES
from tools.music_lifecycle_probe import SINK


def probe_source(library, driver, reference=False, loading=False, saving=False):
    driver = Path(driver)
    files = generate_library(library, loading=loading)
    source = files['audio.c']
    if reference:
        plan = library['tracks'][0]['plan'].copy()
        plan.update(driver_profile='bgm-legacy-mono-vblank')
        if library['stream_extensions']:
            plan.update(driver_profile='bgm-hold-mono-vblank/1', stream_extensions=library['stream_extensions'])
        files.update(generate_music(plan))
        live = candidate_source(driver, files).split('\n/* Candidate V3 single-song controller;')[0]
        # Keep the exact generated data but replace the frozen driver/controller.
        begin = source.index('typedef struct {\n    const unsigned char *notes;')
        end = source.index('/* Over Rev id_music_frame runs') if loading else source.index('/* Multi-song lifecycle adapted')
        data = source[begin:end]
        original_path = Path(driver).parent/'id_music.c'
        original = original_path.read_text(encoding='utf8')
        functions = []
        for name in ('id_music_play', 'id_music_frame', 'id_music_stop'):
            match = re.search(rf'^void {name}\([^\n]*\)\n\{{.*?^\}}', original, re.S|re.M)
            if not match:
                raise ValueError('unrecognized original Over Rev function ' + name)
            functions.append(match[0].replace('OrMusic', 'NgcMusicData').replace('g_or_music', 'ngc_music_tracks')
                             .replace('m->ch[', 'm->channels[').replace('m->loop[', 'm->loop_offsets['))
        source = '#define BGM_DEBUG 0\n' + live + '\nconst u8 NOTE_TABLE[] = {1, 0};\n' + data
        source += f'#define MUS_COUNT {len(library["tracks"])}\n#define MUS_AUCUNE 255\n'
        source += 'static u8 mus_cur;\nstatic u8 mus_attente;\nstatic u8 reference_last_frame;\nu8 id_music_racing(void) { return 0; }\n'
        source += '\n'.join(functions) + '''
void ngc_audio_init(void) {
    Sounds_ResetState(); mus_cur = 255; mus_attente = 255;
    reference_last_frame = g_vb_counter;
}
void ngc_music_play_track(unsigned short track) { id_music_play((u8)track); }
void ngc_music_stop_track(void) { id_music_stop(); }
void ngc_audio_service(void) {
    if (reference_last_frame == g_vb_counter) return;
    reference_last_frame = g_vb_counter;
    Sounds_Update(); id_music_frame();
}
'''
        if loading:
            source = source.replace('static u8 reference_last_frame;', 'static u8 reference_last_frame;\nstatic u8 reference_loading;')
            source = source.replace('reference_last_frame = g_vb_counter;', 'reference_last_frame = g_vb_counter; reference_loading = 0;',1)
            source = source.replace('Sounds_Update(); id_music_frame();','Sounds_Update(); if (!reference_loading) id_music_frame();')
            source += 'void ngc_audio_set_loading(unsigned char loading) { reference_loading = loading; }\n'
        current = '(mus_cur == 255 ? 65535u : mus_cur)'
        pending = '(mus_attente == 255 ? 65535u : mus_attente)'
    else:
        current, pending = 'ngc_music_current', 'ngc_music_pending'
        # Init still clears all selected RAM; replace hardware installation only.
        source = source.replace('    Sounds_Init();', '    Sounds_ResetState();')
    source = source.replace('#define SND_COUNT   (*(volatile u8 *)0x7003)', '#define SND_COUNT probe_count')
    source = source.replace('#define SND_BUF     ((volatile u8 *)0x7004)', '#define SND_BUF probe_buffer')
    source = source.replace('(*(volatile u8 *)0x7003)', 'probe_count').replace('((volatile u8 *)0x7004)', 'probe_buffer')
    source = source.replace('SND_COUNT = s_buf_count;', 'probe_commit(s_buf_count);')
    source = source.replace('probe_count = s_buf_count;', 'probe_commit(s_buf_count);')
    if source.count('probe_commit(s_buf_count);') != 2:
        raise ValueError('unrecognized selected audio commit boundaries')
    source = SINK + source
    source += '''
volatile u8 g_vb_counter;
void probe_init(void) {
    g_vb_counter = 0; probe_count = 0; probe_packet_count = 0;
    probe_overflow = 0; ngc_audio_init();
}
void probe_clock(unsigned int frame) { g_vb_counter = (u8)frame; }
void probe_play(unsigned int track) { ngc_music_play_track((u16)track); }
void probe_stop(void) { ngc_music_stop_track(); }
void probe_service(void) { ngc_audio_service(); }
unsigned int probe_state(unsigned int field) {
    switch (field) {
    case 0: return s_bgm_song_frame;
    case 1: return s_bgm_v0.note_idx;
    case 2: return s_bgm_v0.note_active;
    case 3: return CURRENT;
    case 4: return PENDING;
    case 5: return probe_packet_count;
    case 6: return probe_overflow;
    case 7: return s_sound_drops;
    }
    return 0;
}
unsigned int probe_packet(unsigned int index, unsigned int field) {
    return probe_packets[index][field];
}
'''.replace('CURRENT', current).replace('PENDING', pending)
    files['probe.c'] = source
    if loading:
        files['probe.c'] += 'void probe_loading(unsigned int loading) { ngc_audio_set_loading((u8)loading); }\n'
    if reference:
        files['sounds.h'] = (Path(driver)/'sounds.h').read_text(encoding='utf-8-sig')
        for name in ('ngpc_types.h', 'ngpc_hw.h', 'ngpc_sys.h'):
            files[name] = TYPES
    if saving:
        files['probe.c'] += '\nvoid probe_save(void) { /* Snapshot has no audio side effect. */ }\n'
        if reference:
            files['probe.c'] += '''
/* GBVM data_load restores identity and queues music_next_track, or cuts it.
 * Map those semantics to the original Over Rev frame starter above. */
void probe_restore(unsigned int track) {
    if (track != 65535u && track >= MUS_COUNT) return;
    Bgm_Stop();
    mus_cur = (track == 65535u ? 255u : (u8)track);
    mus_attente = mus_cur;
    reference_last_frame = g_vb_counter;
}
'''
        else:
            from ngpcraft.save_emit import unit_source
            files['probe.c'] += '''
static unsigned short probe_saved_track;
void ngc_save_put(unsigned char value) { (void)value; }
unsigned char ngc_save_get(unsigned short offset) {
    return (unsigned char)(offset ? (probe_saved_track >> 8) : probe_saved_track);
}
'''+unit_source('src/audio.c',['ngc_music_current'])+'''
void probe_restore(unsigned int track) {
    probe_saved_track = (unsigned short)track;
    if (ngc_state_check_audio(0u)) ngc_state_get_audio(0u);
}
'''
    return files


def run_library(library, driver, actions, reference=False, clang=None, loading=False, saving=False):
    previous = 0
    for action in actions:
        if not isinstance(action, (tuple, list)) or len(action) < 2:
            raise ValueError('library probe: expected [frame, operation, ...]')
        frame, op = action[:2]
        if type(frame) is not int or frame < previous:
            raise ValueError('library probe: frames must be nonnegative and monotonic')
        previous = frame
        operations=('play','stop','service')+ (('loading',) if loading else ()) + (('restore','save') if saving else ())
        if op not in operations or len(action) != (3 if op in ('play','loading','restore') else 2):
            raise ValueError('library probe: invalid operation')
        if op == 'play' and (type(action[2]) is not int or not 0 <= action[2] < len(library['tracks'])):
            raise ValueError('library probe: invalid selected track')
        if op == 'loading' and (type(action[2]) is not int or action[2] not in (0,1)):
            raise ValueError('library probe: invalid loading state')
        if op == 'restore' and (type(action[2]) is not int or not 0<=action[2]<=65535):
            raise ValueError('library probe: invalid saved track word')
    if reference and len(library['tracks']) > 255:
        raise ValueError('Over Rev reference uses an 8-bit track index')
    files = probe_source(library, driver, reference, loading, saving)
    script = '''const fs = require('fs');
WebAssembly.instantiate(fs.readFileSync(process.argv[2]), {}).then(({instance}) => {
  const p = instance.exports, trace = []; p.probe_init();
  const initial_packets = Array.from({length:p.probe_state(5)},(_,i)=>[0,1,2].map(f=>p.probe_packet(i,f)));
  for (const [frame, op, ...args] of JSON.parse(fs.readFileSync(process.argv[3]))) {
    const begin = p.probe_state(5); p.probe_clock(frame); p['probe_' + op](...args);
    if (p.probe_state(6)) throw new Error('audio packet trace overflow');
    const packets = [];
    for (let i = begin; i < p.probe_state(5); i++) packets.push([0,1,2].map(f=>p.probe_packet(i,f)));
    trace.push({frame, op, state:Array.from({length:8},(_,f)=>p.probe_state(f)), packets});
  }
  console.log(JSON.stringify({trace, initial_packets}));
}).catch(e=>{console.error(e); process.exit(1);});
'''
    clang = clang or os.environ['NGC_TEST_CLANG']
    with tempfile.TemporaryDirectory(prefix='ngc_library_probe_') as temp:
        root = Path(temp)
        files.update({'run.js': script, 'actions.json': json.dumps(actions)})
        for name, text in files.items():
            _atomic_write(root/name, text)
        exports = ('init', 'clock', 'play', 'stop', 'service', 'state', 'packet')
        if loading: exports += ('loading',)
        if saving: exports += ('restore','save')
        # No libc in this oracle: keep Clang's loop/copy optimizations from
        # introducing unresolved hosted memcpy/memset calls.
        proc = subprocess.run([clang, '--target=wasm32', '-std=c89', '-pedantic-errors', '-O2',
                               '-nostdlib', '-ffreestanding', '-Wl,--no-entry',
                               *('-Wl,--export=probe_'+name for name in exports),
                               str(root/'probe.c'), '-o', str(root/'probe.wasm')],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise ValueError(proc.stderr)
        proc = subprocess.run([shutil.which('node'), str(root/'run.js'), str(root/'probe.wasm'),
                               str(root/'actions.json')], capture_output=True, text=True, timeout=60, check=True)
    return {'schema': 'ngpcraft-native-library-probe/1', 'reference': reference,
            'compiled_source_sha256': hashlib.sha256(files['probe.c'].encode()).hexdigest(),
            'driver_sha256': hashlib.sha256((Path(driver)/'sounds.c').read_bytes()).hexdigest(),
            'controller_reference_sha256': hashlib.sha256((Path(driver).parent/'id_music.c').read_bytes()).hexdigest(),
            'scope': 'host C immediate sink; original Over Rev non-race Play/Stop/frame with V3 service order',
            **json.loads(proc.stdout)}
