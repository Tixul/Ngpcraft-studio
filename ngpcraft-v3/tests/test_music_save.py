import hashlib,json,os,re,tempfile,unittest
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.project import _atomic_write
from ngpcraft.save_state import SAVE,SKIP,classify,describe
from ngpcraft.toolchain import build_rom
from tools.lab_music_save import project_case
from tools.music_library_probe import run_library
from test_music_library import library_fixture
from test_music_streams import OVER_REV

ENV=[os.environ.get(key) for key in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]


class MusicSaveTests(unittest.TestCase):
    def test_identity_is_saved_and_closed_driver_inventory_is_transient(self):
        self.assertEqual(classify(None,'ngc_music_current')[0],SAVE)
        for name in ('s_bgm_note_table','s_bgm_v0','ngc_music_pending','g_vb_counter'):
            self.assertEqual(classify(None,name)[0],SKIP)
        self.assertIsNone(classify(None,'s_bgm_unreviewed'))

    @unittest.skipUnless(ENV[3],'requires Clang')
    def test_actual_reader_restarts_same_track_restores_silence_and_rejects_bad_id(self):
        with tempfile.TemporaryDirectory() as temp:
            library=library_fixture(Path(temp))
            actions=[[0,'play',0]]+[[i,'service'] for i in range(1,12)]+[
                [12,'loading',1],[12,'restore',0],[13,'service'],[14,'loading',0],
                [14,'service'],[15,'service'],[16,'restore',65535],[17,'service'],
                [18,'play',1],[19,'service'],[20,'restore',99],[21,'service']]
            actual=run_library(library,OVER_REV,actions,loading=True,saving=True)
            oracle=run_library(library,OVER_REV,actions,reference=True,loading=True,saving=True)
            self.assertEqual(actual['trace'],oracle['trace'])
            restored=next(row for row in actual['trace'] if row['op']=='restore' and row['frame']==12)
            self.assertEqual(restored['state'][3:5],[0,0])
            silenced=next(row for row in actual['trace'] if row['op']=='restore' and row['frame']==16)
            self.assertEqual(silenced['state'][3:5],[65535,65535])
            invalid=next(row for row in actual['trace'] if row['op']=='restore' and row['frame']==20)
            self.assertEqual(invalid['state'][3:5],[1,65535])

    def test_signature_covers_audio_and_validation_precedes_vm_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            project,_=project_case(Path(temp))
            first=Path(temp)/'first';second=Path(temp)/'second'
            export_project(project,first)
            score=json.loads((project/'native/town.json').read_text())
            score['notes'][0]['divider']+=1
            _atomic_write(project/'native/town.json',json.dumps(score))
            export_project(project,second)
            signatures=[]
            for out in (first,second):
                source=(out/'src/game.c').read_text()
                signatures.append(re.search(r'ngc_save_signature = (0x[0-9A-F]+)',source)[1])
                restore=source.split('unsigned char ngc_save_restore(unsigned short length)',1)[1]
                self.assertLess(restore.index('ngc_state_check_audio'),restore.index('ngc_state_get_vm'))
            self.assertNotEqual(*signatures)


@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS and Clang')
class MusicSaveRomTests(unittest.TestCase):
    def test_real_game_restoration_matches_exact_z80_packets_and_inventory(self):
        from tools.probe_music_events import observe
        from tools.state_inventory import inventory,target_sizes
        from tools.save_vm import budget
        cases=[('active',60),('same',60),('stopped',60),('pending',30),('missing',60)]
        for case,rate in cases:
            with self.subTest(case=case,rate=rate),tempfile.TemporaryDirectory() as temp:
                project,info=project_case(Path(temp),case,rate)
                out=Path(temp)/'export'
                manifest=export_project(project,out);build=build_rom(out,ENV[0])
                measured=target_sizes(out,describe(inventory(out,ENV[3])),ENV[0])
                self.assertEqual(budget(measured,manifest)['payload_max_bytes'],build['save']['payload_max_bytes'])
                symbols=build['link_audit']['public_symbols']
                rom=(out/'rom/game.ngc').read_bytes();at=symbols['_ngc_state_bytes_audio']['address']-0x200000
                self.assertEqual(int.from_bytes(rom[at:at+2],'little'),2)
                proof=observe(out,ENV[1],ENV[2],OVER_REV,Path(temp)/'proof',frames=300)
                self.assertTrue(proof['correct'])
                restores=[a for a in proof['actions'] if a[1]=='restore']
                if case=='missing':
                    self.assertEqual(restores,[])
                    self.assertEqual(proof['samples'][-1]['variables']['VAR_VARIABLE_20'],99)
                else:
                    saved=65535 if case=='stopped' else 0
                    self.assertEqual([a[2] for a in restores],[saved])
                    self.assertEqual(proof['saved_track_ids']['0'] if '0' in proof['saved_track_ids'] else proof['saved_track_ids'][0],saved)
                    self.assertEqual(proof['samples'][-1]['variables']['VAR_VARIABLE_20'],1)
                    self.assertEqual(proof['samples'][-1]['variables']['VAR_VARIABLE_21'],7)
                    if case=='pending':
                        snapshot=next(row for row in proof['host_exported']['trace'] if row['op']=='save')
                        self.assertEqual(snapshot['state'][4],0)
                    if case=='active':
                        flash=(Path(temp)/'proof/flash.bin').read_bytes()
                        second=observe(out,ENV[1],ENV[2],OVER_REV,Path(temp)/'second',frames=300,flash=flash)
                        self.assertTrue(second['correct'])
                        self.assertFalse(any(a[1]=='save' for a in second['actions']))
                        self.assertEqual((Path(temp)/'second/flash.bin').read_bytes(),flash)
                        self.assertEqual(second['samples'][-1]['variables']['VAR_VARIABLE_20'],1)
                        self.assertEqual(second['samples'][-1]['variables']['VAR_VARIABLE_21'],7)
                        from tools.lab_music_save import replace_saved_track
                        invalid=replace_saved_track(out,flash,99)
                        failed=observe(out,ENV[1],ENV[2],OVER_REV,Path(temp)/'invalid',frames=300,flash=invalid)
                        self.assertTrue(failed['correct'])
                        self.assertFalse(any(a[1]=='restore' for a in failed['actions']))
                        self.assertEqual(failed['samples'][-1]['variables']['VAR_VARIABLE_20'],99)
                        self.assertEqual(failed['samples'][-1]['variables']['VAR_VARIABLE_21'],9)

    def test_removed_restart_is_detected_in_the_rom(self):
        from tools.probe_music_events import observe
        with tempfile.TemporaryDirectory() as temp:
            project,_=project_case(Path(temp),'same')
            out=Path(temp)/'export';manifest=export_project(project,out)
            source=(out/'src/audio.c').read_text()
            marker='    ngc_music_pending = ngc_music_current;'
            self.assertEqual(source.count(marker),1)
            _atomic_write(out/'src/audio.c',source.replace(marker,'    ngc_music_pending = 65535u;'))
            manifest['sha256']['src/audio.c']=hashlib.sha256((out/'src/audio.c').read_bytes()).hexdigest()
            _atomic_write(out/'export.json',json.dumps(manifest))
            build_rom(out,ENV[0])
            proof=observe(out,ENV[1],ENV[2],OVER_REV,Path(temp)/'proof',frames=300)
            self.assertFalse(proof['correct'])
