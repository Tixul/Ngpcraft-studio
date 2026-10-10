import copy,json,os,shutil,tempfile,unittest
from pathlib import Path
from ngpcraft.audio_assets import MusicError
from ngpcraft.music_hold import PROFILE,encode_events,adapter_source,adapt_driver,compile_score,export_score
from ngpcraft.music_streams import generate_music
from tools.music_hold_probe import run
from tools.music_extract import extract
from tools.music_driver_probe import driver_source
from test_music_streams import fixture,compile_fixture,OVER_REV

CLANG=os.environ.get('NGC_TEST_CLANG')

def attack(frames=1):
    return {'kind':'attack','duration_frames':frames,'note':58,'instrument':0,'attenuation':3}

def plan(events,channel=0,changes=None):
    song,bank=fixture()
    if changes:bank['instruments'][0]['def'].update(changes)
    result=compile_fixture(song,bank)
    stream,_,features=encode_events(events,channel,{58:1},{0:0},1)
    result.update(driver_profile=PROFILE,stream_extensions=features,duration_unit_frames=1)
    result['channels']=[None]*4
    result['channels'][channel]={'channel':channel,'stream':[246,1,1]+stream+[0],'loop_offset':3}
    return result


class HoldInputTests(unittest.TestCase):
    def test_long_attack_and_off_split_into_holds(self):
        stream,offsets,features=encode_events([attack(600),{'kind':'off','duration_frames':510}],0,{58:1},{0:0},1)
        self.assertEqual(stream,[244,0,240,3,1,255,254,3,255,254,3,90,255,255,254,3,255])
        self.assertEqual(offsets,[0,12])
        self.assertEqual(features,['hold'])

    def test_bad_payloads_and_names_are_not_clamped(self):
        for event,ch in (({'kind':[],'duration_frames':1},0),
                         ({'kind':'hold','duration_frames':0},0),
                         ({'kind':'hold','duration_frames':1,'attenuation':16},0),
                         ({'kind':'hold','duration_frames':1,'divider':0},0),
                         ({'kind':'hold','duration_frames':1,'divider':400},3),
                         ({'kind':'hold','duration_frames':1,'noise_note':2},0),
                         ({**attack(),'instrument':2},0),({**attack(),'note':57},0),
                         ({'kind':'hold','duration_frames':1,'unknown':0},0)):
            with self.assertRaises(MusicError):encode_events([event],ch,{58:1},{0:0},1)
        with self.assertRaises(MusicError):encode_events([attack(0xffffffff)],0,{58:1},{0:0},1)
        with self.assertRaises(MusicError):encode_events([attack()],0,{58:1},{0:128},1)
        with self.assertRaises(MusicError):encode_events([attack(3)],0,{58:1},{0:0},2)

    def test_selected_units_and_legacy_absence(self):
        source,branches=adapter_source(['hold'])
        self.assertIn('Ngc_BgmHold',source)
        for name in ('Ngc_BgmLevel','Ngc_BgmDivider','Ngc_BgmNoiseNote'):
            self.assertNotIn(name,source+branches)
        base=compile_fixture(*fixture())
        self.assertNotIn('stream_extensions.json',generate_music(base))
        if OVER_REV.is_dir():  # local Over Rev driver (NGC_TEST_OVER_REV_AUDIO)
            self.assertNotIn('Ngc_BgmHold',driver_source(OVER_REV,generate_music(base)))
        with self.assertRaises(MusicError):adapt_driver('unknown native C',['hold'])


