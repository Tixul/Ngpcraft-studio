"""Author types, resident frame mapping and released launch-method lowering."""
from dataclasses import replace
import json, os, shutil, subprocess, unittest, tempfile
from types import SimpleNamespace
from pathlib import Path
from ngpcraft.compiler import CompileError
from ngpcraft.projectiles import ProjectileRegistry, compile_projectiles, build_projectile_assets
from ngpcraft.projectile_runtime import projectile_source
from ngpcraft.sprites import NgpcSheet
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.assembler import assemble
from ngpcraft.gbvm.events import HANDLERS
from tools.reference import REFERENCE
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / 'tools/projectile_launch_oracle.js'


def raw(names=('idle','fire')):
    return dict(states=[{'name':n} for n in names], boundsX=-3, boundsY=-1,
                boundsWidth=7, boundsHeight=9)


def sheet(names=('idle','fire')):
    return NgpcSheet('test','8x8',(),((),(),()),(2,0,1,2),
                     ((0,1),(2,2),(3,3),(0,0))*4,names)


class ProjectileCompilerTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NGC_TEST_CLANG') and os.environ.get('NGC_TEST_TOSHIBA'),
                         'user-provided clang and Toshiba compilers required')
    def test_sparse_ids_execute_with_resolved_frames_and_compile_for_target(self):
        registry=ProjectileRegistry({'s':raw()})
        for i in range(9):registry.register({'spriteSheetId':'s','lifeTime':i},'scene:shot')
        result=compile_projectiles(registry.selections[7:], [('s',sheet())],registry.sheets)
        source='\n'.join(projectile_source(result.definitions,result.frames,capacity=2,width_tiles=20,height_tiles=18,
                                          definition_ids=result.definition_indices))
        source+='''
static unsigned short seen_frame;
unsigned char ngc_projectile_hit(const short *bounds,unsigned short x,unsigned short y,unsigned char mask,unsigned char group){return 0;}
void ngc_sprite_draw(unsigned short frame,short x,short y){seen_frame=frame;}
unsigned short probe(void){
    ngc_projectiles_init();
    if(ngc_projectile_launch(0u,1024u,1024u,64u))return 1u;
    if(!ngc_projectile_launch(7u,1024u,1024u,64u))return 2u;
    if(proj_head!=1u||proj_life[1]!=420u||proj_dx[1]!=64||proj_frame[1]!=2u)return 3u;
    ngc_projectiles_render(0,0);if(seen_frame!=1u)return 4u;
    if(ngc_projectile_launch(65535u,0u,0u,0u))return 5u;
    if(!ngc_projectile_launch(8u,1024u,1024u,0u))return 6u;
    if(proj_head!=0u||proj_life[0]!=480u)return 7u;
    if(ngc_projectile_launch(7u,0u,0u,0u))return 8u;
    return 0u;
}
'''
        from tools.lab_projectile_pool import compile_kernel
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);c=folder/'probe.c';wasm=folder/'probe.wasm';c.write_text(source,encoding='utf-8')
            compile=subprocess.run([os.environ['NGC_TEST_CLANG'],'--target=wasm32','-nostdlib','-O2','-fno-builtin',
                '-Wl,--no-entry','-Wl,--export-all',str(c),'-o',str(wasm)],capture_output=True,text=True)
            self.assertEqual(compile.returncode,0,compile.stderr)
            run=subprocess.run(['node','-e',
                "WebAssembly.instantiate(require('fs').readFileSync(process.argv[1]),{}).then(r=>{const code=r.instance.exports.probe();if(code)throw Error('probe '+code);console.log('ok')})",str(wasm)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            with patch('tools.lab_projectile_pool.generated_source',return_value=source):
                target=compile_kernel(folder/'target',os.environ['NGC_TEST_TOSHIBA'],2)
            self.assertEqual(target['returncode'],0,target['output']);self.assertTrue(target['object_exists'])

    def test_selected_pngs_only_and_existing_sheet_reuse(self):
        resources=list((REFERENCE/'appData/templates/gbs2/assets/sprites').rglob('*.gbsres'))
        self.assertTrue(resources)
        path=resources[0];resource=json.loads(path.read_text(encoding='utf-8'))
        sid=resource['id'];metadata={sid:resource,'unreachable':raw()}
        registry=ProjectileRegistry(metadata)
        registry.register({'spriteSheetId':sid},'scene:shot')
        project=SimpleNamespace(root=path.parent, settings={'spriteMode':'8x16','colorMode':'mixed'},
            by_type={'sprite':[(path.name,resource),('absent.png.gbsres',{'id':'unreachable',**raw()})],
                     'engineFieldValues':[]})
        # Sheets are read through scene_assets.sheet_image (GB or native colours).
        import ngpcraft.scene_assets as module
        with patch.object(module,'read_png',wraps=module.read_png) as read:
            pairs,result=build_projectile_assets(project,registry.selections)
            self.assertEqual(read.call_count,1)
            same,again=build_projectile_assets(project,registry.selections,pairs)
            self.assertEqual(read.call_count,1)
            self.assertEqual(same,pairs);self.assertEqual(again,result)
        with patch.object(module,'read_png',side_effect=AssertionError('unreachable PNG')):
            self.assertEqual(build_projectile_assets(project,[]),((),compile_projectiles([],[],{})))
        oversized=replace(sheet(),tiles=((0,)*64,)*513)
        with self.assertRaisesRegex(CompileError,'513 tiles'):
            build_projectile_assets(project,[],[('actor',oversized)])

    def test_sparse_type_ids_reach_generated_c_without_renumbering(self):
        registry=ProjectileRegistry({'s':raw()})
        for i in range(9):registry.register({'spriteSheetId':'s','lifeTime':i},'scene:shot')
        result=compile_projectiles(registry.selections[7:], [('s',sheet())],registry.sheets)
        lines=projectile_source(result.definitions,result.frames,capacity=17,width_tiles=20,height_tiles=18,
                                definition_ids=result.definition_indices)
        text='\n'.join(lines)
        self.assertIn('    case 7u:',text);self.assertIn('    case 8u:',text)
        self.assertNotIn('    case 0u:',lines) # exact type case line
        for ids in ((7,), (7,7), (7,65536),None):
            if ids is None:continue
            with self.subTest(ids=ids),self.assertRaises(CompileError):
                projectile_source(result.definitions,result.frames,capacity=5,width_tiles=20,height_tiles=18,definition_ids=ids)

    def test_registry_deduplicates_authored_types_not_launch_parameters(self):
        registry = ProjectileRegistry({'s':raw()})
        a = dict(spriteSheetId='s')
        self.assertEqual(registry.register(a,'scene:one'),0)
        self.assertEqual(registry.register({**a,'actorId':'another','angle':90,'x':42},'scene:two'),0)
        self.assertEqual(registry.register({**a,'lifeTime':1.001},'scene:three'),1)
        self.assertEqual(registry.register({**a,'collisionMask':['1','2']},'scene:four'),2)
        self.assertEqual(registry.register({**a,'collisionMask':['2','1']},'scene:five'),3)
        self.assertEqual(registry.selections[0].source,'scene:one')
        self.assertEqual(registry.selections[0].args()['spriteStateId'],'')

    def test_resident_frames_are_global_and_direction_ranges_inclusive(self):
        registry=ProjectileRegistry({'s':raw(),'t':raw()})
        for args in ({'spriteSheetId':'s'}, {'spriteSheetId':'t','spriteStateId':'fire'},
                     {'spriteSheetId':'s','lifeTime':1.001,'speed':1.234,'initialOffset':0.031}):
            registry.register(args,'scene:shot')
        compiled=compile_projectiles(registry.selections,
            [('actor',sheet()),('s',sheet()),('t',sheet())],registry.sheets)
        self.assertEqual(compiled.frames,(5,3,4,5,8,6,7,8))
        self.assertEqual(compiled.sheet_ids,('s','t'))
        self.assertEqual(compiled.definitions[1].animations,((4,5),(6,6),(7,7),(4,4)))
        self.assertEqual(compiled.definitions[0].bounds_subpx,(-96,127,-32,255))
        self.assertEqual(compiled.definitions[2].speed_subpx,39)
        self.assertEqual(compiled.definitions[2].initial_offset_subpx,0)
        self.assertEqual(compiled.definitions[2].life_frames,60)
        self.assertEqual(compiled.definition_indices,(0,1,2))
        # A scene subset retains project IDs, without a five-type truncation.
        subset=compile_projectiles(registry.selections[2:], [('s',sheet())],registry.sheets)
        self.assertEqual(subset.definition_indices,(2,))
        for i in range(20): registry.register({'spriteSheetId':'s','lifeTime':2+i},'scene:many')
        self.assertEqual(len(registry.selections),23)

    def test_empty_scene_and_default_state_need_no_png_or_first_empty_state(self):
        self.assertEqual(compile_projectiles([],[],{}).frames,())
        registry=ProjectileRegistry({'s':raw(('named',))})
        registry.register({'spriteSheetId':'s'},'scene:shot')
        result=compile_projectiles(registry.selections,[('s',sheet(('named',)))],registry.sheets)
        self.assertEqual(result.definitions[0].animations,((0,1),(2,2),(3,3),(0,0)))

    def test_located_refusals_for_lossy_author_values(self):
        bad=({'spriteSheetId':'missing'}, {'spriteStateId':'missing'}, {'speed':8},
             {'speed':float('nan')},{'speed':1e308},{'speed':True},{'speed':-1},{'lifeTime':1093},
             {'initialOffset':2048},{'animSpeed':256},{'animSpeed':1.5},
             {'loopAnim':1},{'destroyOnHit':None},{'collisionGroup':'invalid'},
             {'collisionMask':['invalid']},{'collisionMask':None},{'spriteStateId':None})
        for args in bad:
            with self.subTest(args=args),self.assertRaisesRegex(CompileError,'scene:shot'):
                ProjectileRegistry({'s':raw()}).register({'spriteSheetId':'s',**args},'scene:shot')
        registry=ProjectileRegistry({'s':raw()});registry.register({'spriteSheetId':'s'},'scene:shot')
        with self.assertRaisesRegex(CompileError,'scene:shot'):
            compile_projectiles(registry.selections,[],registry.sheets)
        with self.assertRaisesRegex(CompileError,'duplicate resident'):
            compile_projectiles([], [('s',sheet()),('s',sheet())],registry.sheets)

    def test_launches_assemble_without_leaking_rpn_stack(self):
        methods=('launch_projectile_in_direction','launch_projectile_in_angle',
                 'launch_projectile_in_angle_variable','launch_projectile_in_source_actor_direction',
                 'launch_projectile_in_actor_direction','launch_projectile_towards_actor')
        for method in methods:
            with self.subTest(method=method):
                out=[];b=ScriptBuilder(out,{})
                getattr(b,method)(17,-1.125,2.25)
                self.assertEqual(b.stack_ptr,0)
                b._pack_locals();b.script_end()
                _,data,macros,rpn=assemble(b.to_script_string('shot',False),{'VAR_VARIABLE_0':0})
                self.assertTrue(data);self.assertIn('VM_PROJECTILE_LAUNCH',macros)
                if method.endswith('towards_actor'):self.assertTrue(any('.ATAN2' in s for s in out))
        # Delivered since the author handler (tests/test_projectile_events.py).
        self.assertIn('EVENT_LAUNCH_PROJECTILE',HANDLERS)

    def test_builder_located_refusals_are_before_partial_emission(self):
        for index,x,y in ((256,0,0),(True,0,0),(0,1024,0),(0,0,-1024),(0,float('inf'),0),(0,1e308,0)):
            out=[];b=ScriptBuilder(out,{});b.source='scene:shot'
            with self.assertRaisesRegex(CompileError,'scene:shot'):b.launch_projectile_in_angle(index,x,y,0)
            self.assertEqual(out,[])

    @unittest.skipUnless(shutil.which('node') and REFERENCE.exists(),'released source and Node required')
    def test_executed_release_definition_compiler_matches_resolved_types(self):
        registry=ProjectileRegistry({'s':raw()})
        for state in ('','fire'):
            for speed in (0,0.031,1.234,2,7.968):
                for lifetime in (0,0.0083333333,0.025,1.001,4):
                    for offset in (0,0.031,256):
                        for loop,destroy in ((False,False),(False,True),(True,False),(True,True)):
                            registry.register(dict(spriteSheetId='s',spriteStateId=state,speed=speed,
                                lifeTime=lifetime,initialOffset=offset,loopAnim=loop,destroyOnHit=destroy,
                                collisionGroup='2',collisionMask=['player','1','3']),'scene:shot')
        result=compile_projectiles(registry.selections,[('s',sheet())],registry.sheets)
        self.assertEqual(len(result.definitions),600)
        sprite={'id':'s','symbol':'sprite',**raw(),'animationOffsets':sheet().animation_offsets}
        proc=subprocess.run(['node',str(ORACLE),str(REFERENCE/'src/lib/compiler/generateGBVMData.ts'),'definitions'],
            input=json.dumps(dict(projectiles=[s.args() for s in registry.selections],sprites=[sprite])),
            capture_output=True,text=True,check=True)
        rows=json.loads(proc.stdout)['results']
        self.assertEqual(len(rows),600)
        for definition,row in zip(result.definitions,rows):
            self.assertEqual(definition.bounds_subpx,tuple(row['bounds'][k] for k in ('left','right','top','bottom')))
            self.assertEqual(definition.animations,tuple(tuple(pair) for pair in row['animations']))
            self.assertEqual(definition.speed_subpx,row['move_speed'])
            self.assertEqual(definition.life_frames,row['life_time'])
            self.assertEqual(definition.initial_offset_subpx,row['initial_offset'])
            self.assertEqual(definition.animation_tick_mask,row['anim_tick'])
            self.assertEqual(definition.loop_animation,not row['anim_noloop'])
            self.assertEqual(definition.destroy_on_hit,not row['strong'])
            self.assertEqual(definition.collision_group,row['collision_group'])
            self.assertEqual(definition.collision_mask,row['collision_mask'])

    @unittest.skipUnless(shutil.which('node') and REFERENCE.exists(),'released source and Node required')
    def test_executed_release_methods_match_all_six_lowerings(self):
        pairs=[('launchProjectileInDirection','launch_projectile_in_direction',['up','right','down','left']),
               ('launchProjectileInAngle','launch_projectile_in_angle',[-1024.5,-256.5,-255.5,-0.5,0,0.5,255.5,256.5,1024.5]),
               ('launchProjectileInAngleVariable','launch_projectile_in_angle_variable',['0','7',{'type':'argument','symbol':'.SCRIPT_ARG_0','indirect':True}]),
               ('launchProjectileInSourceActorDirection','launch_projectile_in_source_actor_direction',[None]),
               ('launchProjectileInActorDirection','launch_projectile_in_actor_direction',[0,7,'.SCRIPT_ARG_1',{'type':'argument','symbol':'.SCRIPT_ARG_0','indirect':False}]),
               ('launchProjectileTowardsActor','launch_projectile_towards_actor',[0,7,'.SCRIPT_ARG_1',{'type':'argument','symbol':'.SCRIPT_ARG_0','indirect':False}])]
        cases=[];expected=[]
        for js,py,values in pairs:
            for index in (0,17,255):
                for x,y in ((0,0),(-1.125,2.25),(0.031,-0.031),(256,-256)):
                    for value in values:
                        for stack in (0,5):
                            args=[index,x,y]+([] if value is None else [value])
                            cases.append(dict(method=js,args=args,stack=stack))
                            out=[];b=ScriptBuilder(out,{});b.stack_ptr=stack
                            getattr(b,py)(*args)
                            expected.append(dict(output=[s.rstrip() for s in out],stack=b.stack_ptr))
        proc=subprocess.run(['node',str(ORACLE),str(REFERENCE/'src/lib/compiler/scriptBuilder/scriptBuilder.ts')],
                            input=json.dumps(cases),text=True,capture_output=True,check=True)
        actual=json.loads(proc.stdout)
        self.assertEqual(len(actual['results']),len(expected))
        for case,want,got in zip(cases,expected,actual['results']):
            with self.subTest(case=case):self.assertEqual(want,got)


if __name__=='__main__':unittest.main()
