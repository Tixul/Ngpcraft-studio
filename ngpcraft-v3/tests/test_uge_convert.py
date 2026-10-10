import copy,gzip,json,os,shutil,tempfile,unittest
from pathlib import Path

from ngpcraft.audio_assets import MusicError,read_song
from ngpcraft.audio_resolve import resolve_song
from ngpcraft.music_hold import compile_score
from ngpcraft.uge import read_uge
from ngpcraft.uge_convert import POLICY,read_policy,tone_note,volume_attenuation,convert,export_conversion
from ngpcraft.uge_sound import READ_REGISTERS,trace_sound
from tools.reference import REFERENCE
from tools.uge_playback_oracle import run as original_run,fingerprints
from tools.music_hold_probe import run as native_run
from test_music_streams import OVER_REV

SOURCE=REFERENCE/'appData/templates/gbs2/assets/music'
INITIAL={'NR12':243,'NR22':0,'NR32':159,'NR42':0,'NR51':255}
CASES=(('Rulz_Into the woods.uge',[0,0,12],16,1440,13),('Rulz_Intro.uge',[0,0,0],48,448,5))
CLANG=os.environ.get('NGC_TEST_CLANG')


def policy(transpose):return {**POLICY,'transpose_semitones':transpose,'noise_setting':5}


def inputs(temp,transpose):
    apu,pp=Path(temp)/'apu.json',Path(temp)/'policy.json'
    apu.write_text(json.dumps(INITIAL));pp.write_text(json.dumps(policy(transpose)))
    return apu,pp


class ConversionInputTests(unittest.TestCase):
    def test_linear_source_levels_use_logarithmic_psg_steps(self):
        self.assertEqual([volume_attenuation(0,v<<4) for v in range(16)],
                         [None,12,9,7,6,5,4,3,3,2,2,1,1,1,0,0])
        self.assertEqual([volume_attenuation(2,v<<5) for v in range(4)],[None,0,3,6])

    def test_policy_requires_all_losses_and_exact_types(self):
        with tempfile.TemporaryDirectory() as temp:
            _,pp=inputs(temp,[0,0,0])
            self.assertEqual(read_policy(pp)[0],policy([0,0,0]))
            for key,value in (('noise_setting',4),('noise_setting',True),('source_huge_hz',64.0),
                              ('transpose_semitones',[0,0]),('wave','preserve'),('unknown',0)):
                p=policy([0,0,0]);p[key]=value;pp.write_text(json.dumps(p))
                with self.subTest(key=key,value=value),self.assertRaises(MusicError):read_policy(pp)
            p=policy([0,0,0]);del p['intra_tick'];pp.write_text(json.dumps(p))
            with self.assertRaises(MusicError):read_policy(pp)

    def test_wave_frequency_and_explicit_octave_avoid_silent_psg_clamp(self):
        with self.assertRaises(MusicError):tone_note(1046,2,0)
        a=tone_note(1046,0,0);b=tone_note(1046,2,12)
        self.assertEqual(a[:2],b[:2])
        self.assertEqual(a[2]['target_hz'],b[2]['target_hz'])
        self.assertEqual(a[2]['source_hz'],2*b[2]['source_hz'])

    def test_two_song_documents_banks_loops_and_manifest(self):
        for name,transpose,prefix,cycle,patterns in CASES:
            with self.subTest(song=name),tempfile.TemporaryDirectory() as temp:
                apu,pp=inputs(temp,transpose)
                dest=Path(temp)/'out'
                export_conversion(SOURCE/name,apu,pp,dest)
                song,_=read_song(dest/'song.ngps')
                resolved=resolve_song(dest/'song.ngps',dest/'bank.json','sound-creator')
                plan=compile_score(dest/'held-score.json',dest/'bank.json')
                self.assertEqual(len(song['patterns']),patterns)
                self.assertEqual((resolved['first_pass']['ticks'],resolved['repeat_loop']['ticks']),(prefix+cycle,cycle))
                self.assertEqual((plan['intro_frames'],plan['repeat_frames']),(prefix,cycle))
                report=json.loads((dest/'loss-report.json').read_text())
                self.assertTrue(report['mapped_two_cycles_equal'])
                self.assertFalse(report['sound_creator_listened'])
                self.assertAlmostEqual(report['cadence']['duration_stretch_percent'],6.7552083333)
                self.assertTrue(report['intra_tick_trigger_collapses'])
                if name==CASES[0][0]:self.assertIsNone(plan['channels'][3])
                else:self.assertTrue(report['noise_substitutions'])
                import hashlib
                for filename,digest in json.loads((dest/'manifest.json').read_text())['files_sha256'].items():
                    self.assertEqual(hashlib.sha256((dest/filename).read_bytes()).hexdigest(),digest)
                with self.assertRaises(MusicError):export_conversion(SOURCE/name,apu,pp,dest)

    def test_range_rejection_leaves_destination_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            apu,pp=inputs(temp,[0,0,0]);dest=Path(temp)/'out'
            with self.assertRaises(MusicError):export_conversion(SOURCE/CASES[0][0],apu,pp,dest)
            self.assertFalse(dest.exists())


