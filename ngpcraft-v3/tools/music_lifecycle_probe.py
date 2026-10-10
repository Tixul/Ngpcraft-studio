"""Host execution of Over Rev's public audio API, with an immediate sink.

The sink replaces shared RAM IO. It consumes immediately by default, or
byte by byte under explicit control. Neither mode executes a Z80 or models
elapsed hardware time. Candidate stop/lifecycle/transport changes are opt-in.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from ngpcraft.music_streams import generate_music
from ngpcraft.project import _atomic_write
from tools.music_candidate import candidate_source


SINK = '''
static unsigned char probe_buffer[15];
static unsigned char probe_count;
static unsigned int probe_packets[4096][3];
static unsigned int probe_packet_count;
static unsigned int probe_overflow;
static void probe_commit(unsigned char count) {
    unsigned char i;
    for (i = 0; i < count; i++) {
        if (probe_packet_count >= 4096) { probe_overflow = 1; return; }
        probe_packets[probe_packet_count][0] = probe_buffer[i * 3];
        probe_packets[probe_packet_count][1] = probe_buffer[i * 3 + 1];
        probe_packets[probe_packet_count][2] = probe_buffer[i * 3 + 2];
        probe_packet_count++;
    }
}
'''

WRAPPER = '''
#include "music.c"
u8 g_vb_counter;
const u8 NOTE_TABLE[] = {1, 0};
void probe_init(void) {
    g_vb_counter = 0;
    Sounds_ResetState();
    probe_packet_count = 0;
    probe_overflow = 0;
}
void probe_clock(unsigned int frame) { g_vb_counter = (u8)frame; }
void probe_start(void) {
    Bgm_SetNoteTable(ngc_music.notes);
    Bgm_StartLoop4Ex(ngc_music.channels[0], ngc_music.loop_offsets[0],
                    ngc_music.channels[1], ngc_music.loop_offsets[1],
                    ngc_music.channels[2], ngc_music.loop_offsets[2],
                    ngc_music.channels[3], ngc_music.loop_offsets[3]);
}
void probe_stop(void) { Bgm_Stop(); }
void probe_service(void) { Sounds_Update(); }
void probe_sfx(unsigned int channel, unsigned int frames) {
    Sfx_PlayToneCh((u8)channel, 300, 2, (u8)frames);
}
unsigned int probe_state(unsigned int field) {
    switch (field) {
    case 0: return s_bgm_song_frame;
    case 1: return s_bgm_v0.dbg_events;
    case 2: return s_bgm_v0.note_idx;
    case 3: return s_sfxTimer[0];
    case 4: return s_bgm_ch_used_by_sfx[0];
    case 5: return s_bgm_restore_ch[0];
    case 6: return s_sfx_active_mask;
    case 7: return probe_packet_count;
    case 8: return probe_overflow;
    case 9: return s_sound_drops;
    }
    return 0;
}
unsigned int probe_packet(unsigned int index, unsigned int field) {
    return probe_packets[index][field];
}
'''


DEFERRED_SINK = SINK + '''
static unsigned char probe_read_offset;
static unsigned char probe_partial[3];
static void probe_publish(unsigned char count) {
    probe_count = count;
    probe_read_offset = 0;
}
void probe_consume(unsigned int bytes) {
    while (bytes && probe_count) {
        probe_partial[probe_read_offset % 3] = probe_buffer[probe_read_offset];
        probe_read_offset++;
        bytes--;
        if (probe_read_offset % 3 == 0) {
            if (probe_packet_count >= 4096) { probe_overflow = 1; return; }
            probe_packets[probe_packet_count][0] = probe_partial[0];
            probe_packets[probe_packet_count][1] = probe_partial[1];
            probe_packets[probe_packet_count][2] = probe_partial[2];
            probe_packet_count++;
        }
        if (probe_read_offset == probe_count * 3) probe_count = 0;
    }
}
void probe_drain(void) { probe_consume(15); }
unsigned int probe_transport(unsigned int field) {
    if (field == 0) return probe_count;
    if (field == 1) return probe_read_offset;
    return probe_buffer[field - 2];
}
void probe_send(unsigned int b1, unsigned int b2, unsigned int b3);
void probe_burst(unsigned int base, unsigned int count);
void probe_reset_psg(void);
'''

TRANSPORT_WRAPPER = '''
void probe_send(unsigned int b1, unsigned int b2, unsigned int b3) {
    Sfx_SendBytes((u8)b1, (u8)b2, (u8)b3);
}
void probe_burst(unsigned int base, unsigned int count) {
    unsigned int i;
    Sfx_BufferBegin();
    for (i = 0; i < count; i++) Sfx_BufferPush((u8)(base + i), (u8)i, 0x93);
    Sfx_BufferCommit();
}
void probe_reset_psg(void) { Psg_ResetBases(); }
'''


def lifecycle_source(driver, files, adapted=False, deferred=False, buffered=False):
    source = candidate_source(driver, files, adapted=adapted, buffered=buffered)
    replacements = {
        '#define SND_COUNT   (*(volatile u8 *)0x7003)': '#define SND_COUNT probe_count',
        '#define SND_BUF     ((volatile u8 *)0x7004)': '#define SND_BUF probe_buffer',
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise ValueError('unrecognized audio transport: ' + old)
        source = source.replace(old, new)
    if source.count('SND_COUNT = s_buf_count;') != 2:
        raise ValueError('unrecognized audio commit boundaries')
    source = source.replace('SND_COUNT = s_buf_count;',
                            'probe_publish(s_buf_count);' if deferred else 'probe_commit(s_buf_count);')
    wrapper = WRAPPER
    if adapted:
        wrapper = wrapper.replace('Sounds_ResetState();', 'Sounds_ResetState(); ngc_audio_init();')
        wrapper, count = re.subn(r'void probe_start\(void\) \{.*?\n\}',
                                 'void probe_start(void) { ngc_music_play(); }', wrapper, flags=re.S)
        if count != 1:
            raise ValueError('unrecognized probe start')
        wrapper = wrapper.replace('void probe_stop(void) { Bgm_Stop(); }',
                                  'void probe_stop(void) { ngc_music_stop(); }')
        wrapper = wrapper.replace('void probe_service(void) { Sounds_Update(); }',
                                  'void probe_service(void) { ngc_audio_service(); }')
    if deferred:
        wrapper += TRANSPORT_WRAPPER
        wrapper = wrapper.replace('probe_overflow = 0;',
                                  'probe_overflow = 0; probe_count = 0; probe_read_offset = 0;')
    return '#define BGM_DEBUG 1\n' + (DEFERRED_SINK if deferred else SINK) + source + wrapper


def run_lifecycle(plan, driver, actions, clang=None, adapted=False, deferred=False, buffered=False):
    """Actions: [frame, start|stop|service] or [frame, sfx, channel, duration].

    Each snapshot records state and all packets committed by that action.
    With deferred=True, packets are recorded only as the consumer reads them.
    Extra actions: drain, consume byte_count, send b1 b2 b3, burst base count,
    reset_psg. adapted=True selects the lifecycle/stop candidates; buffered=True
    selects the staging transport independently, allowing separate witnesses.
    """
    previous = 0
    for action in actions:
        if not isinstance(action, (list, tuple)) or len(action) < 2:
            raise ValueError('expected [frame, action, ...]')
        frame, op = action[:2]
        if type(frame) is not int or frame < previous:
            raise ValueError('frames must be nonnegative and monotonic')
        previous = frame
        arities = {'start': 2, 'stop': 2, 'service': 2, 'sfx': 4}
        if deferred:
            arities.update(drain=2, consume=3, send=5, burst=4, reset_psg=2)
        if not isinstance(op, str) or op not in arities:
            raise ValueError('unknown lifecycle action')
        if len(action) != arities[op]:
            raise ValueError('invalid lifecycle action arguments')
        if op == 'sfx' and (type(action[2]) is not int or not 0 <= action[2] <= 2
                            or type(action[3]) is not int or not 1 <= action[3] <= 255):
            raise ValueError('invalid tone SFX channel/duration')
        if op == 'consume' and (type(action[2]) is not int or not 1 <= action[2] <= 15):
            raise ValueError('invalid consumer byte count')
        if op == 'send' and any(type(v) is not int or not 0 <= v <= 255 for v in action[2:]):
            raise ValueError('invalid packet byte')
        if op == 'burst' and (type(action[2]) is not int or not 0 <= action[2] <= 251
                              or type(action[3]) is not int or not 1 <= action[3] <= 5):
            raise ValueError('invalid packet burst')
    driver = Path(driver)
    files = generate_music(plan)
    source = lifecycle_source(driver, files, adapted, deferred, buffered)
    clang = clang or os.environ['NGC_TEST_CLANG']
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is required for the audio lifecycle probe')
    types = '''#ifndef PROBE_TYPES_H
#define PROBE_TYPES_H
typedef unsigned char u8; typedef signed char s8;
typedef unsigned short u16; typedef signed short s16;
typedef unsigned int u32; typedef signed int s32;
extern u8 g_vb_counter;
extern u16 HW_SOUNDCPU_CTRL;
#endif
'''
    script = '''const fs = require('fs');
WebAssembly.instantiate(fs.readFileSync(process.argv[2]), {}).then(({instance}) => {
  const p = instance.exports, trace = []; p.probe_init();
  for (const [frame, op, ...args] of JSON.parse(fs.readFileSync(process.argv[3]))) {
    const begin = p.probe_state(7); p.probe_clock(frame);
    p['probe_' + op](...args);
    if (p.probe_state(8)) throw new Error('PSG packet trace overflow');
    const packets = [];
    for (let i = begin; i < p.probe_state(7); i++)
      packets.push([0,1,2].map(f => p.probe_packet(i,f)));
    const row = {frame, op, state: Array.from({length:10},(_,f)=>p.probe_state(f)), packets};
    if (p.probe_transport) row.transport = {
      count: p.probe_transport(0), read_offset: p.probe_transport(1),
      buffer: Array.from({length:15},(_,i)=>p.probe_transport(i+2))};
    trace.push(row);
  }
  console.log(JSON.stringify(trace));
}).catch(e=>{console.error(e); process.exit(1);});
'''
    with tempfile.TemporaryDirectory(prefix='ngc_audio_lifecycle_') as temp:
        dest = Path(temp)
        files.update({'probe.c': source, 'run.js': script, 'actions.json': json.dumps(actions),
                      'sounds.h': (driver / 'sounds.h').read_text(encoding='utf-8-sig')})
        for name in ('ngpc_types.h', 'ngpc_hw.h', 'ngpc_sys.h'):
            files[name] = types
        for name, content in files.items():
            _atomic_write(dest / name, content)
        exports = ('init', 'clock', 'start', 'stop', 'service', 'sfx', 'state', 'packet')
        if deferred:
            exports += ('consume', 'drain', 'send', 'burst', 'reset_psg', 'transport')
        command = [clang, '--target=wasm32', '-std=c89', '-pedantic-errors', '-O2',
                   '-nostdlib', '-ffreestanding', '-Wl,--no-entry',
                   *('-Wl,--export=probe_' + name for name in exports),
                   str(dest / 'probe.c'), '-o', str(dest / 'probe.wasm')]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise RuntimeError(compiled.stdout + compiled.stderr)
        run = subprocess.run([node, str(dest / 'run.js'), str(dest / 'probe.wasm'),
                              str(dest / 'actions.json')], capture_output=True,
                             text=True, timeout=60, check=True)
    return {'format': 'ngpcraft-audio-lifecycle-probe/1', 'proof': 'host-public-api',
            'adaptation': 'V3 candidate lifecycle/stop units' if adapted else 'original driver',
            'transport': ('deferred byte consumer; no Z80 execution or timing' if deferred
                          else 'immediate sink; no Z80 or contention simulation'),
            'buffered_transport': buffered,
            'driver_sha256': hashlib.sha256((driver / 'sounds.c').read_bytes()).hexdigest(),
            'compiled_source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'trace': json.loads(run.stdout)}
