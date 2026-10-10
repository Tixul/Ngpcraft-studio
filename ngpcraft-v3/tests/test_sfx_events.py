import json,os,tempfile,unittest,subprocess,shutil
from unittest.mock import patch
from pathlib import Path
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.events import compile_entity_events
from ngpcraft.pipeline import export_project
from ngpcraft.project import _atomic_write,load_project
from ngpcraft.project_sfx import validate_bindings
from ngpcraft.project_sfx import attach
from ngpcraft.music_library import generate_library
from ngpcraft.toolchain import build_rom
from tools.demo_scene_switch import set_script,ev,switch
from test_music_events import project_fixture
from test_music_library import library_fixture
from test_music_streams import OVER_REV
from types import SimpleNamespace
from ngpcraft.scene_init import used_events

STEP={'frames':3,'tone_divider':300,'tone_attenuation':4,'noise':4,'noise_attenuation':6}

class SfxTests(unittest.TestCase):
    def test_custom_call_cache_does_not_create_reachable_events(self):
        real={'id':'custom','script':[ev('sound','EVENT_SOUND_PLAY_EFFECT',type='crash')]}
        call=ev('call','EVENT_CALL_CUSTOM_EVENT',customEventId='custom')
        call['children']={'script':[{'command':''},ev('wrong','EVENT_MUSIC_PLAY',musicId='unused')]}
        project=SimpleNamespace(by_type={'script':[('custom.gbsres',real)]},files={'custom.gbsres':real})
        init=SimpleNamespace(scenes=[SimpleNamespace(events=[call],sources=['scene:script[0]'])])
        found=used_events(init,project)
        self.assertIn('EVENT_SOUND_PLAY_EFFECT',found)
        self.assertNotIn('EVENT_MUSIC_PLAY',found)
        self.assertNotIn('',found)

    @unittest.skipUnless(os.environ.get('NGC_TEST_CLANG'),'requires Clang')
    def test_native_priority_lifetime_and_physical_clock(self):
        from tools.music_library_probe import probe_source
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);library=library_fixture(root)
            library['sfx_roots']=['Sfx_PlayToneEx','Sfx_PlayNoiseEx','Sfx_Stop']
            shared={'nativeSfx':{'first':[STEP],'second':[{**STEP,'frames':5}]},'nativeSfxSelected':{0,1}}
            audio=attach(generate_library(library),shared)
            with patch('tools.music_library_probe.generate_library',return_value=audio):
                files=probe_source(library,OVER_REV)
            source=files['probe.c']+'''
unsigned int effect_id(void) { return ngc_sfx_id; }
unsigned int effect_left(void) { return ngc_sfx_remaining; }
'''
            for name,text in files.items():(root/name).write_text(text)
            (root/'probe.c').write_text(source)
            names=('probe_init','probe_clock','probe_play','probe_service','probe_state','ngc_sfx_play','effect_id','effect_left')
            result=subprocess.run([os.environ['NGC_TEST_CLANG'],'--target=wasm32','-std=c89','-pedantic-errors','-O2','-nostdlib','-ffreestanding','-Wl,--no-entry',
                *('-Wl,--export='+n for n in names),str(root/'probe.c'),'-o',str(root/'probe.wasm')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            script='''const fs=require('fs'),assert=require('assert');
WebAssembly.instantiate(fs.readFileSync(process.argv[2]),{}).then(({instance:{exports:p}})=>{
 p.probe_init();assert.equal(p.effect_id(),65535);p.probe_play(0);
 p.ngc_sfx_play(0,8);p.ngc_sfx_play(1,4);assert.equal(p.effect_id(),0);
 p.probe_clock(1);p.probe_service();assert.equal(p.effect_left(),2);
 p.probe_service();assert.equal(p.effect_left(),2);
 p.ngc_sfx_play(1,8);assert.equal(p.effect_id(),1);
 for(let i=2;i<=7;i++){p.probe_clock(i);p.probe_service();}
 assert.equal(p.effect_id(),65535);p.ngc_sfx_play(0,0);assert.equal(p.effect_id(),0);
 assert.equal(p.probe_state(6),0);assert.equal(p.probe_state(7),0);
}).catch(e=>{console.error(e);process.exitCode=1;});'''
            (root/'run.js').write_text(script)
            result=subprocess.run([shutil.which('node'),str(root/'run.js'),str(root/'probe.wasm')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_closed_binding_limits(self):
        validate_bindings({'crash:0.5':[STEP]},'test')
        for field,value in (('frames',0),('frames',255),('tone_divider',1024),('noise',8),('tone_attenuation',True)):
            with self.subTest(field=field,value=value),self.assertRaises(CompileError):
                validate_bindings({'crash:0.5':[{**STEP,field:value}]},'test')
        with self.assertRaises(CompileError):validate_bindings({'crash:0.5':[]},'test')

    def test_priority_and_builtin_wait_are_explicit(self):
        options={'nativeSfx':{'crash:0.5':[STEP]}}
        event=ev('sound','EVENT_SOUND_PLAY_EFFECT',type='crash',duration=0.5,priority='high',wait=True)
        text=compile_entity_events('sound',[event],['sound'],options,{})
        self.assertIn('VM_SFX_PLAY',text);self.assertIn('0, 0, 10, 8',text)
        self.assertIn('30',text)
        # Without a binding a built-in effect is translated by default, warned
        # (docs/M5_BUILTIN_SFX.md); a sound file still needs its explicit binding.
        options={'nativeSfx':{},'warnings':[]}
        text=compile_entity_events('default',[event],['sound'],options,{})
        self.assertIn('VM_SFX_PLAY',text);self.assertIn('crash:0.5',options['nativeSfx'])
        self.assertTrue(any('default rule' in w for w in options['warnings']))
        asset=ev('sound','EVENT_SOUND_PLAY_EFFECT',type='a-sound-file-id',effect=0)
        with self.assertRaisesRegex(CompileError,'missing explicit'):
            compile_entity_events('missing',[asset],['sound'],{'nativeSfx':{}},{})

    def test_sfx_only_export_compiles_without_music(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project,folders,_,_,_=project_fixture(root)
            set_script(folders['sample_town'],[
                ev('sound','EVENT_SOUND_PLAY_EFFECT',type='crash',duration=0.5,priority='medium',wait=True)])
            set_script(folders['cave'],[])
            side=project/'ngpcraft.json';data=json.loads(side.read_text());data['ngpc']['native_sfx']={'crash:0.5':[STEP]}
            _atomic_write(side,json.dumps(data))
            out=root/'export';manifest=export_project(project,out)
            self.assertIn('native_sfx',manifest['features'])
            self.assertEqual(manifest['native_audio']['tracks'],[])
            self.assertIn('ngc_sfx_play',(out/'src/audio.c').read_text())
            if os.environ.get('NGC_TEST_TOSHIBA'):
                result=build_rom(out,Path(os.environ['NGC_TEST_TOSHIBA']))
                self.assertTrue(result['complete_rom'])

    def test_save_restore_cancels_effect_without_saving_playhead(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project,folders,ids,first,_=project_fixture(root)
            save=ev('save','EVENT_SAVE_DATA',saveSlot=0,__scriptTabs='save')
            save['children']={'true':[],'load':[]}
            set_script(folders['sample_town'],[
                ev('music','EVENT_MUSIC_PLAY',musicId=first),
                ev('sound','EVENT_SOUND_PLAY_EFFECT',type='crash',duration=0.5),save,
                switch('next',ids['cave'],(9,12,'up'),None)])
            side=project/'ngpcraft.json';data=json.loads(side.read_text());data['ngpc']['native_sfx']={'crash:0.5':[STEP]}
            _atomic_write(side,json.dumps(data));out=root/'export';export_project(project,out)
            source=(out/'src/audio.c').read_text()
            restore=source.split('unsigned short ngc_state_get_audio(',1)[1]
            self.assertIn('Sfx_Stop();',restore)
            self.assertIn('ngc_sfx_id = 65535u;',restore)
            saved=source.split('void ngc_state_put_audio(void)',1)[1].split('unsigned short ngc_state_get_audio(',1)[0]
            self.assertNotIn('&ngc_sfx_',saved)

    def test_default_has_no_sfx_symbol_and_explicit_vm_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project,_,_,_,_=project_fixture(root)
            out=root/'export';manifest=export_project(project,out)
            self.assertNotIn('native_sfx',manifest['features'])
            self.assertNotIn('void ngc_sfx_play',(out/'src/audio.c').read_text())
            side=project/'ngpcraft.json';data=json.loads(side.read_text());data['ngpc']['vm_context_policy']='gbvm-16'
            _atomic_write(side,json.dumps(data));manifest=export_project(project,root/'bounded')
            self.assertEqual(manifest['scene_engine']['contexts'],16)
            self.assertEqual(manifest['scene_engine']['context_policy'],'gbvm-16')
            data['ngpc']['vm_context_policy']='silent-truncate';_atomic_write(side,json.dumps(data))
            with self.assertRaises(ValueError):load_project(project)
