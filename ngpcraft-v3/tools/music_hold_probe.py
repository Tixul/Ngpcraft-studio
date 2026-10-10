"""Observe the actual selected native C voice on every logical frame."""
import hashlib,json,os,shutil,subprocess,tempfile
from pathlib import Path
from ngpcraft.music_streams import generate_music
from ngpcraft.project import _atomic_write
from tools.music_driver_probe import driver_source,WRAPPER

EXTRA=r'''
unsigned int hold_state(unsigned int ch, unsigned int field) {
    BgmVoice *v=probe_voices[ch];
    switch(field) {
    case 0:return v->note_active;
    case 1:return v->note_idx;
    case 2:return v->next_frame;
    case 3:return v->attn;
    case 4:return v->attn_cur;
    case 5:return v->base_div;
    case 6:return v->tone_div;
    case 7:return v->adsr_phase;
    case 8:return v->adsr_counter;
    case 9:return v->vib_delay_counter;
    case 10:return v->env_index;
    case 11:return v->inst_id;
    case 12:return probe_commands[ch].valid;
    case 13:return probe_commands[ch].b1;
    case 14:return probe_commands[ch].b2;
    case 15:return probe_commands[ch].b3;
    case 16:return v->dbg_last_cmd;
    case 17:return s_sound_fault;
    }
    return 0;
}
'''

def run(plan,driver,frames,clang=None,legacy=False):
    if type(frames) is not int or not 1<=frames<=65536:raise ValueError('invalid probe frame count')
    driver=Path(driver)
    files=generate_music(plan)
    if legacy:files.pop('stream_extensions.json',None)
    source=driver_source(driver,files)
    clang=clang or os.environ['NGC_TEST_CLANG']
    types='''#ifndef NGC_PROBE_TYPES
#define NGC_PROBE_TYPES
typedef unsigned char u8; typedef signed char s8;
typedef unsigned short u16; typedef signed short s16;
typedef unsigned int u32; typedef signed int s32;
extern u8 VBCounter, g_vb_counter;
extern u16 SOUNDCPU_CTRL, HW_SOUNDCPU_CTRL;
#endif
'''
    script='''const fs=require('fs');
WebAssembly.instantiate(fs.readFileSync(process.argv[2]),{}).then(({instance})=>{
 const p=instance.exports,out=[];p.probe_init();
 for(let frame=0;frame<Number(process.argv[3]);frame++){
  p.probe_step(frame);
  out.push(Array.from({length:4},(_,ch)=>Array.from({length:18},(_,f)=>p.hold_state(ch,f))));
 }
 process.stdout.write(JSON.stringify(out));
}).catch(e=>{console.error(e);process.exit(1);});
'''
    with tempfile.TemporaryDirectory(prefix='ngc_hold_') as tmp:
        dest=Path(tmp)
        files.update({'probe.c':'#define BGM_DEBUG 1\n'+source+WRAPPER+EXTRA,'run.js':script,
                      'sounds.h':(driver/'sounds.h').read_text(encoding='utf-8-sig')})
        for name in ('ngpc.h','library.h','ngpc_types.h','ngpc_hw.h','ngpc_sys.h'):files[name]=types
        for name,text in files.items():_atomic_write(dest/name,text)
        wasm=dest/'probe.wasm'
        cmd=[clang,'--target=wasm32','-std=c89','-pedantic-errors','-O2','-nostdlib','-Wl,--no-entry',
             '-Wl,--export=probe_init','-Wl,--export=probe_step','-Wl,--export=hold_state',str(dest/'probe.c'),'-o',str(wasm)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
        if p.returncode:raise ValueError(p.stderr)
        p=subprocess.run([shutil.which('node'),str(dest/'run.js'),str(wasm),str(frames)],capture_output=True,text=True,timeout=60,check=True)
        result={'trace':json.loads(p.stdout),'driver_sha256':hashlib.sha256((driver/'sounds.c').read_bytes()).hexdigest(),
                'selected_source_sha256':hashlib.sha256(source.encode()).hexdigest(),'legacy':legacy}
    return result
