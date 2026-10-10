"""Emote event, first-frame assets, shared GBVM state, absence and native pixels."""
import json,os,random,subprocess,tempfile,unittest
from pathlib import Path
from PIL import Image
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.gbvm.emit import NGPC_FUNCTIONS
from ngpcraft.emote_runtime import emote_source
from ngpcraft.emotes import compile_emotes
from ngpcraft.pipeline import export_project,plan_project
from ngpcraft.project import load_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.lab_actor_emotes import project_case,reference_source,wasm_compile,run
from tools.reference import REFERENCE

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]

class EmoteCompilerTests(unittest.TestCase):
    def test_released_handler_order_far_operand_and_init_fade_flag(self):
        source=(REFERENCE/'src/lib/events/eventActorEmote.js').read_text(encoding='utf-8')
        self.assertIn('actorSetActive(input.actorId);\n  actorEmote(input.emoteId);',source)
        self.assertIn('waitUntilAfterInitFade: true',source)
        out=[];b=ScriptBuilder(out,{'scene':{'actors':[{'id':'a'}]},'emotes':[{'id':'e','symbol':'emote_e'}]})
        HANDLERS['EVENT_ACTOR_EMOTE']({'actorId':'a','emoteId':'e'},b)
        self.assertEqual(out,['        ; Actor Set Active','        VM_SET_CONST            .LOCAL_ACTOR, 1','',
            '        ; Actor Emote','        VM_ACTOR_EMOTE          .LOCAL_ACTOR, ___bank_emote_e, _emote_e',''])

    def test_unknown_emote_has_location_and_preserves_author_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=project_case(root)
            path=next((p/'project/scenes').rglob('actors/push.gbsres'))
            actor=json.loads(path.read_text(encoding='utf-8'));event=next(e for e in actor['startScript'] if e['command']=='EVENT_ACTOR_EMOTE')
            event['args']['emoteId']='missing-author-emote';write_json(path,actor)
            with self.assertRaisesRegex(CompileError,r'startScript\[\d+\]: EVENT_ACTOR_EMOTE: emote.*missing-author-emote'):
                export_project(p,root/'export')
            self.assertIn('missing-author-emote',path.read_text())

    def test_unused_has_no_emote_tiles_timer_unit_or_link_symbol(self):
        from tools.platform_oracle import project_case as plain
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);m=export_project(plain(root),root/'export')
            self.assertNotIn('actor_emotes',m['features']);self.assertNotIn('emotes',m['scene_engine'])
            text=(root/'export/src/scene_actors.c').read_text()
            for name in ('ngc_emote','act_emote','ngc_actor_emote_step'):self.assertNotIn(name,text)
            if ENV[0]:
                build=build_rom(root/'export',ENV[0])
                self.assertNotIn('_ngc_actor_emote_step',build['link_audit']['public_symbols'])

    def test_scene_residency_budgets_and_stack_specialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);m=export_project(project_case(root),root/'export')
            report=m['scene_engine']['emotes']
            self.assertEqual(len(report['resident']),6);self.assertEqual(report['tiles'],24)
            self.assertEqual(report['hardware_sprites'],4);self.assertEqual(report['ram_bytes'],4)
            self.assertNotIn('emote:sweat',report['resident'])
            self.assertLess(m['gbvm']['stack_budget']['words_per_context'],64)

    def test_wide_strip_uses_first_frame_as_gbvm_and_invalid_shape_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=project_case(root);project=load_project(p)
            index,(rel,raw)=next((i,e) for i,e in enumerate(project.by_type['emote']) if e[1]['name']=='love')
            path=project.root/Path(rel).with_suffix('')
            before=compile_emotes(project,[(index,raw['id'])],'8x16','probe')[0]
            image=Image.open(path).convert('RGBA');wide=Image.new('RGBA',(32,16),(0,0,0,255));wide.paste(image,(0,0));wide.save(path)
            after=compile_emotes(project,[(index,raw['id'])],'8x8','probe')[0]
            self.assertEqual(before.tiles,after.tiles);self.assertEqual(len(after.frames),1)
            Image.new('RGBA',(16,8),(0,0,0,255)).save(path)
            with self.assertRaisesRegex(CompileError,r'emote love.*expected 16 px high'):
                compile_emotes(project,[(index,raw['id'])],'8x16','probe')

    def test_multi_scene_optional_emote_dispatch_and_per_scene_residency(self):
        from tools.lab_scene_cuts import project_case as cuts
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p,_=cuts(root);project=load_project(p)
            path=next((p/'project/scenes').rglob('scene.gbsres'))
            scene=json.loads(path.read_text(encoding='utf-8'));emote=project.by_type['emote'][0][1]
            scene['script'].insert(0,ev('emote','EVENT_ACTOR_EMOTE',actorId='player',emoteId=emote['id']))
            write_json(path,scene);m=export_project(p,root/'export')
            reports=m['scene_engine']['scenes']
            self.assertEqual(sum('emotes' in r for r in reports),1)
            if ENV[0]:build_rom(root/'export',ENV[0])

