"""Execute original local driver C with generated data, hardware IO uncalled.

The source file is read afresh; only instrument/curve data are substituted.
This is a host sequencer proof, not a timing/transport/console proof.
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


def driver_source(driver, files):
    src = (driver / 'sounds.c').read_text(encoding='utf-8-sig')
    if '#include "../../sound/sound_sample_instruments.c"' in src:
        src = src.replace('#include "../../sound/sound_sample_instruments.c"', '#include "instruments.inc"')
    else:
        src, count = re.subn(r'static const BgmInstrument s_bgm_instruments\[\] = \{.*?\n\};',
                             '#include "instruments.inc"', src, flags=re.S)
        if count != 1:
            raise ValueError('unrecognized driver instrument table')
    begin = src.index('#ifndef SOUNDS_ENABLE_MACROS')
    end = src.index('/* Duration timer (frames) */', begin)
    tables = []
    for name, flag, types, count_name in [
        ('macros', 'MACROS', 'typedef struct {u8 frames; s8 attn_delta; s16 pitch_delta;} BgmMacroStep;\ntypedef struct {const BgmMacroStep *steps; u8 count;} BgmMacro;', 'macro'),
        ('env_curves', 'ENV_CURVES', 'typedef struct {const s8 *steps; u8 count;} BgmEnvCurve;', 'env_curve'),
        ('pitch_curves', 'PITCH_CURVES', 'typedef struct {const s16 *steps; u8 count;} BgmPitchCurve;', 'pitch_curve')]:
        enabled = name + '.inc' in files
        tables.append(f'#define SOUNDS_ENABLE_{flag} {int(enabled)}\n')
        if enabled:
            tables.append(types + f'\n#include "{name}.inc"\n')
            tables.append(f'static const u8 s_bgm_{count_name}_count = sizeof(s_bgm_{name}) / sizeof(s_bgm_{name}[0]);\n')
        else:
            tables.append(f'static const u8 s_bgm_{count_name}_count = 0;\n')
    src = src[:begin] + ''.join(tables) + src[end:]
    if 'stream_extensions.json' in files:
        from ngpcraft.music_hold import adapt_driver
        src = adapt_driver(src, json.loads(files['stream_extensions.json'])['features'])
    return src


WRAPPER = '''
#include "music.c"
const u8 NOTE_TABLE[] = {1, 0};
static BgmVoice *probe_voices[4];
static PsgCmd probe_commands[4];
void probe_init(void) {
    u8 ch;
    probe_voices[0] = &s_bgm_v0; probe_voices[1] = &s_bgm_v1;
    probe_voices[2] = &s_bgm_v2; probe_voices[3] = &s_bgm_vn;
    s_bgm_speed = 1; s_bgm_loop = 1; s_bgm_gate_percent = 100;
    s_bgm_song_frame = 0;
    Bgm_ResetFadeState();
    Bgm_SetNoteTable(ngc_music.notes);
    for (ch = 0; ch < 4; ch++) {
        probe_voices[ch]->freq_base = (u8)(0x80 + ch * 32);
        probe_voices[ch]->attn_base = (u8)(0x90 + ch * 32);
        BgmVoice_StartEx(probe_voices[ch], ngc_music.channels[ch], ngc_music.loop_offsets[ch]);
    }
}
void probe_step(unsigned int frame) {
    u8 ch;
    s_bgm_song_frame = frame;
    for (ch = 0; ch < 4; ch++) BgmVoice_Step(probe_voices[ch], &probe_commands[ch]);
}
unsigned int probe_get(unsigned int ch, unsigned int field) {
    BgmVoice *v = probe_voices[ch];
    switch(field) {
    case 0: return v->dbg_events;
    case 1: return v->dbg_last_cmd;
    case 2: return v->dbg_last_note;
    case 3: return v->next_frame;
    case 4: return v->inst_id;
    case 5: return v->base_div;
    case 6: return v->attn;
    case 7: return probe_commands[ch].b1;
    case 8: return probe_commands[ch].b2;
    case 9: return probe_commands[ch].b3;
    }
    return 0;
}
'''


def run_driver(plan, driver, frames, clang=None):
    clang = clang or os.environ['NGC_TEST_CLANG']
    node = shutil.which('node')
    driver = Path(driver)
    files = generate_music(plan)
    source = driver_source(driver, files)
    with tempfile.TemporaryDirectory(prefix='ngc_music_') as temp:
        dest = Path(temp)
        for name, text in files.items():
            _atomic_write(dest / name, text)
        _atomic_write(dest / 'sounds.h', (driver / 'sounds.h').read_text(encoding='utf-8-sig'))
        types = '''#ifndef PROBE_TYPES_H
#define PROBE_TYPES_H
typedef unsigned char u8; typedef signed char s8;
typedef unsigned short u16; typedef signed short s16;
typedef unsigned int u32; typedef signed int s32;
extern u8 VBCounter, g_vb_counter;
extern u16 SOUNDCPU_CTRL, HW_SOUNDCPU_CTRL;
#endif
'''
        for name in ('ngpc.h', 'library.h', 'ngpc_types.h', 'ngpc_hw.h', 'ngpc_sys.h'):
            _atomic_write(dest / name, types)
        _atomic_write(dest / 'probe.c', '#define BGM_DEBUG 1\n' + source + WRAPPER)
        wasm = dest / 'probe.wasm'
        command = [clang, '--target=wasm32', '-std=c89', '-pedantic-errors', '-O2', '-nostdlib', '-ffreestanding',
                   '-Wl,--no-entry', '-Wl,--export=probe_init', '-Wl,--export=probe_step',
                   '-Wl,--export=probe_get', str(dest / 'probe.c'), '-o', str(wasm)]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise RuntimeError(compiled.stdout + compiled.stderr)
        script = '''const fs = require('fs');
WebAssembly.instantiate(fs.readFileSync(process.argv[2]), {}).then(({instance}) => {
  const p=instance.exports, trace=[], previous=[0,0,0,0]; p.probe_init();
  for(let frame=0;frame<Number(process.argv[3]);frame++) {
    p.probe_step(frame);
    for(let ch=0;ch<4;ch++) {
      const count=p.probe_get(ch,0);
      if(count!==previous[ch]) {
        trace.push([frame,ch,...Array.from({length:9},(_,f)=>p.probe_get(ch,f+1))]);
        previous[ch]=count;
      }
    }
  }
  console.log(JSON.stringify(trace));
}).catch(e=>{console.error(e);process.exit(1);});
'''
        _atomic_write(dest / 'run.js', script)
        run = subprocess.run([node, str(dest / 'run.js'), str(wasm), str(frames)],
                             capture_output=True, text=True, timeout=60, check=True)
    return {'trace': json.loads(run.stdout), 'driver_sha256': hashlib.sha256((driver / 'sounds.c').read_bytes()).hexdigest()}
