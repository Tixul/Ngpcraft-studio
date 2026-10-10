"""Real actor/frame attachment, scene assets and optional multiscene dispatch."""
import json,os,subprocess,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from ngpcraft.compiler import CompileError
from ngpcraft.scene_projectiles import attach_projectiles
from ngpcraft.scene_actors import actors_source
from ngpcraft.scene_link import link_scenes
from ngpcraft.projectiles import CompiledProjectiles,ProjectileRegistry
from ngpcraft.projectile_runtime import ProjectileDefinition
from ngpcraft.project import load_project
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.scene_engine import engine_files
from ngpcraft.sprites import NgpcSheet
from tools.platform_oracle import project_case

CLANG=os.environ.get('NGC_TEST_CLANG')
TOSHIBA=os.environ.get('NGC_TEST_TOSHIBA')
SHEET=NgpcSheet('test','8x8',((0,)*64,), ((),), (0,), ((0,0),)*8)
ACTOR=dict(sheet=0,x=1024,y=1024,dir=0,flags=8,anim_tick=255,move_speed=0)
BOX=(0,31,0,31)
COMPILED=CompiledProjectiles((7,), (ProjectileDefinition(BOX,((0,0),)*4,4,32,0,255,True,True,8,0),), (9,), ('s',))

def source(scripts=None,capacity=5):
    return attach_projectiles(actors_source([ACTOR,dict(ACTOR,x=2048)], [SHEET],152),
        COMPILED,capacity=capacity,bounds=(BOX,BOX),groups=(1,2),view_h=152,scripts=scripts)