@unittest.skipUnless(ENV[3],'requires Clang wasm and Node')
class EmoteReferenceTests(unittest.TestCase):
    def test_shared_timer_and_emitted_wrapper_match_original_under_interleaved_contexts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=reference_source().replace('vm_actor_emote(','vm_actor_emote_reference(')
            header='''\nstatic SCRIPT_CTX gen_contexts[3];
#undef VM_REF_TO_PTR
#define VM_REF_TO_PTR(idx) (&actor_id)
'''
            kernel='\n'.join(emote_source([(0,100),(1,200)]))
            wrapper=NGPC_FUNCTIONS['vm_actor_emote']
            tail='''\nvoid reset_gen(void){unsigned k;ngc_emote_actor=255;ngc_emote_timer=0;act_emote_frame=0;
for(k=0;k<3;k++){gen_contexts[k].flags=0;gen_contexts[k].waitable=0;gen_contexts[k].PC=100;}}
void call_gen(unsigned c,unsigned actor,unsigned emote){actor_id=actor;gen_contexts[c].waitable=0;gen_contexts[c].PC=100;
vm_actor_emote(gen_contexts+c,0,0,emote);}
unsigned get_gen(unsigned i){return i==0?ngc_emote_actor:i==1?ngc_emote_timer:i==2?act_emote_frame:
i<6?gen_contexts[i-3].flags:i<9?gen_contexts[i-6].waitable:(unsigned short)gen_contexts[i-9].PC;}
'''
            wasm_compile(root,'compare',source+header+kernel+wrapper+tail,ENV[3])
            rng=random.Random(432260)
            cases=[[[0,1,0]]*61,[[0,0,0],[1,2,1]]*70,[[0,1,0],[1,2,1],[2,0,0]]*90]
            cases += [[[rng.randrange(3),rng.randrange(3),rng.randrange(2)] for _ in range(1000)] for _ in range(4)]
            write_json(root/'cases.json',cases)
            js='''const fs=require('fs'),e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;
let checked=0;for(const cases of JSON.parse(fs.readFileSync(process.argv[3]))){e.reset();e.reset_gen();
for(const [c,a,id] of cases){e.call(c,a,id===0?100:200);e.call_gen(c,a,id);
const x=Array.from({length:12},(_,i)=>e.get(i)),y=Array.from({length:12},(_,i)=>e.get_gen(i));
if(JSON.stringify(x)!==JSON.stringify(y))throw Error(JSON.stringify({c,a,id,x,y}));checked++;}}process.stdout.write(String(checked));'''
            write_new(root/'compare.js',js.encode())
            proc=subprocess.run(['node',str(root/'compare.js'),str(root/'compare.wasm'),str(root/'cases.json')],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(proc.stdout,'4471')

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS, Clang and Node')
class EmoteRomTests(unittest.TestCase):
    def test_released_frames_bounce_palette_hidden_disabled_pinned_and_new_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'proof',*ENV)
        self.assertEqual(r['state_mismatch_ticks'],0,r['state_first'])
        self.assertEqual(r['pixel_mismatch_frames'],0,r['pixel_first'])
        self.assertEqual(r['phases_seen'],list(range(1,7)));self.assertEqual(r['completed_phase'],6)
        self.assertEqual(r['dropped_frames'],[])

    def test_missing_bounce_witness_is_seen_at_screen(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,witness='bounce')
        self.assertGreater(r['pixel_mismatch_frames'],0)

    def test_half_duration_witness_is_detected_by_original_c(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,witness='duration')
        self.assertTrue(r['reference_error'] or r['state_mismatch_ticks'])

    def test_wrong_palette_witness_is_seen_at_screen(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,witness='palette')
        self.assertGreater(r['pixel_mismatch_frames'],0)

if __name__=='__main__':unittest.main()
