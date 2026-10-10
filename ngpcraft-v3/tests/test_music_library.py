import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.audio_assets import MusicError
from ngpcraft.music_hold import compile_score
from ngpcraft.music_library import compile_library, export_library, generate_library, merge_plans, select_runtime, _stream_remap
from ngpcraft.music_streams import FIELDS, compile_music
from ngpcraft.music_streams import _compile_instruments
from ngpcraft.project import _atomic_write
from ngpcraft.runtime_units import linked_objects, public_symbols
from tools.music_extract import select_c
from tools.music_library_probe import run_library
from tools.music_library_rom import build_library_rom, observe_library_rom
from test_music_streams import fixture, note, compile_fixture, OVER_REV


def library_fixture(root):
    song, bank = fixture()
    bank['instruments'][0]['def'].update(env_on=1, env_curve_id=5, pitch_curve_id=8, macro_id=4)
    bank['instruments'].append({'name': 'Second', 'def': {**bank['instruments'][0]['def'], 'attn':7,
                                                      'env_curve_id':1, 'pitch_curve_id':2, 'macro_id':1}})
    song['patterns'][0]['channels'][0][4] = note(62, i=1)
    song['patterns'][0]['channels'][1][0] = note(46, i=1)
    song['patterns'][0]['channels'][3][0] = note(5, i=1)
    second_bank = copy.deepcopy(bank)
    second_bank['instruments'][0]['def'].update(env_curve_id=1, pitch_curve_id=2, macro_id=1, attn=3)
    second_bank['instruments'][1]['def'].update(env_on=0, env_curve_id=0, pitch_curve_id=0, macro_id=0, attn=5)
    score = {'schema':'ngpcraft-held-score/1', 'duration_unit_frames':1,
             'notes':[{'id':1,'divider':300}, {'id':2,'divider':244}], 'channels':[
        {'first':[{'kind':'attack','duration_frames':8,'note':1,'instrument':0,'attenuation':3},
                  {'kind':'hold','duration_frames':8,'attenuation':6,'divider':244},
                  {'kind':'off','duration_frames':8}],
         'repeat':[{'kind':'attack','duration_frames':8,'note':2,'instrument':0,'attenuation':3},
                   {'kind':'hold','duration_frames':16,'divider':488}, {'kind':'off','duration_frames':8}]},
        None, None,
        {'first':[{'kind':'attack','duration_frames':8,'note':5,'instrument':1,'attenuation':5},
                  {'kind':'hold','duration_frames':8,'noise_note':6}, {'kind':'off','duration_frames':8}],
         'repeat':[{'kind':'attack','duration_frames':8,'note':7,'instrument':1,'attenuation':5},
                   {'kind':'hold','duration_frames':16,'noise_note':5}, {'kind':'off','duration_frames':8}]}]}
    sources = {'song.ngps':song, 'bank.json':bank, 'score.json':score, 'second-bank.json':second_bank}
    for name, value in sources.items():
        _atomic_write(root/name, json.dumps(value))
    manifest = {'schema':'ngpcraft-music-library/1','tracks':[
        {'id':'Theme','format':'ngps','song':'song.ngps','bank':'bank.json','note_encoding':'sound-creator','tick_clock':'vblank'},
        {'id':'Held','format':'held-score','score':'score.json','bank':'second-bank.json'}]}
    _atomic_write(root/'library.json', json.dumps(manifest))
    return compile_library(root/'library.json')


