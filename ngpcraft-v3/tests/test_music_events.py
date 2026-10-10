import copy
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.assembler import assemble
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import compile_entity_events
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project, write_project, _atomic_write
from ngpcraft.toolchain import build_rom
from tools.demo_scene_switch import derive_scenes, set_script, switch, num, ev
from tools.reference import REFERENCE
from test_music_library import library_fixture
from test_music_streams import OVER_REV

GBS2=REFERENCE/'appData/templates/gbs2'


def project_fixture(root,rate=60,text=False):
    folders,ids=derive_scenes(GBS2,root/'project',('sample_town','cave'),'sample_town',(12,20))
    project=root/'project'
    native=project/'native';native.mkdir()
    library_fixture(native)
    p=load_project(project)
    music=[res for _,res in p.by_type['music']]
    first,second=music[0]['id'],music[1]['id']
    set_script(folders['sample_town'],[
        ev('play','EVENT_MUSIC_PLAY',musicId=first),
        ev('wait','EVENT_WAIT',units='frames',frames=num(24)),
        ev('same','EVENT_MUSIC_PLAY',musicId=first),
        *([ev('text','EVENT_TEXT',text='Music continues.')] if text else []),
        ev('wait2','EVENT_WAIT',units='frames',frames=num(24)),
        switch('switch',ids['cave'],(9,12,'up'),None)])
    set_script(folders['cave'],[
        ev('play2','EVENT_MUSIC_PLAY',musicId=second),
        ev('wait3','EVENT_WAIT',units='frames',frames=num(40)),
        ev('stop','EVENT_MUSIC_STOP'),
        ev('wait4','EVENT_WAIT',units='frames',frames=num(16)),
        ev('restart','EVENT_MUSIC_PLAY',musicId=second)])
    sidecar={'schema':'ngpcraft-project/1','ngpc':{'frame_rate':rate},'resources':{
        first:{'native_music':{'format':'ngps','song':'native/song.ngps','bank':'native/bank.json',
                               'note_encoding':'sound-creator','tick_clock':'vblank'}},
        second:{'native_music':{'format':'held-score','score':'native/score.json','bank':'native/second-bank.json'}}}}
    _atomic_write(project/'ngpcraft.json',json.dumps(sidecar))
    return project,folders,ids,first,second


class MusicEventTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_CLANG'),'requires Clang')
    @unittest.skipUnless(OVER_REV.is_dir(),'requires Over Rev audio (NGC_TEST_OVER_REV_AUDIO)')
    def test_pending_music_waits_for_load_and_service_is_guarded(self):
        from tools.music_library_probe import run_library
        with tempfile.TemporaryDirectory() as temp:
            library=library_fixture(Path(temp))
            actions=[[0,'loading',1],[0,'play',0],[0,'service'],[1,'service'],
                     [2,'loading',0],[2,'service'],[2,'service'],[3,'service'],
                     [4,'loading',1],[4,'play',1],[4,'service'],[5,'service'],
                     [6,'loading',0],[6,'service'],[7,'service']]
            actual=run_library(library,OVER_REV,actions,loading=True)
            reference=run_library(library,OVER_REV,actions,reference=True,loading=True)
            self.assertEqual(actual['trace'],reference['trace'])
            self.assertEqual(actual['initial_packets'],reference['initial_packets'])
            rows=actual['trace']
            # Over Rev records the requested ID immediately; PENDING tells
            # whether the actual driver has been allowed to start it.
            for index in (2,3):
                self.assertEqual(rows[index]['state'][3:5],[0,0])
                self.assertEqual(rows[index]['state'][2],0)
            self.assertEqual(rows[5]['state'][3:5],[0,65535])
            self.assertEqual(rows[5]['state'],rows[6]['state'])
            self.assertEqual(rows[6]['packets'],[])
            for index in (10,11):self.assertEqual(rows[index]['state'][3:5],[1,1])
            self.assertEqual(rows[13]['state'][3:5],[1,65535])

    def test_builder_and_vm_bytes_follow_published_reference(self):
        b=ScriptBuilder([],{'music':[{'id':'m','dataName':'song'}]})
        b.music_play('m');b.music_stop()
        self.assertIn('        VM_MUSIC_PLAY           ___bank_song_Data, _song_Data, .MUSIC_NO_LOOP',b.output)
        self.assertIn('        VM_MUSIC_STOP',b.output)
        events=[ev('a','EVENT_MUSIC_PLAY',musicId='m'),ev('b','EVENT_MUSIC_STOP')]
        text=compile_entity_events('test',events,['a','b'],{'music':[{'id':'m','dataName':'song'}]}, {})
        self.assertEqual(assemble(text,{},sprites={'song_Data':258})[1],bytes([0x60,1,2,0,0x61,0]))
        with self.assertRaisesRegex(CompileError,'unknown music asset'):
            compile_entity_events('test',[ev('a','EVENT_MUSIC_PLAY',musicId='absent')],['a'],{}, {})


