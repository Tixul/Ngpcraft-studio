"""Full demo audio must not push modal video commits out of VBlank.

NGC_TEST_COMPLETE_GBS2 supplies the explicit native-audio project. The test
copies it, reuses the authored Cave/Sage dialogue, and observes the cartridge
externally. No profiling code is added to the game.
"""
import json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom

ROOT=Path(__file__).resolve().parents[1]
ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_COMPLETE_GBS2')]

@unittest.skipUnless(all(ENV),'requires native tools and full explicit-audio gbs2 project')
class AudioVideoTests(unittest.TestCase):
    def test_platform_dialogue_commits_stay_in_vblank_with_full_music(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project=root/'project';shutil.copytree(ENV[3],project)
            scene=project/'project/scenes/caves/cave/scene.gbsres'
            data=json.loads(scene.read_text(encoding='utf8'))
            sage=json.loads((scene.parent/'actors/sage.gbsres').read_text(encoding='utf8'))
            data['script']+=sage['script'][:1]
            scene.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
            settings=project/'project/settings.gbsres';s=json.loads(settings.read_text(encoding='utf8'))
            s.update(startSceneId=data['id'],startX=7,startY=7)
            settings.write_text(json.dumps(s),encoding='utf8')
            export=root/'export';manifest=export_project(project,export)
            # Starting directly in Cave drops the one-way Logo/Title bootstrap.
            self.assertEqual(len(manifest['scene_engine']['scenes']),15)
            self.assertEqual(len(manifest['native_audio']['tracks']),10)
            build_rom(export,Path(ENV[0]))
            probe=root/'probe'
            result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/probe_gbvm.py'),str(export),
                '--emulator',ENV[1],'--bios',ENV[2],'--out',str(probe),'--frames','240','--frame-timing'],
                cwd=ROOT,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            evidence=json.loads((probe/'probe.json').read_text(encoding='utf8'))
            modal=[r for r in evidence['frame_timings'] if r['mode']=='modal']
            self.assertGreater(len(modal),200)
            self.assertEqual([r for r in modal if not r['started_in_vblank'] or r['overran_vblank']],[])
            artifacts=os.environ.get('NGC_TEST_ARTIFACTS')
            if artifacts:
                out=Path(artifacts);out.mkdir(parents=True,exist_ok=True)
                (out/'audio-video-vblank.json').write_text(json.dumps(evidence,indent=2),encoding='utf8')
