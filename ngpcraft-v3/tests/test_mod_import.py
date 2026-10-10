"""Official MicroDoom MOD import and exact cycle repetition, without padding."""
import hashlib,json,os,tempfile,unittest
from pathlib import Path
from tools.convert_mod import convert_mod
from tools.migrate_gbstudio import migrate
from ngpcraft.uge_convert import POLICY
from ngpcraft.audio_assets import MusicError
from ngpcraft.music_hold import compile_score

SOURCE=os.environ.get('NGC_TEST_MICRODOOM')
DEPS=os.environ.get('NGC_MIGRATION_DEPENDENCIES')

@unittest.skipUnless(SOURCE,'set NGC_TEST_MICRODOOM to the original MicroDoom project folder')
class ModImportTests(unittest.TestCase):
    def test_explicit_exact_repeat_preserves_source_period_and_hashes(self):
        source=Path(SOURCE)/'assets/music/Slow death.mod';before=source.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);apu=root/'apu.json';policy=root/'policy.json'
            apu.write_text(json.dumps(dict(NR12=0,NR22=0,NR32=0,NR42=0,NR51=255)))
            options={**POLICY,'transpose_semitones':[0,0,0],'noise_setting':5}
            policy.write_text(json.dumps(options))
            with self.assertRaisesRegex(MusicError,'424'):
                convert_mod(source,root/'refused',apu,policy)
            self.assertFalse((root/'refused').exists())
            policy.write_text(json.dumps({**options,'cycle_alignment':'exact-repeat'}))
            convert_mod(source,root/'converted',apu,policy)
            report=json.loads((root/'converted/loss-report.json').read_text())
            self.assertEqual((report['source_cycle_ticks'],report['cycle_repetitions'],report['loop_ticks']),(424,2,848))
            self.assertTrue(report['mapped_two_cycles_equal'])
            plan=compile_score(root/'converted/held-score.json',root/'converted/bank.json')
            self.assertEqual(plan['repeat_frames'],848)
            manifest=json.loads((root/'converted/manifest.json').read_text())
            for name,digest in manifest['files_sha256'].items():
                self.assertEqual(hashlib.sha256((root/'converted'/name).read_bytes()).hexdigest(),digest)
            with self.assertRaises(ValueError):convert_mod(source,root/'converted',apu,policy)
        self.assertEqual(source.read_bytes(),before)

    @unittest.skipUnless(DEPS,'set NGC_MIGRATION_DEPENDENCIES to the migration node_modules')
    def test_official_migration_keeps_source_and_records_sprite_detection(self):
        source=Path(SOURCE)/'MicroDoom.gbsproj';before=source.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'migrated';report=migrate(source,out,DEPS)
            self.assertEqual(report['unimplemented_commands'],[])
            self.assertEqual(report['resources'],126)
            self.assertTrue(report['source_unchanged']);self.assertTrue(report['warnings'])
            self.assertEqual(sum(item.get('source')=='renderer/lib/sprites/detect.ts:detectClassic' for item in report['asset_hydration']),27)
            with self.assertRaises(ValueError):migrate(source,out,DEPS)
        self.assertEqual(source.read_bytes(),before)
