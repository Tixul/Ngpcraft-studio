"""Spritesheet compiler, resident assets, GBVM C oracle and native ROM."""
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.gbvm.sprite_assets import sprite_assets
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_actors import actors_source
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_platform import states_order
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.lab_actor_spritesheets import PHASES, project_case, reference_source, run, run_collision
from tools.reference import REFERENCE

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]

class SpriteCompilerTests(unittest.TestCase):
    def test_published_helper_order_and_far_sheet_macro(self):
        source=(REFERENCE/'src/lib/events/eventActorSetSprite.js').read_text(encoding='utf-8')
        self.assertIn('actorSetActive(input.actorId);\n  actorSetSprite(input.spriteSheetId);',source)
        out=[]
        b=ScriptBuilder(out,{'scene':{'actors':[{'id':'a'}]},'sprites':[{'id':'s','symbol':'sprite_s'}]})
        HANDLERS['EVENT_ACTOR_SET_SPRITE']({'actorId':'a','spriteSheetId':'s'},b)
        self.assertEqual(out,['        ; Actor Set Active','        VM_SET_CONST            .LOCAL_ACTOR, 1','',
                             '        ; Actor Set Spritesheet','        VM_ACTOR_SET_SPRITESHEET .LOCAL_ACTOR, ___bank_sprite_s, _sprite_s',''])

    def test_missing_asset_is_localized_and_author_value_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=project_case(root)
            path=next((p/'project/scenes').rglob('actors/push.gbsres'))
            actor=json.loads(path.read_text(encoding='utf-8'))
            actor['startScript'][2]['args']['spriteSheetId']='missing-author-sheet'
            write_json(path,actor)
            with self.assertRaisesRegex(CompileError,r'startScript\[2\]: EVENT_ACTOR_SET_SPRITE: sprite sheet.*missing-author-sheet'):
                export_project(p,root/'export')
            self.assertEqual(json.loads(path.read_text())['startScript'][2]['args']['spriteSheetId'],'missing-author-sheet')

    def test_unreachable_sheet_has_no_residency(self):
        from tools.platform_oracle import project_case as plain
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manifest=export_project(plain(root),root/'export')
            self.assertNotIn('actor_spritesheets',manifest['features'])
            self.assertNotIn('cat',manifest['scene_engine']['sheets'])
            text=(root/'export/src/scene_actors.c').read_text()
            for name in ('act_sheet[','act_init_bounds','act_state_anims','ngc_actor_set_spritesheet'):
                self.assertNotIn(name,text)
            if ENV[0]:
                build=build_rom(root/'export',ENV[0])
                self.assertNotIn('_ngc_actor_set_spritesheet',build['link_audit']['public_symbols'])

    def test_reachable_asset_only_used_by_sprite_switch_and_stack_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);m=export_project(project_case(root),root/'export')
            self.assertEqual(set(m['scene_engine']['sheets']),{'player','turnip','cat','chest'})
            self.assertEqual(m['scene_engine']['actor_spritesheets']['ram_bytes'],56)
            self.assertLess(m['gbvm']['stack_budget']['words_per_context'],64)
            self.assertIn('actor_spritesheets',m['features'])

    def test_switch_only_does_not_export_the_animation_state_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=project_case(root)
            path=next((p/'project/scenes').rglob('actors/push.gbsres'))
            actor=json.loads(path.read_text());actor['startScript']=[e for e in actor['startScript'] if e['command']!='EVENT_ACTOR_SET_STATE']
            write_json(path,actor);m=export_project(p,root/'export')
            self.assertNotIn('actor_anim_states',m['features'])
            text=(root/'export/src/scene_actors.c').read_text()
            self.assertNotIn('void ngc_actor_set_anim_set(',text)
            if ENV[0]:build_rom(root/'export',ENV[0])

    def test_reachability_uses_shared_script_symbols(self):
        sprites=[{'id':'a','symbol':'sprite_a'},{'id':'b','symbol':'sprite_b'}]
        self.assertEqual(sprite_assets(['        VM_ACTOR_SET_SPRITESHEET .SCRIPT_ARG_0_ACTOR, ___bank_sprite_b, _sprite_b\n'],sprites),((1,'b'),))

    def test_multi_scene_different_residency_and_optional_dispatch(self):
        from tools.lab_scene_cuts import project_case as cuts
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p,_=cuts(root)
            scenes=sorted((p/'project/scenes').rglob('scene.gbsres'))
            scene=json.loads(scenes[0].read_text(encoding='utf-8'))
            ids={s['name']:s['id'] for _,s in load_project(p).by_type['sprite']}
            scene['script'].insert(0,ev('switch-cat','EVENT_ACTOR_SET_SPRITE',actorId='player',spriteSheetId=ids['cat']))
            write_json(scenes[0],scene);m=export_project(p,root/'export')
            self.assertIn('actor_spritesheets',m['features'])
            self.assertIn('ngc_actor_set_spritesheet',(root/'export/src/scene_actors.c').read_text())
            if ENV[0]:build_rom(root/'export',ENV[0])

