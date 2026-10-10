"""Logical hUGE register writes for the C/E/B/D/F subset and note subpatterns.

Port of pinned GBVM hUGEDriver.asm (GBVM_LICENSE.txt); note table authored
by SuperDisk, 2019, retained numerically. No hardware envelope, length,
sweep, DAC waveform synthesis, seconds or NGPC adaptation is simulated.
Caller supplies the initial readable register values explicitly.
"""
import copy
import json
from pathlib import Path

from .audio_assets import MusicError
from .uge import GROUPS, KINDS
from .uge_timeline import _initial, _step, _validate


PERIODS = (
    44,156,262,363,457,547,631,710,786,854,923,986,
    1046,1102,1155,1205,1253,1297,1339,1379,1417,1452,1486,1517,
    1546,1575,1602,1627,1650,1673,1694,1714,1732,1750,1767,1783,
    1798,1812,1825,1837,1849,1860,1871,1881,1890,1899,1907,1915,
    1923,1930,1936,1943,1949,1954,1959,1964,1969,1974,1978,1982,1985,1988,
    1992,1995,1998,2001,2004,2006,2009,2011,2013,2015,
)
READ_REGISTERS = (0xff12,0xff17,0xff1c,0xff21,0xff25)
REGISTER_NAMES = ('NR12','NR22','NR32','NR42','NR51')
# 6xy (call routine) writes no register: GBVM queues it (uge_convert.routine_calls).
SUPPORTED = (None,0,1,2,6,8,10,11,12,13,14,15)


def _check(song):
    _validate(song)
    for order, item in enumerate(song['sequence']):
        for ch, pid in enumerate(item['channels']):
            for row, cell in enumerate(song['patterns'][pid]):
                loc=f'uge.order[{order}].channel[{ch}].row[{row}]'
                if cell['effectCode'] not in SUPPORTED:
                    raise MusicError(f'{loc}: sound effect {cell["effectCode"]:X} is not implemented')
                if cell['note'] is not None and cell['instrument'] is not None:
                    inst=song[GROUPS[KINDS[ch]]][cell['instrument']]
                    if inst['subpatternEnabled']:
                        for sr, sub in enumerate(inst['subpattern'][:32]):
                            if sub['effectCode'] is not None:
                                raise MusicError(f'{loc}.instrument[{cell["instrument"]}].subpattern[{sr}]: '
                                                 'subpattern sound effects are not implemented')
                            if sub['note'] is not None and not 0<=sub['note']<=71:
                                raise MusicError(f'{loc}.subpattern[{sr}]: expected delta note 0..71')
                            if sub['jump'] is not None and sub['jump']>32:
                                raise MusicError(f'{loc}.subpattern[{sr}]: jump outside 32 emitted rows')


def noise_poly(note):
    """Exact byte arithmetic of original get_note_poly; not PSG noise mapping."""
    value=(~((note+192)&255))&255
    if value<7:
        return value
    upper=((value>>2)-1)&255
    swapped=((upper<<4)|(upper>>4))&255
    return ((value&3)+4)|swapped


def _envelope(inst):
    change=inst['volumeSweepChange']
    return (inst['initialVolume']<<4)|(8 if change>0 else 0)|(8-abs(change) if change else 0)


