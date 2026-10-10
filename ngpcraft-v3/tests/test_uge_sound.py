"""Compare ported sound state AND ordered writes with unchanged ASM execution."""
import copy
import gzip
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.audio_assets import MusicError
from ngpcraft.uge import read_uge
from ngpcraft.uge_sound import PERIODS, READ_REGISTERS, read_initial_apu, trace_sound,walk_sound
from ngpcraft.uge_timeline import FIELDS,walk_rows
from tools.reference import REFERENCE
from tools.uge_playback_oracle import fingerprints,run
from test_uge_timeline import fixture,effect
from test_uge import binary_fixture


def initial(original):
    return {a:original['trace'][0]['apu'][a-0xff10] for a in READ_REGISTERS}


def canonical(original):
    out=[]
    for tick in original['trace']:
        channels=[]
        for ch,raw in enumerate(tick['channels']):
            offset=1 if ch==3 else 0
            channels.append({'note':raw[4],'period':raw[0]|(raw[1]<<8),
                             'highmask':raw[5],'table_row':raw[10+offset],
                             'table_active':bool(raw[8+offset]|raw[9+offset]),
                             'step_width':raw[6] if ch==3 else 0})
        out.append({'clock':{f:tick[f] for f in FIELDS},'channels':channels})
    return out


def comparable(model):
    return [{'clock':s['clock'],'channels':[{k:v for k,v in ch.items() if k!='instrument'}
                                           for ch in s['channels']]} for s in model['states']]


def proof(name,data):
    dest=os.environ.get('NGC_TEST_ARTIFACTS')
    if dest:
        root=Path(dest)/'uge-sound'
        root.mkdir(parents=True,exist_ok=True)
        with gzip.open(root/(name+'.json.gz'),'xb') as stream:stream.write(json.dumps(data).encode('utf8'))