class ProjectileSceneTests(unittest.TestCase):
    def test_cold_scene_is_byte_identical_and_uses_no_pool(self):
        original=actors_source([ACTOR],[SHEET],152)
        empty=CompiledProjectiles((),(),(),())
        self.assertEqual(attach_projectiles(original,empty,capacity=5,bounds=(BOX,),groups=(1,),view_h=152),original)
        with tempfile.TemporaryDirectory() as tmp:
            p=load_project(project_case(Path(tmp)))
            scene=build_engine_scene(p,p.scenes[0]);files,report=engine_files(scene)
            self.assertNotIn('projectiles',report)
            self.assertNotIn('proj_',files['src/scene_actors.c'])

    def test_selected_assets_precede_emotes_reuse_actor_sheets_and_report_ram(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=load_project(project_case(Path(tmp)))
            plain=build_engine_scene(p,p.scenes[0]);raw={s['id']:s for _,s in p.by_type['sprite']}
            registry=ProjectileRegistry(raw)
            registry.register({'spriteSheetId':plain.sheet_ids[0]},'projectile source')
            scene=build_engine_scene(p,p.scenes[0],projectiles=registry.selections,projectile_capacity=17)
            self.assertEqual(scene.sheets,plain.sheets)
            self.assertEqual(scene.actors,plain.actors)
            files,report=engine_files(scene,projectile_groups=[1]+[2]*(len(scene.actors)-1))
            self.assertEqual(report['projectiles']['pool_ram_bytes'],495)
            self.assertEqual(report['projectiles']['hit_handle_ram_bytes'],0)
            self.assertIn('proj_x[17]',files['src/scene_actors.c'])
            self.assertNotIn('ngc_vm_projectile',files['src/scene_actors.c'])
            with self.assertRaisesRegex(CompileError,'groups must be resolved explicitly'):engine_files(scene)

    def test_unreachable_png_is_not_opened_by_scene_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=load_project(project_case(Path(tmp)))
            initial=build_engine_scene(p,p.scenes[0]);unused=[rel for rel,s in p.by_type['sprite'] if s['id'] not in initial.sheet_ids]
            self.assertTrue(unused)
            for rel in unused:(p.root/Path(rel).with_suffix('')).unlink()
            after=build_engine_scene(p,p.scenes[0])
            self.assertEqual(after.sheets,initial.sheets)

    def test_multiscene_private_helpers_and_max_pool_storage(self):
        cold=actors_source([ACTOR],[SHEET],152)
        files,report=link_scenes([{'src/scene_actors.c':cold},{'src/scene_actors.c':source()},
            {'src/scene_actors.c':source(capacity=17)}])
        linked=files['src/scene_actors.c']
        self.assertIn('proj_x[17]',linked)
        self.assertIn('s1_ngc_projectiles_update',linked)
        self.assertNotIn('s0_ngc_projectiles_update',linked)
        # A cold scene has no launch case, and falls through to return 0.
        launch=linked[linked.index('unsigned char ngc_projectile_launch('):]
        self.assertIn('case 1u:',launch);self.assertIn('case 2u:',launch)

    def test_zero_bank_zero_address_is_present_and_hooks_are_selected(self):
        text=source({1:(0,0)})
        self.assertIn('proj_hit_present[2] = {\n    0, 1,',text)
        self.assertIn('ngc_vm_projectile_activate(i);',text)
        self.assertIn('ngc_vm_projectile_detach(i);',text)
        self.assertIn('ngc_vm_projectile_reset();',text)
        files,_=link_scenes([{'src/scene_actors.c':source()}, {'src/scene_actors.c':text}])
        self.assertIn('ngc_vm_projectile_hit',files['src/scene_actors.c'])

    def test_hit_contexts_charge_activations_to_their_own_script(self):
        from ngpcraft.gbvm.emit import projectile_hit_contexts
        self.assertEqual(projectile_hit_contexts(6,[0]*6),24)
        # Dead Dungeon Boss Level: nine bindings, Brain activates 4 actors,
        # Heart/Eye L/Eye R one each: 36 + 8*7, not 4*9*(1+2*4) = 324.
        self.assertEqual(projectile_hit_contexts(9,[0,4,1,0,1,0,0,0,1]),92)

    def test_movement_collision_bounds_are_reused(self):
        original=actors_source([ACTOR,dict(ACTOR,x=2048)],[SHEET],152,moves=(),bounds=(BOX,BOX))
        text=attach_projectiles(original,COMPILED,capacity=5,bounds=(BOX,BOX),groups=(1,2),view_h=152)
        self.assertEqual(text.count('short act_bounds['),1)
        link_scenes([{'src/scene_actors.c':text}])

    def test_invalid_binding_capacity_bounds_and_anchor_are_located(self):
        for bindings in ({2:(0,0)}, {1:(256,0)}, {1:(0,65536)}, {True:(0,0)}):
            with self.subTest(bindings=bindings),self.assertRaisesRegex(CompileError,'invalid hit binding'):source(bindings)
        with self.assertRaisesRegex(CompileError,'capacity'):source(capacity=256)
        with self.assertRaisesRegex(CompileError,'anchor changed'):
            attach_projectiles('',COMPILED,capacity=5,bounds=(BOX,),groups=(1,),view_h=152)

    @unittest.skipUnless(CLANG,'requires Clang wasm and Node')
    def test_actual_actor_frames_locked_motion_render_order_reset_and_hooks(self):
        text=source({1:(0,0)})
        text+='''
unsigned short ngc_scroll_next[2];
static unsigned short draws[60],used,reset_count,activate_count,detach_count;
void ngc_scroll_set(short x,short y){ngc_scroll_next[0]=x;ngc_scroll_next[1]=y;}
void ngc_sprites_begin(void){used=0;}
void ngc_sprites_end(void){draws[used++]=65535;}
void ngc_sprite_draw(unsigned short frame,short x,short y){draws[used++]=frame;draws[used++]=x;draws[used++]=y;}
void ngc_vm_projectile_reset(void){++reset_count;}
void ngc_vm_projectile_activate(unsigned char i){++activate_count;}
void ngc_vm_projectile_detach(unsigned char i){++detach_count;}
void ngc_vm_projectile_hit(unsigned char i,unsigned char p,unsigned char b,unsigned short a,unsigned short g){}
unsigned short probe(unsigned short op){
 if(op==0){ngc_actors_start(80,76);return reset_count;}
 if(op==1)return ngc_projectile_launch(7,2560,2560,64);
 if(op==2){ngc_actors_update(1);ngc_actors_render();++ngc_game_time;return proj_x[proj_head];}
 if(op==3)return draws[used-4];
 if(op==4)return draws[used-1];
 if(op==5){ngc_actor_deactivate(1);ngc_actor_deactivate(1);return detach_count;}
 if(op==6){ngc_actor_activate(1);return activate_count;}
 if(op==7){ngc_actors_load();return proj_head;}
 return 0;}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'probe.c').write_text(text)
            proc=subprocess.run([CLANG,'--target=wasm32','-std=c89','-pedantic-errors','-nostdlib','-O2',
                '-Wl,--no-entry','-Wl,--export-all',str(root/'probe.c'),'-o',str(root/'probe.wasm')],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            (root/'run.js').write_text("const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2]))).exports;console.log(JSON.stringify([0,1,2,3,4,5,6,7].map(x=>e.probe(x))));")
            proc=subprocess.run(['node',str(root/'run.js'),str(root/'probe.wasm')],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertEqual(json.loads(proc.stdout),[1,1,2592,9,65535,1,3,255])

@unittest.skipUnless(all(os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')),
                     'requires Toshiba, native emulator and BIOS')
class ProjectileSceneNativeTests(unittest.TestCase):
    def run_case(self,root,**kwargs):
        from tools.lab_projectile_scenes import run
        return run(root,os.environ['NGC_TEST_TOSHIBA'],os.environ['NGC_TEST_EMULATOR'],os.environ['NGC_TEST_BIOS'],**kwargs)

    def test_real_png_actor_order_locked_update_lifetime_and_larger_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            for capacity in (5,17):
                with self.subTest(capacity=capacity):
                    r=self.run_case(Path(tmp)/str(capacity),capacity=capacity)
                    self.assertTrue(r['passed'],r['frames_with_mismatch'])
                    self.assertEqual(r['dropped_frames'],[])

    def test_cold_scene_link_has_no_projectile_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=self.run_case(Path(tmp)/'cold',cold=True)
            self.assertTrue(r['passed']);self.assertFalse(r['launch_linked'])

    def test_missing_render_and_vm_locked_update_witnesses_fail_at_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            for witness in ('render','locked'):
                with self.subTest(witness=witness):
                    r=self.run_case(Path(tmp)/witness,witness=witness)
                    self.assertFalse(r['passed']);self.assertTrue(r['frames_with_mismatch'])

if __name__=='__main__':unittest.main()