class HeldScoreTests(unittest.TestCase):
    def score(self):
        return {'schema':'ngpcraft-held-score/1','duration_unit_frames':1,
                'notes':[{'id':58,'divider':218}],
                'channels':[{'first':[attack(2)],'repeat':[{'kind':'hold','duration_frames':4,'divider':333,'attenuation':7}]},None,None,None]}

    def test_export_preserves_repeat_hold_and_selects_only_used_bank_dependencies(self):
        _,bank=fixture()
        bank['instruments'][0]['def'].update(env_curve_id=2,pitch_curve_id=6,macro_id=3)
        bank['instruments'].append({'name':'unused','def':dict(bank['instruments'][0]['def'])})
        with tempfile.TemporaryDirectory() as temp:
            sp,bp=Path(temp)/'score.json',Path(temp)/'bank.json'
            sp.write_text(json.dumps(self.score()));bp.write_text(json.dumps(bank))
            p=compile_score(sp,bp)
            self.assertEqual(p['notes'],[{'source_note':58,'divider':218,'compiled_id':1}])
            self.assertEqual((p['intro_frames'],p['first_pass_frames'],p['repeat_frames']),(2,6,4))
            self.assertEqual(len(p['instruments']),1)
            self.assertEqual(p['tables']['pitch_curve_id'][0]['source_id'],6)
            self.assertEqual(p['channels'][0]['loop_offset'],9)
            self.assertEqual(p['channels'][0]['stream'][9:],[254,4,7,254,5,77,1,254,3,4,0])
            dest=Path(temp)/'export'
            result=export_score(sp,bp,dest)
            self.assertEqual(result['stream_extensions'],['divider','hold','level'])
            manifest=json.loads((dest/'music.json').read_text())
            for name in ('stream_extensions.inc','stream_dispatch.inc','stream_extensions.json'):
                self.assertIn(name,manifest['files_sha256'])
            self.assertNotIn('Ngc_BgmNoiseNote',(dest/'stream_extensions.inc').read_text())
            with self.assertRaises(MusicError):export_score(sp,bp,dest)

    def test_rejects_missing_pitch_duplicate_ids_bad_lengths_and_duration_quantization(self):
        _,bank=fixture()
        variants=[]
        score=self.score();score['notes']=[];variants.append(score)
        score=self.score();score['notes']*=2;variants.append(score)
        score=self.score();score['channels'][1]={'first':[attack(3)],'repeat':[attack(4)]};variants.append(score)
        score=self.score();score['duration_unit_frames']=3;variants.append(score)
        score=self.score();score['channels'][0]['repeat'][0]['divider']=1024;variants.append(score)
        score=self.score();score['channels'][0]['first'][0]['instrument']=1;variants.append(score)
        with tempfile.TemporaryDirectory() as temp:
            sp,bp=Path(temp)/'score.json',Path(temp)/'bank.json'
            bp.write_text(json.dumps(bank))
            for score in variants:
                sp.write_text(json.dumps(score))
                with self.subTest(score=score),self.assertRaises(MusicError):compile_score(sp,bp)