class SoundTests(unittest.TestCase):
    def compare(self,cases,name):
        originals=run([{**c,'record_writes':True} for c in cases])
        models=[]
        for i,(c,orig) in enumerate(zip(cases,originals)):
            with self.subTest(i=i):
                model=trace_sound(c['song'],c['ticks'],initial(orig))
                self.assertEqual(comparable(model),canonical(orig))
                self.assertEqual([s['current_wave'] for s in model['states']],
                                 [s['current_wave'] for s in orig['trace']])
                self.assertEqual(model['writes'],orig['writes'])
                models.append(model)
        proof(name,{'cases':cases,'originals':originals,'models':models,'fingerprints':fingerprints()})
        return originals,models

    def test_attack_hold_volume_cut_and_instrument_only(self):
        s=fixture()
        s['ticksPerRow']=4
        s['dutyInstruments'][1]['volumeSweepChange']=2
        s['noiseInstruments'][1]['volumeSweepChange']=2
        for ch in range(4):
            effect(s,0,ch,None,None,note=24,instrument=0)
            effect(s,1,ch,12,5,instrument=1)
            effect(s,2,ch,14,2)
            effect(s,3,ch,None,None,note=28)
            effect(s,4,ch,14,0,note=24,instrument=0)
            effect(s,5,ch,12,0)
        orig,models=self.compare([{'song':s,'ticks':28}],'hold-cuts')
        self.assertEqual(models[0]['writes'][1],[])
        # A C effect on a held pulse writes both volume and trigger; on a
        # simultaneous attack it triggers twice, preserving original order.
        held=models[0]['writes'][4]
        self.assertEqual([v for a,v in held if a==0xff14],[0x86])
        cut=models[0]['writes'][16]
        self.assertEqual([v for a,v in cut if a==0xff14],[255,0x86])
        wrong=copy.deepcopy(models[0]['writes'])
        wrong[4]=[item for item in wrong[4] if item[0]!=0xff14]
        self.assertNotEqual(wrong,orig[0]['writes'])

    def test_instrument_fields_and_subpatterns_on_all_channels(self):
        s=fixture()
        s['ticksPerRow']=4
        for ch in range(4):effect(s,0,ch,None,None,note=36,instrument=0)
        s['dutyInstruments'][0].update(length=19,frequencySweepTime=3,frequencySweepShift=-2,volumeSweepChange=-3)
        s['waveInstruments'][0].update(length=71,volume=3,waveIndex=4)
        s['noiseInstruments'][0].update(length=17,bitCount=7,volumeSweepChange=2)
        for group in ('dutyInstruments','waveInstruments','noiseInstruments'):
            instr=s[group][0]
            instr['subpatternEnabled']=True
            for i,note in enumerate((36,48,None)):
                instr['subpattern'][i].update(note=note,jump=1 if i==2 else 0)
        self.compare([{'song':s,'ticks':140}],'instruments-subpatterns')

    def test_exported_last_subpattern_row_loops_even_jump_zero(self):
        s=fixture()
        effect(s,0,0,None,None,note=36,instrument=0)
        s['dutyInstruments'][0]['subpatternEnabled']=True
        orig,models=self.compare([{'song':s,'ticks':34}],'subpattern-last')
        self.assertEqual(models[0]['states'][32]['channels'][0]['table_row'],0)

    def test_random_sound_cells_against_original(self):
        rng=random.Random(43203)
        cases=[]
        for _ in range(16):
            s=fixture()
            s['ticksPerRow']=rng.randint(1,8)
            for row in range(64):
                for ch in range(4):
                    note=rng.randint(12,60) if rng.random()<0.3 else None
                    inst=rng.choice((None,0,1))
                    code=rng.choice((None,None,12,14,15))
                    param=rng.randint(0,255) if code==12 else rng.randint(0,9) if code==14 else rng.randint(1,8) if code==15 else None
                    effect(s,row,ch,code,param,note,inst)
            cases.append({'song':s,'ticks':300})
        self.compare(cases,'random')

    def test_five_eligible_corpus_songs_two_clock_cycles(self):
        names=('Rulz_BattleTheme.uge','Rulz_Into the woods.uge','Rulz_Intro.uge',
               'Rulz_LightMood.uge','Tronimal_EchoExample.uge')
        paths={p.name:p for p in (REFERENCE/'appData/templates/gbs2').rglob('*.uge')}
        cases=[]
        for name in names:
            song,_=read_uge(paths[name])
            timing=walk_rows(song)
            cases.append({'song':song,'ticks':timing['ticks']+timing['cycle_ticks']})
        self.compare(cases,'five-songs')

    def test_decoder_does_not_change_driver_state(self):
        s=fixture()
        for ch in range(4):effect(s,0,ch,12,5,note=24,instrument=0)
        a,b=run([{'song':s,'ticks':16,'record_writes':True},{'song':s,'ticks':16}])
        self.assertEqual(a['rom_sha256'],b['rom_sha256'])
        self.assertEqual(canonical(a),canonical(b))
        self.assertEqual(a['trace'],b['trace'])

    def test_extended_effects_against_original_on_all_channels(self):
        cases=[]
        for code,param in ((0,0x37),(1,3),(2,5),(8,0x5a),(10,0x21),(10,0xf0),(10,0x0f)):
            s=fixture();s['ticksPerRow']=6
            for ch in range(4):
                effect(s,0,ch,None,None,36,0)
                effect(s,1,ch,code,param)
                effect(s,2,ch,code,param,40,1)
            cases.append({'song':s,'ticks':40})
        self.compare(cases,'extended-pattern-effects')

    def test_period_table_matches_source_not_octave_labels(self):
        source=REFERENCE/'appData/engine/gbvm/third-party/HUGE_TRACKER/include/hUGE_note_table.inc'
        periods=tuple(int(line.split()[1]) for line in source.read_text().splitlines() if line.startswith('dw '))
        self.assertEqual(PERIODS,periods)
        self.assertEqual(len(PERIODS),72)

    def test_write_cycle_preserves_instrument_program_and_matches_original(self):
        s=fixture()
        s['ticksPerRow']=1
        effect(s,0,0,None,None,note=36,instrument=0)
        inst=s['dutyInstruments'][0]
        inst['subpatternEnabled']=True
        for i,note in enumerate((36,48,None)):
            inst['subpattern'][i].update(note=note,jump=1 if i==2 else 0)
        regs={a:0 for a in READ_REGISTERS}
        timing=walk_rows(s)
        cycle=walk_sound(s,regs)
        self.assertGreater(cycle['ticks'],timing['ticks'])
        size=cycle['ticks']+cycle['cycle_ticks']
        orig=run([{'song':s,'ticks':size,'record_writes':True}])[0]
        # Use actual bootstrap read registers for the independent proof.
        cycle=walk_sound(s,initial(orig))
        start,length=cycle['cycle_start_tick'],cycle['cycle_ticks']
        self.assertEqual(cycle['writes'],orig['writes'][:cycle['ticks']])
        self.assertEqual(orig['writes'][start:start+length],orig['writes'][start+length:start+length*2])
        with self.assertRaisesRegex(MusicError,'exceeds 2'):walk_sound(s,regs,2)
        proof('write-cycle',{'song':s,'cycle':cycle,'original':orig})

    def test_trace_cli_read_only_and_initial_apu_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            song=root/'source.uge'
            data=binary_fixture()[0]
            song.write_bytes(data)
            apu=root/'apu.json'
            apu.write_text(json.dumps({'NR12':243,'NR22':0,'NR32':159,'NR42':0,'NR51':255}))
            proc=subprocess.run([sys.executable,'-B','-m','ngpcraft','trace-uge',str(song),
                                 '--initial-apu',str(apu),'--ticks','8'],capture_output=True,
                                timeout=15,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
            self.assertEqual(proc.returncode,0,proc.stderr)
            report=json.loads(proc.stdout)
            self.assertFalse(report['runtime_ready'])
            self.assertEqual(len(report['writes']),8)
            self.assertEqual(report['initial_apu']['NR12'],243)
            self.assertEqual(song.read_bytes(),data)
            self.assertEqual(set(root.iterdir()),{song,apu})
            for bad in ({}, {'NR12':0}, {'NR12':True,'NR22':0,'NR32':0,'NR42':0,'NR51':0}):
                apu.write_text(json.dumps(bad))
                with self.assertRaises(MusicError):read_initial_apu(apu)
            apu.write_text('{"NR12":0,"NR12":1,"NR22":0,"NR32":0,"NR42":0,"NR51":0}')
            with self.assertRaisesRegex(MusicError,'duplicate register NR12'):read_initial_apu(apu)

    def test_initial_registers_are_explicit_and_bad_effects_localized(self):
        regs={a:0 for a in READ_REGISTERS}
        with self.assertRaisesRegex(MusicError,'explicitly'):trace_sound(fixture(),1,{})
        for code,param in ((3,1),(4,2),(5,2),(7,1),(9,1)):
            s=fixture()
            effect(s,2,1,code,param)
            with self.assertRaisesRegex(MusicError,'order\[0\].channel\[1\].row\[2\]'):
                trace_sound(s,1,regs)
        # 6xy (call routine) writes no register (docs/M5_MUSIC_ROUTINE.md).
        s=fixture()
        effect(s,2,1,6,0x21)
        self.assertEqual(trace_sound(s,40,regs)['writes'],trace_sound(fixture(),40,regs)['writes'])
        s=fixture()
        effect(s,0,0,None,None,note=12,instrument=0)
        instr=s['dutyInstruments'][0]
        instr['subpatternEnabled']=True
        instr['subpattern'][0]['note']=0
        with self.assertRaisesRegex(MusicError,'transposed note'):trace_sound(s,1,regs)
