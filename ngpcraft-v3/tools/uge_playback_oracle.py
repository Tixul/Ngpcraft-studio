"""Run the pinned GBVM hUGEDriver ASM unchanged in reference Binjgb.

Song data comes from the real GB Studio exportToC (only syntax is translated
to RGBDS). Each host request calls dosound once; no simulated tracker code.
This is a logical tick oracle, not GB/NGPC cadence or listening validation.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from tools.reference import REFERENCE
from tools.uge_oracle import run as export_original
from tools.gbstudio_ts import OracleError


def assembly(data):
    """Strict syntax translation of original emitted C, preserving packed DN."""
    blocks = re.findall(r'(?:static const|const) ([\w *]+?) (\w+)(\[\])? = \{(.*?)\};', data, re.S)
    if not blocks or blocks[-1][1] != 'oracle_Data':
        raise OracleError('unexpected original exportToC layout')
    symbols = [b[1] for b in blocks] + ['order_cnt']
    def token(value):
        value = value.strip().removeprefix('&')
        if value in symbols:
            return 'data_' + value
        if value == 'routines':
            return 'oracle_routines'
        if re.fullmatch(r'[A-G]s[3-8]', value):
            return value.replace('s', '#')
        if re.fullmatch(r'(0x[0-9A-F]+|\d+|___|[A-G]_[3-8])', value):
            return '$' + value[2:] if value.startswith('0x') else value
        raise OracleError('unrecognized exported token: ' + value)
    count = re.search(r'order_cnt = (\d+);', data)
    if not count:
        raise OracleError('missing exported order count')
    out = ['include "include/hUGE.inc"', 'SECTION "song data", ROM0',
           'data_order_cnt: db ' + count[1], 'oracle_routines:']
    # Routine effects deliberately refused by run; no GBVM callback replacement.
    out += ['dw 0'] * 16
    for kind, name, array, body in blocks:
        out.append('data_' + name + ':')
        if kind == 'hUGESong_t':
            values = [token(t) for t in body.split(',')]
            out += ['db ' + values[0], 'dw ' + ','.join(values[1:])]
        elif kind in ('hUGEDutyInstr_t', 'hUGEWaveInstr_t', 'hUGENoiseInstr_t'):
            records = re.findall(r'\{([^{}]+)\}', body)
            residual = re.sub(r'\{[^{}]+\}', '', body).replace(',', '').strip()
            if residual or not records:
                raise OracleError('unexpected instrument records')
            for record in records:
                values = [token(t) for t in record.split(',')]
                if kind == 'hUGENoiseInstr_t':
                    if len(values) != 5:
                        raise OracleError('unexpected noise ABI')
                    out += ['db ' + values[0], 'dw ' + values[1], 'db ' + ','.join(values[2:])]
                else:
                    if len(values) != 5:
                        raise OracleError('unexpected instrument ABI')
                    out += ['db ' + ','.join(values[:3]), 'dw ' + values[3], 'db ' + values[4]]
        elif '*' in kind:
            out.append('dw ' + ','.join(token(t) for t in body.split(',') if t.strip()))
        elif 'DN(' in body:
            rows = re.findall(r'DN\(([^()]*)\)', body)
            if re.sub(r'DN\([^()]*\)', '', body).replace(',', '').strip():
                raise OracleError('unexpected pattern layout')
            out.extend('    dn ' + ','.join(token(t) for t in row.split(',')) for row in rows)
        elif kind == 'unsigned char':
            out.append('db ' + ','.join(token(t) for t in body.split(',') if t.strip()))
        else:
            raise OracleError('unexpected exported type: ' + kind)
    return '\n'.join(out) + '\n'


GLUE = '''include "include/hardware.inc"
SECTION "entry", ROM0[$100]
    nop
    jp main
    ds $150 - @, 0
SECTION "oracle control", WRAM0
oracle_request: db
oracle_ack: db
oracle_ready: db
SECTION "oracle code", ROM0
main:
    di
    ld sp, $dfff
    xor a
    ldh [rIE], a
    ld [oracle_request], a
    ld [oracle_ack], a
    ld [oracle_ready], a
    ld a, $80
    ldh [rAUDENA], a
    ld a, $ff
    ldh [rAUDTERM], a
    ld a, $77
    ldh [rAUDVOL], a
    ld hl, data_oracle_Data
    call hUGE_init
    ld a, 1
    ld [oracle_ready], a
.wait:
    ld a, [oracle_request]
    or a
    jr z, .wait
    call hUGE_dosound
    xor a
    ld [oracle_request], a
    ld hl, oracle_ack
    inc [hl]
    jr .wait
'''

RUNNER = r'''
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const root=process.env.NGC_REFERENCE;
const cases=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const here=path.dirname(process.argv[2]);
const driver=path.join(root,'appData/engine/gbvm/third-party/HUGE_TRACKER');
const factories={};
for(const name of ['rgbasm','rgblink','rgbfix']) {
    const p=path.join(root,'appData/wasm/rgbds',name);
    const local=path.join(here,name+'.mjs');
    fs.copyFileSync(p+'.js',local);
    factories[name]=(await import(pathToFileURL(local))).default;
}
const binPath=path.join(here,'binjgb.cjs');
fs.copyFileSync(path.join(root,'appData/wasm/binjgb/binjgb.js'),binPath);
const binFactory=(await import(pathToFileURL(binPath))).default;
async function rgb(name,args,files) {
    const errors=[];
    const m=await factories[name]({
        locateFile:()=> 'file:///unused.wasm',
        wasmBinary:fs.readFileSync(path.join(root,'appData/wasm/rgbds',name+'.wasm')),
        arguments:args,preRun:m=>{
            m.FS.mkdir('include');
            for(const [p,v] of Object.entries(files))m.FS.writeFile(p,v);
        },print:()=>{},printErr:s=>errors.push(s)
    });
    if(errors.some(s=>/error:|ERROR:/.test(s)))throw Error(errors.join('\n'));
    return m.FS;
}
const includes={};
for(const n of ['hardware.inc','hUGE.inc','hUGE_note_table.inc'])
    includes['include/'+n]=fs.readFileSync(path.join(driver,'include',n));
const source=fs.readFileSync(path.join(driver,'hUGEDriver.asm'));
const output=[];
for(const c of cases) {
    const files={...includes,'driver.asm':source,'song.asm':c.assembly,'glue.asm':c.glue};
    const objects={};
    for(const n of ['driver','song','glue']) {
        const f=await rgb('rgbasm',['-E',n+'.asm','-o',n+'.o'],files);
        objects[n+'.o']=f.readFile(n+'.o');
    }
    const linked=await rgb('rgblink',['-o','out.gb','-n','out.sym',...Object.keys(objects)],objects);
    const fixed=await rgb('rgbfix',['-v','out.gb','-p','0xff'],{'out.gb':linked.readFile('out.gb')});
    const rom=fixed.readFile('out.gb');
    const symbols={};
    for(const line of linked.readFile('out.sym',{encoding:'utf8'}).split('\n')) {
        const match=line.match(/^([0-9a-f]+):([0-9a-f]+) (\S+)$/i);
        if(match)symbols[match[3]]=parseInt(match[2],16);
    }
    const m=await binFactory({wasmBinary:fs.readFileSync(path.join(root,'appData/wasm/binjgb/binjgb.wasm')),print:()=>{},printErr:()=>{}});
    const ptr=m._malloc(rom.length);
    m.HEAPU8.set(rom,ptr);
    const emu=m._emulator_new_simple(ptr,rom.length,44100,2048);
    if(!emu)throw Error('Binjgb rejected original driver ROM');
    function read(name) {
        if(!(name in symbols))throw Error('missing original symbol '+name);
        return m._emulator_read_mem(emu,symbols[name]);
    }
    function advanceUntil(name,value) {
        for(let i=0;i<(c.record_writes ? 250000 : 4096);i++) {
            if(read(name)===value)return;
            const pcBefore=m._emulator_get_PC(emu);
            const writeCount=activeWrites ? activeWrites.length : 0;
            if(c.record_writes && activeWrites) captureWrite();
            const result=m._emulator_run_until_f64(emu,m._emulator_get_ticks_f64(emu)+(c.record_writes && activeWrites ? 1 : 512));
            // Audio-buffer events can return before executing the instruction.
            // A store always advances PC here (interrupts disabled by glue).
            if(c.record_writes && activeWrites && m._emulator_get_PC(emu)===pcBefore)
                activeWrites.length=writeCount;
            if(result&24)throw Error('emulator trap while waiting for '+name);
        }
        throw Error('original driver timeout '+name);
    }
    let activeWrites=null;
    function captureWrite() {
        const pc=m._emulator_get_PC(emu);
        const mem=a=>m._emulator_read_mem(emu,a&65535);
        const op=mem(pc), a=m._emulator_get_A(emu), hl=m._emulator_get_HL(emu);
        let addr=null, value=a;
        if(op===0xe0)addr=0xff00|mem(pc+1);
        else if(op===0xe2)addr=0xff00|(m._emulator_get_BC(emu)&255);
        else if(op===0xea)addr=mem(pc+1)|(mem(pc+2)<<8);
        else if(op===0x22 || op===0x32)addr=hl;
        else if(op>=0x70 && op<=0x77 && op!==0x76) {
            addr=hl;
            const bc=m._emulator_get_BC(emu),de=m._emulator_get_DE(emu);
            value=[bc>>8,bc&255,de>>8,de&255,hl>>8,hl&255,0,a][op-0x70];
        }
        else if(op===0x36){addr=hl;value=mem(pc+1);}
        else if(op===0x02)addr=m._emulator_get_BC(emu);
        else if(op===0x12)addr=m._emulator_get_DE(emu);
        else if(op===0x34 || op===0x35){addr=hl;value=(mem(hl)+(op===0x34?1:-1))&255;}
        else if(op===0x08) {
            const dst=mem(pc+1)|(mem(pc+2)<<8),sp=m._emulator_get_SP(emu);
            for(const [p,v] of [[dst,sp&255],[(dst+1)&65535,sp>>8]])
                if(p>=0xff10 && p<=0xff3f)activeWrites.push([p,v]);
        } else if(op===0xcb && hl>=0xff10 && hl<=0xff3f) {
            const cb=mem(pc+1);
            if((cb&7)===6 && !(cb>=0x40 && cb<=0x7f))throw Error('unhandled CB APU store');
        }
        // All stores used by the driver to APU are among these opcodes;
        // unknown CB writes fail explicitly instead of disappearing.
        // Capture before execution; run_until(ticks+1) steps one instruction.
        if(addr!==null && addr>=0xff10 && addr<=0xff3f)activeWrites.push([addr,value]);
    }
    function snapshot() {
        const s={};
        for(const name of ['current_order','row','tick','counter','ticks_per_row','row_break','next_order'])s[name]=read(name);
        s.current_wave=read('current_wave');
        s.channels=[];
        for(let ch=1;ch<=4;ch++) {
            const base=symbols['channel_period'+ch];
            if(base===undefined)throw Error('missing original channel');
            s.channels.push(Array.from({length:16},(_,i)=>m._emulator_read_mem(emu,base+i)));
        }
        // Hardware readbacks include unused bits; preserve them unchanged.
        s.apu=Array.from({length:23},(_,i)=>m._emulator_read_mem(emu,0xff10+i));
        return s;
    }
    try {
        advanceUntil('oracle_ready',1);
        const trace=[snapshot()];
        const writes=[];
        for(let t=0;t<c.ticks;t++) {
            if(c.record_writes)activeWrites=[];
            m._emulator_write_mem(emu,symbols.oracle_request,1);
            advanceUntil('oracle_ack',(t+1)&255);
            if(c.record_writes){writes.push(activeWrites);activeWrites=null;}
            trace.push(snapshot());
        }
        output.push({trace,...(c.record_writes ? {writes} : {}),rom_sha256:(await import('node:crypto')).createHash('sha256').update(rom).digest('hex')});
    } finally {m._emulator_delete(emu);}
}
process.stdout.write(JSON.stringify(output));
'''


def run(cases):
    """cases: {song: original normalized Song, ticks: bounded call count}."""
    node = shutil.which('node')
    if not node:
        raise OracleError('Node required for original hUGEDriver execution')
    for case in cases:
        if type(case['ticks']) is not int or not 0 <= case['ticks'] <= 65536:
            raise OracleError('ticks must be 0..65536')
        song = case['song']
        if not 1 <= len(song['sequence']) <= 127:
            raise OracleError('original byte order count requires 1..127 orders')
        cells = [cell for p in song['patterns'] for cell in p]
        cells += [cell for group in ('dutyInstruments','waveInstruments','noiseInstruments')
                  for instr in song[group] if instr['subpatternEnabled'] for cell in instr['subpattern'][:32]]
        if any(cell['effectCode'] == 6 for cell in cells):
            raise OracleError('routine callbacks require GBVM context; refused in tick oracle')
    exported = export_original([{'song':c['song'], 'export_c':True} for c in cases])
    payload = [{'assembly':assembly(e['export_c']), 'glue':GLUE, 'ticks':c['ticks'], 'record_writes':c.get('record_writes',False)}
               for c,e in zip(cases,exported)]
    with tempfile.TemporaryDirectory(prefix='ngc_huge_') as tmp:
        root=Path(tmp)
        (root/'runner.mjs').write_text(RUNNER,encoding='utf8')
        (root/'cases.json').write_text(json.dumps(payload),encoding='utf8')
        proc=subprocess.run([node,str(root/'runner.mjs'),str(root/'cases.json')],
                            cwd=root,env={**os.environ,'NGC_REFERENCE':str(REFERENCE)},
                            capture_output=True,timeout=180)
        if proc.returncode:
            raise OracleError(proc.stderr.decode('utf8','replace')[-3000:])
        return json.loads(proc.stdout)


def fingerprints():
    paths = ['src/shared/lib/uge/ugeHelper.ts',
             'appData/engine/gbvm/third-party/HUGE_TRACKER/hUGEDriver.asm']
    paths += ['appData/engine/gbvm/third-party/HUGE_TRACKER/include/'+p
              for p in ('hUGE.inc','hUGE_note_table.inc','hardware.inc')]
    paths += ['appData/wasm/'+p+suffix for p in ('rgbds/rgbasm','rgbds/rgblink','rgbds/rgbfix','binjgb/binjgb')
              for suffix in ('.js','.wasm')]
    return {p:hashlib.sha256((REFERENCE/p).read_bytes()).hexdigest() for p in paths}