class _Sound:
    def __init__(self,song,registers):
        self.song=song
        self.clock=_initial(song)
        self.reg=dict(registers)
        self.current_wave=100
        self.channels=[{'note':0,'period':0,'highmask':0,'table_row':0,
                        'table_active':False,'instrument':None,'step_width':0} for _ in range(4)]
        self.writes=[]

    def write(self,addr,value):
        if not 0<=value<=255:
            raise MusicError(f'uge.sound: register {addr:04X} write outside byte range: {value}')
        self.reg[addr]=value
        self.writes.append([addr,value])

    def play(self,ch):
        state=self.channels[ch]
        if ch==3:
            self.write(0xff22,state['period'])
            self.write(0xff23,state['highmask'])
            return
        if ch==2:
            pan=self.reg[0xff25]
            self.write(0xff25,pan&0xbb)
            self.write(0xff1a,0)
            self.write(0xff1a,255)
        self.write(0xff13+ch*5,state['period']&255)
        self.write(0xff14+ch*5,(state['period']>>8)|state['highmask'])
        if ch==2:self.write(0xff25,pan)

    def load(self,ch,index):
        state=self.channels[ch]
        state['highmask']&=0x7f
        if index is None:return
        inst=self.song[GROUPS[KINDS[ch]]][index]
        state.update(instrument=index,table_active=inst['subpatternEnabled'],table_row=0)
        length=inst['length']
        if ch<2:
            if ch==0:
                shift=inst['frequencySweepShift']
                self.write(0xff10,(inst['frequencySweepTime']<<4)|(8 if shift<0 else 0)|abs(shift))
            self.write(0xff11+ch*5,(inst['dutyCycle']<<6)|((64-length)&63 if length is not None else 0))
            self.write(0xff12+ch*5,_envelope(inst))
        elif ch==2:
            self.write(0xff1b,(256-length)&255 if length is not None else 0)
            self.write(0xff1c,inst['volume']<<5)
            if self.current_wave!=inst['waveIndex']:
                self.current_wave=inst['waveIndex']
                pan=self.reg[0xff25]
                self.write(0xff25,pan&0xbb)
                self.write(0xff1a,0)
                wave=self.song['waves'][self.current_wave]
                for i in range(16):self.write(0xff30+i,(wave[i*2]<<4)|wave[i*2+1])
                self.write(0xff1a,128)
                self.write(0xff25,pan)
        else:
            self.write(0xff21,_envelope(inst))
            self.write(0xff20,(64-length)&63 if length is not None else 0)
            state['step_width']=8 if inst['bitCount']==7 else 0
            state['period']|=state['step_width']
        state['highmask']=128|(64 if length is not None else 0)

    def effect(self,ch,cell):
        code,param=cell['effectCode'],cell['effectParam']
        state = self.channels[ch]
        if code == 0 and param:
            phase = ((self.clock['counter'] - 1) & 255) % 3
            note = state['note'] + ((param & 15) if phase == 0 else (param >> 4) if phase == 1 else 0)
            if ch == 3:
                self.write(0xff22, noise_poly(PERIODS[note] & 255) | state['step_width'])
                self.write(0xff23, 0)
            else:
                if note >= len(PERIODS):
                    raise MusicError('uge.arpeggio: note outside original period table')
                state['period'] = PERIODS[note]
                self.write(0xff13+ch*5, state['period'] & 255)
                self.write(0xff14+ch*5, state['period'] >> 8)
        elif code in (1, 2) and self.clock['tick'] != 0:
            period = (state['period'] + (param if code == 1 else -param)) & 65535
            if ch == 3:
                self.write(0xff22, noise_poly(period & 255) | state['step_width'])
                self.write(0xff23, 0)
            else:
                state['period'] = period
                self.write(0xff13+ch*5, period & 255)
                self.write(0xff14+ch*5, period >> 8)
        elif code == 8 and self.clock['tick'] == 0:
            self.write(0xff25, param)
        elif code == 10 and self.clock['tick'] == 0:
            addr = 0xff12 + ch*5
            readback = self.reg[addr] | (0x9f if ch == 2 else 0)
            level = min(15, max(0, (readback >> 4) - (param & 15)) + (param >> 4))
            self.write(addr, level << 4)
            high = 0xff14 + ch*5
            # NRx4 readback has unused bits set (GB APU register masks).
            # NRx4 is not among the supplied initial registers: until the song
            # writes it, its length-enable bit 6 is the power-on 0.
            written = self.reg[high] if high in self.reg else 0
            current = (written & 0x40) | 0xbf
            self.write(high, current | 128)
            self.play(ch)
        elif code==12 and self.clock['tick']==0:
            # SM83 SWAP C swaps both nibbles of the full source byte. Values
            # above 15 occur in real songs and can alter envelope low bits.
            swapped=((param<<4)|(param>>4))&255
            if ch==2:
                value=0 if swapped==0 else 0x60 if swapped<0x50 else 0x40 if swapped<0xa0 else 0x20
                self.write(0xff1c,value)
            else:
                value=swapped|(self.reg[0xff12+ch*5]&15 if ch<2 else 0)
                self.write(0xff12+ch*5,value)
                self.play(ch)
        elif code==14 and self.clock['tick']==param:
            self.write(0xff12+ch*5,0)
            if ch!=2:self.write(0xff14+ch*5,255)

    def table(self,ch,loc):
        state=self.channels[ch]
        if not state['table_active']:return
        row=state['table_row']
        if row>=32:
            raise MusicError(f'{loc}.subpattern[{row}]: outside emitted program')
        inst=self.song[GROUPS[KINDS[ch]]][state['instrument']]
        cell=inst['subpattern'][row]
        # exportToC forces the 32nd row's numeric jump to one, even zero.
        jump=1 if row==31 and cell['jump'] is not None else cell['jump']
        state['table_row']=jump-1 if jump else row+1
        if cell['note'] is None:return
        note=(state['note']+cell['note']-36)&255
        if ch==3:
            self.write(0xff22,noise_poly(note)|state['step_width'])
            self.write(0xff23,state['highmask']&0x7f)
        else:
            if note>=len(PERIODS):
                raise MusicError(f'{loc}.subpattern[{row}]: transposed note {note} outside original table')
            state['period']=PERIODS[note]
            self.write(0xff13+ch*5,state['period']&255)
            self.write(0xff14+ch*5,(state['period']>>8)|(state['highmask']&0x7f))

    def snapshot(self):
        return {'clock':dict(self.clock),'channels':copy.deepcopy(self.channels),'current_wave':self.current_wave}

    def step(self):
        self.writes=[]
        order,row,tick=self.clock['current_order']//2,self.clock['row'],self.clock['tick']
        for ch,pid in enumerate(self.song['sequence'][order]['channels']):
            cell=self.song['patterns'][pid][row]
            state=self.channels[ch]
            loc=f'uge.order[{order}].channel[{ch}].row[{row}].tick[{tick}]'
            if tick==0:
                note=cell['note']
                if note is not None:
                    state['note']=note
                    state['period']=noise_poly(note) if ch==3 else PERIODS[note]
                    self.load(ch,cell['instrument'])
                self.effect(ch,cell)
                if note is not None:self.play(ch)
            # Original process_effects skips pattern effects with zero param;
            # E00 has already executed at tick zero; no other supported effect
            # writes audio at nonzero tick with param zero.
            elif cell['effectParam']:
                self.effect(ch,cell)
            self.table(ch,loc)
        _step(self.song,self.clock)