class LibraryTests(unittest.TestCase):
    def test_merge_uses_source_factory_ids_not_dense_local_aliases(self):
        a, bank = fixture()
        bank['instruments'][0]['def'].update(env_on=1, env_curve_id=5, pitch_curve_id=8, macro_id=4)
        first = compile_fixture(a, bank)
        bank['instruments'][0]['def'].update(env_curve_id=1, pitch_curve_id=2, macro_id=1)
        second = compile_fixture(a, bank)
        self.assertEqual(first['instruments'][0]['def']['env_curve_id'], 1)
        self.assertEqual(second['instruments'][0]['def']['env_curve_id'], 1)
        original = copy.deepcopy((first, second))
        merged = merge_plans([('A',first),('B',second),('A_again',first)])
        self.assertEqual((first,second), original)
        self.assertEqual(len(merged['instruments']), 2)
        self.assertEqual([item['source_id'] for item in merged['tables']['env_curve_id']], [1,5])
        self.assertEqual([track['instrument_remap'][0]['library_id'] for track in merged['tracks']], [0,1,0])
        self.assertEqual(merged['tracks'][1]['plan']['channels'][0]['stream'][4], 1)
        for track, source in zip(merged['tracks'], (first,second,first)):
            self.assertEqual(track['plan']['notes'], source['notes'])
            self.assertEqual(track['plan']['channels'][0]['loop_offset'], source['channels'][0]['loop_offset'])

    def test_remap_never_interprets_payload_f4_as_instrument_command(self):
        stream = [246,1,244,244,0,240,4,1,244,254,5,244,1,254,3,244,255,244,0]
        self.assertEqual(_stream_remap(stream,{0:7}), [246,1,244,244,7,240,4,1,244,254,5,244,1,254,3,244,255,244,0])
        # FE 07 is the routine call (docs/M5_MUSIC_ROUTINE.md); FE 08 is unknown.
        for invalid in ([244,0], [254,8,1,0], [1,1], [0,0], [244,3,0]):
            with self.subTest(invalid=invalid), self.assertRaises(MusicError):
                _stream_remap(invalid,{0:7})

    def test_capacity_and_ids_fail_closed(self):
        song, bank = fixture()
        plan = compile_fixture(song,bank)
        for names in ([], [('A',plan),('A',plan)], [('Bad-name',plan)], [('4name',plan)]):
            with self.subTest(names=[n for n,_ in names]), self.assertRaises(MusicError):
                merge_plans(names)
        many = []
        for i in range(129):
            p = copy.deepcopy(plan)
            p['instruments'][0]['def']['adsr_attack'] = i
            many.append((f'Track{i}',p))
        self.assertEqual(len(merge_plans(many[:128])['instruments']),128)
        with self.assertRaisesRegex(MusicError,'128 distinct'):
            merge_plans(many)

    def test_export_selects_only_used_tables_extensions_and_apis(self):
        song, bank = fixture()
        files = generate_library(merge_plans([('A',compile_fixture(song,bank))]))
        selection = json.loads(files['audio-selection.json'])
        self.assertEqual(selection['variant'],'0:0')
        for name in ('Sfx_PlayToneCh','Sfx_PlayNoiseEx','Bgm_DebugSnapshot','Bgm_StartLoop3',
                     's_bgm_env_curves','s_bgm_pitch_curves','s_bgm_macros','Ngc_BgmHold','Ngc_BgmLevel'):
            self.assertNotIn(name+'(',files['audio.c'])
            self.assertNotIn(name+'[',files['audio.c'])
        self.assertIn('ngc_sound_staging', selection['selected'])
        for name in selection['mutable_state']:
            self.assertIn(f'&{name}, sizeof({name})',files['audio.c'])
        self.assertNotIn('static const u8 *s_bgm_note_table =', files['audio.c'])
        manifest = json.loads(files['music-library.json'])
        for name, digest in manifest['files_sha256'].items():
            self.assertEqual(hashlib.sha256(files[name].encode()).hexdigest(),digest)
        self.assertEqual(linked_objects(['native_audio']), {'audio.rel','game.rel','startup.rel','cart.rel'})
        self.assertNotIn('_ngc_audio_service',public_symbols([]))

    def test_manifest_closed_paths_duplicate_keys_and_new_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library_fixture(root)
            manifest = json.loads((root/'library.json').read_text())
            for mutate in (lambda x:x.update(extra=True), lambda x:x['tracks'][0].update(song='../escape.ngps'),
                           lambda x:x['tracks'][0].update(format='uge'), lambda x:x['tracks'][0].update(tick_clock='64hz'),
                           lambda x:x['tracks'][1].update(note_encoding='midi')):
                bad=copy.deepcopy(manifest);mutate(bad)
                _atomic_write(root/'bad.json',json.dumps(bad))
                with self.assertRaises(MusicError):compile_library(root/'bad.json')
            _atomic_write(root/'bad.json','{"schema":"ngpcraft-music-library/1","schema":"x","tracks":[]}')
            with self.assertRaises(MusicError):compile_library(root/'bad.json')
            result = export_library(root/'library.json',root/'out')
            self.assertEqual(result['tracks'],2)
            with self.assertRaises(MusicError):export_library(root/'library.json',root/'out')

    def test_product_export_does_not_import_tools_or_run_external_compilers(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);library_fixture(root)
            script='''import sys, json
import subprocess
class BlockTools:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tools' or fullname.startswith('tools.'):
            raise AssertionError('laboratory import ' + fullname)
sys.meta_path.insert(0, BlockTools())
def reject(*args, **kwargs): raise AssertionError('external process at export')
subprocess.run = reject
subprocess.Popen = reject
from ngpcraft.music_library import export_library
print(json.dumps(export_library(sys.argv[1],sys.argv[2])))
'''
            proc=subprocess.run([sys.executable,'-B','-c',script,str(root/'library.json'),str(root/'out')],
                                capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)