@unittest.skipUnless(CLANG and shutil.which('node'),'requires native C wasm and original GBVM oracle')
class ConversionOracleTests(unittest.TestCase):
    def test_native_c_levels_pitch_and_silences_match_original_asm_mapped_over_two_cycles(self):
        # The expected trace is built from original SM83 register writes and
        # channel RAM, not the converter's held score or C stream.
        cases=[]
        for name,transpose,prefix,cycle,_ in CASES:
            song,_=read_uge(SOURCE/name)
            cases.append({'song':song,'ticks':prefix+2*cycle,'record_writes':True})
        originals=original_run(cases)
        for case,original,description in zip(cases,originals,CASES):
            name,transpose,prefix,cycle,_=description
            with self.subTest(song=name),tempfile.TemporaryDirectory() as temp:
                apu,pp=inputs(temp,transpose)
                dest=Path(temp)/'out';export_conversion(SOURCE/name,apu,pp,dest)
                p=compile_score(dest/'held-score.json',dest/'bank.json')
                native=native_run(p,OVER_REV,case['ticks'])
                regs={a:original['trace'][0]['apu'][a-0xff10] for a in READ_REGISTERS}
                self.assertEqual(regs,dict(zip(READ_REGISTERS,INITIAL.values())))
                model=trace_sound(case['song'],case['ticks'],regs)
                self.assertEqual(model['writes'],original['writes'])
                started=[False]*4
                for tick,writes in enumerate(original['writes']):
                    for a,v in writes:
                        regs[a]=v
                        for ch,high in enumerate((0xff14,0xff19,0xff1e,0xff23)):
                            if a==high and v&128:started[ch]=True
                    for ch in range(4):
                        v=regs[(0xff12,0xff17,0xff1c,0xff21)[ch]]
                        att=(None,0,3,6)[(v>>5)&3] if ch==2 else (None,12,9,7,6,5,4,3,3,2,2,1,1,1,0,0)[v>>4]
                        if not regs[0xff25]&((1<<ch)|(16<<ch)):att=None
                        actual=native['trace'][tick][ch]
                        audible=started[ch] and att is not None
                        self.assertEqual(bool(actual[0]),audible,(name,tick,ch,'activity'))
                        self.assertEqual(actual[17],0,(name,tick,ch,'fault'))
                        if audible:
                            self.assertEqual(actual[4],att,(name,tick,ch,'level'))
                            if ch<3:
                                raw=original['trace'][tick+1]['channels'][ch]
                                period=raw[0]|raw[1]<<8
                                self.assertEqual(actual[5],tone_note(period,ch,transpose[ch])[1],(name,tick,ch,'divider'))
                            else:self.assertEqual(actual[1],5,(name,tick,ch,'noise preset'))
                folder=os.environ.get('NGC_TEST_ARTIFACTS')
                if folder:
                    root=Path(folder)/'uge-convert';root.mkdir(parents=True,exist_ok=True)
                    with gzip.open(root/(name+'.json.gz'),'xb') as stream:
                        stream.write(json.dumps({'original':original,'native':native,'fingerprints':fingerprints(),
                                                 'source_sha256':json.loads((dest/'loss-report.json').read_text())['source_sha256'],
                                                 'frames_compared':case['ticks'],'channels_per_frame':4}).encode())