def _start(song,initial_registers):
    if not isinstance(initial_registers,dict) or set(initial_registers)!=set(READ_REGISTERS):
        raise MusicError('uge.sound.initial_registers: explicitly supply NR12/22/32/42/51')
    if any(type(v) is not int or not 0<=v<=255 for v in initial_registers.values()):
        raise MusicError('uge.sound.initial_registers: expected byte values')
    _check(song)
    return _Sound(song,initial_registers)


def read_initial_apu(path):
    """Explicit five named read registers; no silent bootstrap assumptions."""
    path=Path(path)
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError(f'duplicate register {key}')
            out[key]=value
        return out
    try:
        data=json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=unique)
    except (OSError,ValueError) as error:
        raise MusicError(f'{path}: invalid initial APU JSON: {error}') from error
    if not isinstance(data,dict) or set(data)!=set(REGISTER_NAMES):
        raise MusicError(f'{path}: expected exactly NR12/NR22/NR32/NR42/NR51')
    if any(type(v) is not int or not 0<=v<=255 for v in data.values()):
        raise MusicError(f'{path}: initial APU values must be byte integers')
    return {addr:data[name] for addr,name in zip(READ_REGISTERS,REGISTER_NAMES)}


def trace_sound(song,ticks,initial_registers):
    """Trace ordered APU writes and driver state; initial read registers required."""
    if type(ticks) is not int or not 0<=ticks<=65536:
        raise MusicError('uge.sound.ticks: expected integer 0..65536')
    sound=_start(song,initial_registers)
    states=[sound.snapshot()]
    writes=[]
    for _ in range(ticks):
        sound.step()
        writes.append(sound.writes)
        states.append(sound.snapshot())
    return {'schema':'ngpcraft-uge-registers/1','runtime_ready':False,
            'unit':'logical_huge_tick','states':states,'writes':writes,
            'initial_apu':{name:initial_registers[addr] for name,addr in zip(REGISTER_NAMES,READ_REGISTERS)},
            'limitations':['No hardware envelope/length/sweep/DAC simulation or NGPC adaptation.',
                           'Pattern effects 0/1/2/8/A/B/C/D/E/F and note/jump subpatterns only.']}


def walk_sound(song,initial_registers,max_ticks=65536):
    """Find a repeating driver-write state, without hardware sound simulation.

Arpeggio uses the byte counter, which must participate in cycle detection.
Without arpeggio the supported effects do not read it. Include all future read registers, wave ID and instrument
program states so a clock repetition alone cannot terminate the walk.
"""
    if type(max_ticks) is not int or not 1<=max_ticks<=65536:
        raise MusicError('uge.sound.max_ticks: expected integer 1..65536')
    sound=_start(song,initial_registers)
    seen,writes={},[]
    for absolute in range(max_ticks+1):
        state=sound.snapshot()
        if not any(cell['effectCode'] == 0 and cell['effectParam'] for pattern in song['patterns'] for cell in pattern):
            del state['clock']['counter']
        state['read_registers']=[sound.reg[a] for a in READ_REGISTERS]
        key=json.dumps(state,sort_keys=True,separators=(',',':'))
        if key in seen:
            start=seen[key]
            return {'schema':'ngpcraft-uge-write-cycle/1','runtime_ready':False,
                    'unit':'logical_huge_tick','ticks':absolute,
                    'cycle_start_tick':start,'cycle_ticks':absolute-start,
                    'writes':writes,'initial_apu':{name:initial_registers[addr] for name,addr in zip(REGISTER_NAMES,READ_REGISTERS)},
                    'cycle_state':state,
                    'limitations':['Driver-write cycle only; hardware envelope/length/sweep/DAC state is not simulated.',
                                   'No NGPC adaptation, audible loop or console timing is inferred.']}
        seen[key]=absolute
        if absolute==max_ticks:break
        sound.step()
        writes.append(sound.writes)
    raise MusicError(f'uge.sound: driver-write cycle exceeds {max_ticks} logical ticks')