@unittest.skipUnless(os.environ.get('NGC_TEST_CLANG') and shutil.which('node') and OVER_REV.is_dir(),
                     'requires Clang, Node and original Over Rev')
class LibraryHostTests(unittest.TestCase):
    def test_all_72_closed_variants_compile_with_exact_selected_extensions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            base=library_fixture(root)
            for factory in range(8):
                for updates in range(9):
                    library=copy.deepcopy(base)
                    definition=dict.fromkeys(FIELDS,0)
                    definition.update(attn=3,env_step=1,env_speed=1,vib_speed=1,sweep_speed=1,sweep_end=1)
                    for bit,field in enumerate(('env_curve_id','pitch_curve_id','macro_id')):
                        definition[field]=1 if factory & (1<<bit) else 0
                    library['instruments']=[{'source_id':0,'def':definition}]
                    library['tables']=_compile_instruments(library['instruments'])
                    library['stream_extensions']=[] if not updates else ['hold']+[
                        name for bit,name in enumerate(('level','divider','noise_note')) if (updates-1) & (1<<bit)]
                    files=generate_library(library)
                    selection=json.loads(files['audio-selection.json'])
                    self.assertEqual(selection['variant'],f'{factory}:{updates}')
                    for feature,name in (('hold','Ngc_BgmHold'),('level','Ngc_BgmLevel'),
                                         ('divider','Ngc_BgmDivider'),('noise_note','Ngc_BgmNoiseNote')):
                        self.assertEqual(name in selection['selected'],feature in library['stream_extensions'])
                    _atomic_write(root/'audio.c',files['audio.c']);_atomic_write(root/'audio.h',files['audio.h'])
                    proc=subprocess.run([os.environ['NGC_TEST_CLANG'],'--target=wasm32','-std=c89',
                                         '-pedantic-errors','-fsyntax-only',str(root/'audio.c')],
                                        capture_output=True,text=True,timeout=60)
                    self.assertEqual(proc.returncode,0,f'{factory}:{updates}: {proc.stderr}')

    def test_switch_repeated_request_cancel_pending_restart_and_wrap_match_over_rev(self):
        with tempfile.TemporaryDirectory() as temp:
            library=library_fixture(Path(temp))
        actions=[[0,'play',0],[40,'service'],[41,'service'],[41,'play',0],[41,'service']]
        actions += [[frame,'service'] for frame in range(42,100)]
        actions += [[100,'play',1],[100,'play',0],[101,'service'],[102,'service'],
                    [103,'play',1],[103,'stop'],[104,'service'],[105,'play',1]]
        actions += [[frame,'service'] for frame in range(106,280)]
        actions += [[280,'stop'],[281,'play',1],[282,'service'],[283,'service']]
        exported=run_library(library,OVER_REV,actions)
        reference=run_library(library,OVER_REV,actions,reference=True)
        self.assertEqual(exported['trace'],reference['trace'])
        self.assertEqual(exported['trace'][1]['state'][0],0)
        self.assertEqual(exported['trace'][2]['state'][0],1)
        self.assertEqual(exported['trace'][2]['state'],exported['trace'][3]['state'])
        for row in exported['trace']:self.assertEqual(row['state'][6:],[0,0])

    def test_exported_module_has_exact_declared_public_api_and_no_dead_functions(self):
        with tempfile.TemporaryDirectory() as temp:
            library=library_fixture(Path(temp))
        files=generate_library(library)
        code=files['audio.c'].replace('#include "audio.h"','')
        # Resolve the count macro before feeding already-preprocessed C selector.
        code=re.sub(r'#define NGC_MUSIC_TRACK_COUNT (\d+)u',r'',code)
        code=code.replace('NGC_MUSIC_TRACK_COUNT','2u')
        selected, report=select_c(code,['ngc_audio_init','ngc_music_play_track','ngc_music_stop_track','ngc_audio_service'],
                                  os.environ['NGC_TEST_CLANG'])
        emitted=set(re.findall(r'^(?:static )?(?:void|u8|s8|u16|s16|u32|s32) (\w+)\([^;]*?\)\s*\{',code,re.M))
        self.assertTrue(emitted <= set(report['selected']), sorted(emitted-set(report['selected'])))
        self.assertTrue({'Ngc_BgmHold','Ngc_BgmLevel','Ngc_BgmDivider','Ngc_BgmNoiseNote'} <= set(report['selected']))
        self.assertEqual(set(re.findall(r'^void (\w+)\([^;]*?\)\s*\{',code,re.M)),
                         {'ngc_audio_init','ngc_music_play_track','ngc_music_stop_track','ngc_audio_service'})

    def test_legacy_and_hold_only_profiles_compile_and_execute(self):
        song, bank=fixture();legacy=compile_fixture(song,bank)
        with tempfile.TemporaryDirectory() as temp:
            library=library_fixture(Path(temp))
        held=copy.deepcopy(library['tracks'][1]['plan'])
        # Fixture that actually uses a long reset-free hold, with F4 duration.
        held['channels'][0]['stream']=[246,1,1,244,0,240,3,1,8,254,3,244,0]
        held['channels'][0]['loop_offset']=3;held['channels'][3]=None
        held['stream_extensions']=['hold']
        for plans in ([('A',legacy)],[('A',held)]):
            p=merge_plans(plans)
            actions=[[0,'play',0]]+[[frame,'service'] for frame in range(1,520)]
            x=run_library(p,OVER_REV,actions);y=run_library(p,OVER_REV,actions,True)
            self.assertEqual(x['trace'],y['trace'])


@unittest.skipUnless(all(os.environ.get(k) for k in ('NGC_TEST_CLANG','NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS'))
                     and shutil.which('node'), 'requires Clang, Node, Toshiba, native emulator and BIOS')
class LibraryRomTests(unittest.TestCase):
    def test_exact_exported_module_runs_two_tracks_on_real_z80(self):
        with tempfile.TemporaryDirectory(prefix='ngc_library_rom_') as temp:
            root=Path(temp);library=library_fixture(root)
            build_library_rom(library,root/'rom',os.environ['NGC_TEST_TOSHIBA'])
            result=observe_library_rom(root/'rom',os.environ['NGC_TEST_EMULATOR'],os.environ['NGC_TEST_BIOS'],OVER_REV)
            self.assertTrue(result['correct'], (result['actual_bytes'][:48],result['expected_bytes'][:48]))
            self.assertGreater(result['z80_executed'],1000)