@unittest.skipUnless(ENV[3],'requires Clang wasm and Node')
class SpriteReferenceTests(unittest.TestCase):
    def test_generated_runtime_matches_original_c_including_bounds_and_animation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=project_case(root);project=load_project(p)
            raw=[s for _,s in project.by_type['sprite']]
            reachable=tuple((i,s['id']) for i,s in enumerate(raw) if s['name'] in {p[0] for p in PHASES})
            engine=build_engine_scene(project,project.scenes[0],sprite_switches=reachable)
            source=actors_source(list(engine.actors),engine.sheets,engine.background.height,
                bounds=engine.bounds,actor_states=states_order(project),sprite_switches=engine.sprite_switches)
            cases={local:global_id for global_id,local,_ in engine.sprite_switches}
            stub='''unsigned short ngc_scroll_next[2];
void ngc_scroll_set(short x,short y) { (void)x; (void)y; }
void ngc_sprites_begin(void) {} void ngc_sprites_end(void) {}
void ngc_sprite_draw(unsigned short frame,short x,short y) { (void)frame;(void)x;(void)y; }
void reset(void) {ngc_actors_load();gb_scroll_x=gb_scroll_y=0u;activate(1u);}
void select_state(unsigned s,unsigned loop) {ngc_actor_set_anim_set(1u,s);ngc_actor_set_flags(1u,loop?0:4,4);}
void set_dir(unsigned anim) {ngc_actor_set_dir(1u,anim&3u,anim>>2);}
void step(unsigned time) {ngc_game_time=time;ngc_actors_update(0u);}
unsigned get(unsigned i) {return i==0?act_frame[1]-act_fstart[1]:i==1?act_frame[1]:i==2?act_flags[1]:i==3?act_fend[1]-act_fstart[1]:i<8?(unsigned short)act_bounds[4+i-4]:act_anim[1];}
'''
            stub+='void select_sheet(unsigned s) {switch(s) {'+''.join(f'case {local}:ngc_actor_set_spritesheet(1u,{gid}u);break;' for local,gid in cases.items())+'}}\n'
            ref=reference_source(engine,project)+'\nvoid set_dir(unsigned anim) {actors[0].animation=anim;actor_reset_anim(actors);}\n'
            for name,c in [('generated',source+stub),('reference',ref)]:
                write_new(root/(name+'.c'),c.encode())
                proc=subprocess.run([ENV[3],'--target=wasm32','-nostdlib','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',str(root/(name+'.c')),'-o',str(root/(name+'.wasm'))],capture_output=True,text=True)
                self.assertEqual(proc.returncode,0,proc.stderr)
            rng=random.Random(4322)
            steps=[]
            for time in range(1024):
                # Every direction, default/missing states, looping flags and
                # repeated sheets are exercised between ordinary animation ticks.
                steps.append([time,rng.choice(list(cases)),rng.randrange(8),rng.randrange(len(states_order(project))),rng.randrange(2)])
            write_json(root/'steps.json',steps)
            js='''const fs=require('fs');const load=n=>new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]+'/'+n+'.wasm'))).exports;
const a=load('generated'),b=load('reference');a.reset();b.reset();let checked=0;
for(const [time,s,dir,state,loop] of JSON.parse(fs.readFileSync(process.argv[2]+'/steps.json'))){
for(const e of [a,b]){e.set_dir(dir);e.select_state(state,loop);e.step(time);e.select_sheet(s);}
const x=Array.from({length:9},(_,i)=>a.get(i)),y=Array.from({length:9},(_,i)=>b.get(i));
if(JSON.stringify(x)!==JSON.stringify(y)){throw Error(JSON.stringify({time,s,dir,state,loop,x,y}));}checked++;}process.stdout.write(String(checked));'''
            write_new(root/'compare.js',js.encode())
            proc=subprocess.run(['node',str(root/'compare.js'),str(root)],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(proc.stdout,'1024')

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS, Clang and Node')
class SpriteRomTests(unittest.TestCase):
    def test_replaced_bounds_drive_walls_and_actor_contacts_in_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run_collision(Path(tmp)/'collision',*ENV[:3])
            self.assertEqual(result['mismatches'],[])
            self.assertEqual(result['motion_mismatches'],[])
            self.assertEqual(result['completed_cases'],9)
            self.assertEqual(result['dropped_frames'],[])

    def test_eight_sheet_state_transitions_match_original_frames_and_source_pngs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run(Path(tmp)/'proof',*ENV)
            self.assertEqual(result['pixel_mismatch_frames'],0,result['first'])
            self.assertEqual(result['phases_seen'],list(range(1,9)))
            self.assertEqual(result['dropped_frames'],[])
            self.assertEqual(result['poses']['3'],result['poses']['4'])

    def test_ignored_sheet_witness_is_detected_at_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run(Path(tmp)/'witness',*ENV,frames=180,witness='sheet')
            self.assertGreater(result['pixel_mismatch_frames'],0)

if __name__=='__main__':unittest.main()