@unittest.skipUnless(CLANG and shutil.which('node'),'requires Clang wasm and Node')
class HoldDriverTests(unittest.TestCase):
    def test_hold_changes_level_and_divider_without_retriggering_adsr_or_vibrato(self):
        p=plan([attack(),{'kind':'hold','duration_frames':8,'attenuation':7,'divider':333}],
               changes={'adsr_on':1,'adsr_attack':10,'adsr_decay':10,'adsr_sustain':4,
                        'vib_on':1,'vib_depth':1,'vib_delay':12,'vib_speed':2})
        result=run(p,OVER_REV,8)
        a,b=result['trace'][0][0],result['trace'][1][0]
        self.assertEqual(a[1],b[1])
        self.assertEqual(b[2:7],[9,7,7,333,333])
        self.assertEqual(b[7:10],[1,9,11])
        self.assertEqual(b[16],4)
        self.assertEqual(b[17],0)
        self.assertEqual(result['trace'][7][0][9],5)
        bad=run(p,OVER_REV,8,legacy=True)
        self.assertNotEqual(bad['trace'],result['trace'])
        proof('held-update',{'plan':p,'candidate':result,'legacy':bad})

    def test_long_note_does_not_restart_effect_phase_at_255_frames(self):
        p=plan([attack(600)],changes={'adsr_on':1,'adsr_attack':30,'adsr_decay':30,
                                    'adsr_sustain':3,'adsr_release':8})
        result=run(p,OVER_REV,520)
        trace=result['trace']
        self.assertEqual(trace[254][0][7],trace[255][0][7])
        self.assertEqual(trace[255][0][2],510)
        self.assertEqual(trace[510][0][2],600)
        self.assertEqual(trace[255][0][16],4)
        proof('long-note',{'plan':p,'candidate':result})

    def test_noise_update_changes_setting_without_note_reset(self):
        events=[{**attack(),'note':1},{'kind':'hold','duration_frames':8,'noise_note':6,'attenuation':9}]
        p=plan(events,3)
        result=run(p,OVER_REV,8)
        row=result['trace'][1][3]
        self.assertEqual(row[1],6)
        self.assertEqual(row[13:16],[0xe5,0xe5,0xf9])
        self.assertEqual(row[16],4)

    def test_hold_across_loop_keeps_note_and_effect_phase(self):
        p=plan([attack(),{'kind':'hold','duration_frames':5}],changes={'vib_on':1,'vib_depth':1,'vib_delay':12,'vib_speed':2})
        p['channels'][0]['loop_offset']=9
        result=run(p,OVER_REV,14)
        self.assertEqual(result['trace'][6][0][2],11)
        self.assertEqual(result['trace'][6][0][9],6)
        self.assertEqual(result['trace'][11][0][2],16)
        self.assertEqual(result['trace'][11][0][9],1)

    def test_long_rest_does_not_restart_adsr_release(self):
        p=plan([attack(),{'kind':'off','duration_frames':600}],
               changes={'adsr_on':1,'adsr_attack':1,'adsr_release':255})
        result=run(p,OVER_REV,520)
        self.assertEqual(result['trace'][256][0][8],0)
        self.assertEqual(result['trace'][257][0][7],0)
        self.assertEqual(result['trace'][300][0][0],0)

    def test_invalid_runtime_wait_faults_and_stops(self):
        p=plan([attack(),{'kind':'hold','duration_frames':8}])
        p['channels'][0]['stream'][-2]=0
        result=run(p,OVER_REV,3)
        self.assertEqual(result['trace'][1][0][0],0)
        self.assertEqual(result['trace'][1][0][17],1)

    def test_dependency_selection_keeps_only_requested_extension(self):
        p=plan([attack(),{'kind':'hold','duration_frames':8}])
        code,report=extract(p,OVER_REV,['Sounds_Init','ngc_audio_service','ngc_music_play','ngc_music_stop'])
        self.assertIn('Ngc_BgmHold',report['selected'])
        for name in ('Ngc_BgmLevel','Ngc_BgmDivider','Ngc_BgmNoiseNote'):
            self.assertNotIn(name,code)
        files=generate_music(p)
        manifest=json.loads(files['music.json'])
        self.assertIn('stream_extensions.json',manifest['files_sha256'])


def proof(name,data):
    folder=os.environ.get('NGC_TEST_ARTIFACTS')
    if folder:
        root=Path(folder)/'music-hold'
        root.mkdir(parents=True,exist_ok=True)
        (root/(name+'.json')).write_text(json.dumps(data,indent=2),encoding='utf8')


@unittest.skipUnless(CLANG and shutil.which('node') and all(os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')),'requires native toolchain and emulator')
class HeldRomTests(unittest.TestCase):
    def test_native_psg_hold_updates_sfx_stop_and_restart(self):
        from tools.music_rom import build,observe
        p=plan([attack(2),{'kind':'hold','duration_frames':3,'attenuation':7,'divider':333},
                {'kind':'off','duration_frames':2},attack(3),{'kind':'hold','duration_frames':4,'attenuation':2,'divider':210}])
        with tempfile.TemporaryDirectory(prefix='ngc_held_rom_') as temp:
            dest=Path(temp)/'rom'
            build(dest,os.environ['NGC_TEST_TOSHIBA'],OVER_REV,phase_frames=16,music_plan=p)
            result=observe(dest,os.environ['NGC_TEST_EMULATOR'],os.environ['NGC_TEST_BIOS'],OVER_REV)
            self.assertTrue(result['correct'],(result['actual_bytes'],result['expected_bytes']))
            self.assertGreater(result['z80_executed'],1000)
            proof('native-rom',result)