@unittest.skipUnless(GBS2.is_dir(),'requires published GB Studio 4.3.2')
class ProjectMusicTests(unittest.TestCase):
    def test_reachable_native_assets_only_and_physical_service_30fps(self):
        with tempfile.TemporaryDirectory() as temp:
            project,_,_,first,second=project_fixture(Path(temp),rate=30,text=True)
            manifest=export_project(project,Path(temp)/'export')
            self.assertEqual([x['resource_id'] for x in manifest['native_audio']['tracks']],[first,second])
            for name in ('VM_MUSIC_PLAY','VM_MUSIC_STOP'):self.assertIn(name,manifest['gbvm']['opcodes'])
            output=Path(temp)/'export'
            startup=(output/'src/startup.c').read_text()
            self.assertIn('volatile unsigned char ngc_frame;',startup)
            self.assertIn('g_vb_counter = ngc_frame;',startup)
            vm=(output/'src/gbvm.c').read_text()
            self.assertIn('ngc_audio_service();',vm[vm.index('static void wait_vbl_done'):])
            game=(output/'src/game.c').read_text()
            self.assertEqual(game.count('ngc_audio_init();'),1)
            self.assertIn('ngc_audio_set_loading(1u);',game)
            self.assertIn('!ngc_audio_loading_flag',(output/'src/audio.c').read_text())
            # Most gbs2 music resources have no binding and are not converted.
            self.assertEqual(len(json.loads((output/'music-library.json').read_text())['tracks']),2)

    def test_binding_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            project,_,_,first,_=project_fixture(Path(temp))
            sidecar=json.loads((project/'ngpcraft.json').read_text())
            del sidecar['resources'][first]
            _atomic_write(project/'ngpcraft.json',json.dumps(sidecar))
            with self.assertRaisesRegex(CompileError,'reachable music needs'):
                export_project(project,Path(temp)/'out')
            sidecar['resources'][first]={'native_music':{'format':'held-score','score':'../escape','bank':'native/bank.json'}}
            _atomic_write(project/'ngpcraft.json',json.dumps(sidecar))
            with self.assertRaisesRegex(ValueError,'escapes project'):load_project(project)

    def test_roundtrip_keeps_bound_native_binary_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            project,*_=project_fixture(Path(temp))
            saved=Path(temp)/'saved'
            write_project(load_project(project),saved)
            for name in ('song.ngps','bank.json','score.json','second-bank.json'):
                self.assertEqual((project/'native'/name).read_bytes(),(saved/'native'/name).read_bytes())
            manifest=export_project(saved,Path(temp)/'out')
            self.assertEqual(len(manifest['native_audio']['tracks']),2)

    def test_save_with_music_selects_track_restoration(self):
        with tempfile.TemporaryDirectory() as temp:
            project,folders,ids,first,_=project_fixture(Path(temp))
            set_script(folders['sample_town'],[
                ev('play','EVENT_MUSIC_PLAY',musicId=first),
                ev('save','EVENT_SAVE_DATA',saveSlot=0),
                switch('switch',ids['cave'],(9,12,'up'),None)])
            manifest=export_project(project,Path(temp)/'out')
            self.assertIn('audio_save_state',manifest['features'])
            self.assertIn('ngc_state_get_audio',(Path(temp)/'out/src/audio.c').read_text())

    def test_stop_only_and_no_music_do_not_select_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            project,folders,_,_,_=project_fixture(Path(temp))
            set_script(folders['sample_town'],[ev('stop','EVENT_MUSIC_STOP')])
            manifest=export_project(project,Path(temp)/'out')
            self.assertNotIn('native_audio',manifest['features'])
            self.assertFalse((Path(temp)/'out/src/audio.c').exists())
            self.assertNotIn('g_vb_counter',(Path(temp)/'out/src/startup.c').read_text())

    @unittest.skipUnless(os.environ.get('NGC_TEST_TOSHIBA'),'requires Toshiba')
    def test_single_scene_and_silent_exports_link_with_selected_units(self):
        for music in (True,False):
            with self.subTest(music=music),tempfile.TemporaryDirectory() as temp:
                project,folders,_,first,_=project_fixture(Path(temp))
                set_script(folders['sample_town'],[
                    ev('play','EVENT_MUSIC_PLAY',musicId=first) if music else ev('stop','EVENT_MUSIC_STOP')])
                manifest=export_project(project,Path(temp)/'out')
                build=build_rom(Path(temp)/'out',os.environ['NGC_TEST_TOSHIBA'])
                self.assertEqual('_ngc_audio_service' in build['link_audit']['public_symbols'],music)
                self.assertEqual('native_audio' in manifest['features'],music)

    @unittest.skipUnless(os.environ.get('NGC_TEST_TOSHIBA'),'requires Toshiba')
    def test_real_two_scene_exports_link_with_audio_at_60_and_30(self):
        for rate in (60,30):
            with self.subTest(rate=rate),tempfile.TemporaryDirectory() as temp:
                project,*_=project_fixture(Path(temp),rate=rate,text=True)
                export_project(project,Path(temp)/'out')
                build=build_rom(Path(temp)/'out',os.environ['NGC_TEST_TOSHIBA'])
                self.assertIn('_ngc_audio_service',build['link_audit']['public_symbols'])
                # ROM proof: emulator + BIOS, and the reference library (Clang + Over Rev audio).
                if (os.environ.get('NGC_TEST_EMULATOR') and os.environ.get('NGC_TEST_BIOS')
                        and os.environ.get('NGC_TEST_CLANG') and OVER_REV.is_dir()):
                    from tools.probe_music_events import observe
                    proof=observe(Path(temp)/'out',os.environ['NGC_TEST_EMULATOR'],
                        os.environ['NGC_TEST_BIOS'],OVER_REV,Path(temp)/'proof')
                    self.assertTrue(proof['correct'])
                    self.assertGreater(proof['z80_executed'],0)
                    self.assertEqual([a[2] for a in proof['actions'] if a[1]=='play'],[0,0,1,1])
                    self.assertEqual(sum(a[1]=='stop' for a in proof['actions']),1)
                    self.assertEqual(len(proof['actual_bytes']),501)
                    served={a[0] for a in proof['actions'] if a[1]=='service'}
                    # Modal text stops game ticks but cannot stop audio service.
                    self.assertTrue(set(range(40,125)).issubset(served))
                    self.assertTrue(set(range(132,360)).issubset(served))
