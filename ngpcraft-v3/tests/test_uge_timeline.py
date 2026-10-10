"""Logical clock parity with actual GBVM ASM in RGBDS/Binjgb, plus APU cases."""
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
from ngpcraft.uge import parse_uge, read_uge
from ngpcraft.uge_timeline import FIELDS, trace_clock, walk_rows
from tools.gbstudio_ts import OracleError
from tools.reference import REFERENCE
from tools.uge_playback_oracle import assembly, fingerprints, run
from test_uge import binary_fixture


def fixture():
    song = parse_uge(binary_fixture()[0])
    for pattern in song['patterns']:
        for cell in pattern:
            cell.update(note=None, instrument=None, effectCode=None, effectParam=None)
    return song


def effect(song, row, channel, code, param, note=None, instrument=None):
    song['patterns'][channel][row].update(note=note,instrument=instrument,
                                         effectCode=code,effectParam=param)


def clock(trace):
    return [{f:state[f] for f in FIELDS} for state in trace]


def proof(name, data):
    dest=os.environ.get('NGC_TEST_ARTIFACTS')
    if dest:
        root=Path(dest)/'uge-ticks'
        root.mkdir(parents=True,exist_ok=True)
        with gzip.open(root/(name+'.json.gz'),'xb') as stream:
            stream.write(json.dumps(data).encode('utf8'))


class ClockTests(unittest.TestCase):
    def test_cli_clock_is_read_only_and_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.uge'
            data=binary_fixture()[0]
            path.write_bytes(data)
            result=subprocess.run([sys.executable,'-B','-m','ngpcraft','inspect-uge',str(path),'--clock'],
                                  capture_output=True,timeout=15,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads(result.stdout)
            self.assertFalse(report['runtime_ready'])
            self.assertNotIn('song',report)
            self.assertEqual(report['clock']['unit'],'logical_huge_tick')
            self.assertEqual(report['clock']['cycle_ticks'],384)
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual(list(Path(tmp).iterdir()),[path])

    def test_original_driver_control_boundaries_and_priority(self):
        cases=[]
        for speed in (0,1,2,255):
            s=fixture()
            effect(s,0,0,15,speed)
            cases.append({'song':s,'ticks':520})
        # Later channels win; B and D combine, D00 can cancel pending break.
        for codes in (((11,2),(13,8)), ((13,8),(11,2)), ((11,2),(13,0))):
            s=fixture()
            s['sequence']*=3
            s['ticksPerRow']=2
            for ch,(code,param) in enumerate(codes):effect(s,0,ch,code,param)
            cases.append({'song':s,'ticks':270})
        s=fixture()
        effect(s,0,0,15,2)
        effect(s,0,3,15,7)
        cases.append({'song':s,'ticks':130})
        result=run(cases)
        for i,(case,original) in enumerate(zip(cases,result)):
            with self.subTest(i=i):
                self.assertEqual(trace_clock(case['song'],case['ticks']),clock(original['trace']))
        self.assertEqual(result[0]['trace'][255]['tick'],255)
        self.assertEqual((result[0]['trace'][256]['row'],result[0]['trace'][256]['tick']),(1,0))
        self.assertEqual((result[4]['trace'][2]['current_order'],result[4]['trace'][2]['row']),(2,7))
        self.assertEqual((result[6]['trace'][2]['current_order'],result[6]['trace'][2]['row']),(0,1))
        self.assertEqual(result[7]['trace'][1]['ticks_per_row'],7)
        proof('boundaries',{'cases':cases,'original':result,'fingerprints':fingerprints()})

    def test_seeded_controls_against_original(self):
        rng=random.Random(43202)
        cases=[]
        for _ in range(24):
            s=fixture()
            s['sequence']*=rng.randint(1,4)
            s['ticksPerRow']=rng.choice((1,2,6,255,0))
            for row in range(64):
                for ch in range(4):
                    code=rng.choice((None,None,None,11,13,15))
                    if code is None:continue
                    limit= len(s['sequence']) if code==11 else 64 if code==13 else 255
                    effect(s,row,ch,code,rng.randint(0,limit))
            cases.append({'song':s,'ticks':800})
        result=run(cases)
        for i,(case,original) in enumerate(zip(cases,result)):
            with self.subTest(i=i):
                self.assertEqual(trace_clock(case['song'],case['ticks']),clock(original['trace']))
        proof('seeded-controls',{'cases':cases,'original':result})

    def test_twelve_real_songs_one_complete_clock_cycle(self):
        files=sorted((REFERENCE/'appData/templates/gbs2').rglob('*.uge'))
        self.assertEqual(len(files),12)
        cases,reports=[],[]
        for f in files:
            song,sha=read_uge(f)
            report=walk_rows(song)
            reports.append({'file':f.name,'source_sha256':sha,'clock':report})
            cases.append({'song':song,'ticks':report['ticks']+1})
        originals=run(cases)
        for case,original,report in zip(cases,originals,reports):
            with self.subTest(file=report['file']):
                self.assertEqual(trace_clock(case['song'],case['ticks']),clock(original['trace']))
        proof('gbs2',{'reports':reports,'original':originals,'fingerprints':fingerprints()})

    def test_refusals_are_localized_and_cycle_is_bounded(self):
        for code,param,text in ((11,2,'B02'),(13,65,'D41')):
            s=fixture()
            effect(s,4,2,code,param)
            with self.assertRaisesRegex(MusicError,'order\[0\].channel\[2\].row\[4\].*'+text):trace_clock(s,1)
        # 6xy leaves the clock alone: the conversion queues it for GBVM
        # (uge_convert.routine_calls, docs/M5_MUSIC_ROUTINE.md).
        s=fixture()
        effect(s,4,2,6,0x21)
        self.assertEqual(trace_clock(s,8),trace_clock(fixture(),8))
        s=fixture()
        effect(s,0,0,None,None,note=12,instrument=0)
        instr=s['dutyInstruments'][0]
        instr['subpatternEnabled']=True
        instr['subpattern'][2].update(effectCode=15,effectParam=3)
        with self.assertRaisesRegex(MusicError,'subpattern\[2\]'):walk_rows(s)
        with self.assertRaisesRegex(MusicError,'exceeds 3'):walk_rows(fixture(),3)
        for bad in (-1,65537,True,1.5):
            with self.assertRaises(MusicError):trace_clock(fixture(),bad)

    def test_oracle_refuses_callbacks_and_unrecognized_c(self):
        s=fixture()
        effect(s,0,0,6,0)
        with self.assertRaisesRegex(OracleError,'callback'):run([{'song':s,'ticks':1}])
        with self.assertRaises(OracleError):assembly('some changed original C layout')


class OriginalSoundCases(unittest.TestCase):
    """Characterize real driver; these cases do not claim a ported synthesizer."""
    @classmethod
    def setUpClass(cls):
        cls.cases=[]
        # Cxy on effect-only rows preserves note/period; instrument-only cell
        # does not reload instrument. E is tick-relative, not row-relative.
        s=fixture()
        s['ticksPerRow']=4
        s['dutyInstruments'][1]['volumeSweepChange']=2
        s['noiseInstruments'][1]['volumeSweepChange']=2
        for ch in range(4):
            effect(s,0,ch,None,None,note=24,instrument=0)
            effect(s,1,ch,12,5,instrument=1)
            effect(s,2,ch,14,2)
        cls.cases.append({'song':s,'ticks':12})
        # E00 with simultaneous attack still cuts DAC volume.
        s=fixture()
        for ch in range(4):effect(s,0,ch,14,0,note=24,instrument=0)
        cls.cases.append({'song':s,'ticks':1})
        # An unreachable cut does not cut on a later row.
        s=fixture()
        s['ticksPerRow']=2
        for ch in range(4):effect(s,0,ch,14,3,note=24,instrument=0)
        cls.cases.append({'song':s,'ticks':5})
        # Wave C is quantized into four hardware levels.
        s=fixture()
        s['ticksPerRow']=1
        for row,value in enumerate((0,1,4,5,9,10,15)):
            effect(s,row,2,12,value,note=24 if row==0 else None,instrument=0 if row==0 else None)
        cls.cases.append({'song':s,'ticks':7})
        # Noise instrument subpattern executes even on held/empty rows.
        s=fixture()
        s['ticksPerRow']=4
        effect(s,0,3,None,None,note=24,instrument=0)
        instr=s['noiseInstruments'][0]
        instr['subpatternEnabled']=True
        instr['subpattern'][0]['note']=36
        instr['subpattern'][1]['note']=48
        instr['subpattern'][2]['jump']=1
        cls.cases.append({'song':s,'ticks':8})
        cls.originals=run(cls.cases)
        proof('sound-characterization',{'cases':cls.cases,'original':cls.originals})

    def test_volume_on_held_notes_and_delayed_cut_all_channels(self):
        trace=self.originals[0]['trace']
        for ch in range(4):
            self.assertEqual(trace[1]['channels'][ch][:5],trace[5]['channels'][ch][:5])
            self.assertEqual(trace[5]['apu'][2+ch*5],0x40|0x9f if ch==2 else 0x50)
            self.assertEqual(trace[10]['apu'][2+ch*5],trace[9]['apu'][2+ch*5])
            self.assertEqual(trace[11]['apu'][2+ch*5],0x9f if ch==2 else 0)

    def test_zero_cut_and_cut_outside_row(self):
        for ch in range(4):
            self.assertEqual(self.originals[1]['trace'][1]['apu'][2+ch*5],0x9f if ch==2 else 0)
            self.assertNotEqual(self.originals[2]['trace'][5]['apu'][2+ch*5],0x9f if ch==2 else 0)

    def test_wave_quantization(self):
        actual=[s['apu'][12]&0x60 for s in self.originals[3]['trace'][1:]]
        self.assertEqual(actual,[0,0x60,0x60,0x40,0x40,0x20,0x20])

    def test_noise_subpattern_runs_on_each_tick(self):
        states=[s['channels'][3][11] for s in self.originals[4]['trace'][1:]]
        self.assertEqual(states,[1,2,0,1,2,0,1,2])
        polynomials=[s['apu'][18] for s in self.originals[4]['trace'][1:]]
        self.assertNotEqual(polynomials[0],polynomials[1])
        self.assertEqual(polynomials[0],polynomials[3])
