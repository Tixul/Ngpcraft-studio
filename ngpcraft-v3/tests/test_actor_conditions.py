"""Actor predicates: strict axes, unit quantization, selected runtime, native ROM."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.lab_actor_conditions import project_case, run, reference_run_conditions, reference_source
from tools.platform_oracle import project_case as plain_platform
from tools.reference import REFERENCE

ENV=[os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS','NGC_TEST_CLANG')]
def num(n):return {'type':'number','value':n}

class ActorConditionBuilderTests(unittest.TestCase):
    def test_relative_axes_strictness_branch_order_and_stack(self):
        source=(REFERENCE/'src/lib/compiler/scriptBuilder/scriptBuilder.ts').read_text()
        self.assertIn('ifActorRelativeToActor = (',source)
        for direction,offset,op in [('left',1,'.LT'),('right',1,'.GT'),('up',2,'.LT'),('down',2,'.GT')]:
            out=[];b=ScriptBuilder(out,{'scene':{'actors':[{'id':'a'}]}})
            b.actor_set_active('player')
            b.if_actor_relative_to_actor(direction,'a',lambda:out.append(';yes'),lambda:out.append(';no'))
            reads=[line.strip() for line in out if 'VM_ACTOR_GET_POS' in line]
            self.assertEqual(len(reads),2)
            self.assertIn(f'.LOCAL_ACTOR + {offset}', '\n'.join(out))
            self.assertIn(f'.R_OPERATOR {op}', '\n'.join(out))
            self.assertLess(out.index(';yes'),out.index(';no'))
            self.assertEqual(b.stack_ptr,0)

    def test_pixel_and_tile_units_and_calculated_coordinates(self):
        for units,shift in [('pixels',5),('tiles',8)]:
            out=[];b=ScriptBuilder(out,{'scene':{'actors':[]}})
            b.if_actor_at_position_by_script_values('player',{'type':'add','valueA':num(3),'valueB':num(4)},num(8),units=units)
            text='\n'.join(out)
            self.assertEqual(text.count(f'.R_INT8     {shift}'),2)
            self.assertEqual(text.count('.R_OPERATOR .SHR'),2)
            self.assertEqual(text.count('.R_OPERATOR .EQ'),2)
            self.assertIn('.R_OPERATOR .AND',text)
            self.assertIn('.R_INT16    7',text)
            self.assertEqual(b.stack_ptr,0)

    def test_disable_else_does_not_compile_the_false_path(self):
        for name,args in [('EVENT_IF_ACTOR_RELATIVE_TO_ACTOR',{'operation':'left','otherActorId':'player'}),
                          ('EVENT_IF_ACTOR_AT_POSITION',{'x':num(0),'y':num(0)})]:
            paths=[];b=ScriptBuilder([],{'scene':{'actors':[]},'compileEvents':lambda b,p:paths.append(p)})
            HANDLERS[name]({'actorId':'player','true':['yes'],'false':['no'],'__disableElse':True,**args},b)
            self.assertEqual(paths,[['yes'],[]])

    def test_invalid_operation_actor_and_units_are_refused(self):
        b=ScriptBuilder([],{'scene':{'actors':[]}})
        with self.assertRaisesRegex(CompileError,'Missing operation'):b.if_actor_relative_to_actor('near','player')
        # Unknown actor: GB Studio's fallback with a located warning (decision A9).
        b.if_actor_relative_to_actor('left','missing'); self.assertIn('not in this scene', b.options['warnings'][-1])
        with self.assertRaisesRegex(CompileError,'tiles or pixels'):
            b.if_actor_at_position_by_script_values('player',num(0),num(0),units='meters')

    def test_unrequested_reader_is_absent_and_requested_stack_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'base';base.mkdir();requested=root/'requested';requested.mkdir()
            m=export_project(plain_platform(base),base/'export')
            self.assertNotIn('actor_positions',m['features'])
            self.assertNotIn('ngc_actor_get_pos',(base/'export/src/scene_actors.c').read_text())
            m=export_project(project_case(requested),requested/'export')
            self.assertIn('actor_positions',m['features'])
            self.assertLess(m['gbvm']['stack_budget']['words_per_context'],64)
            self.assertNotIn('act_state_anims',(requested/'export/src/scene_actors.c').read_text())

    def test_reader_dispatches_in_a_multiple_scene_export(self):
        from tools.lab_scene_cuts import project_case as cuts
        from tools.demo_gbs_scene import write_json
        from tools.lab_actor_conditions import branch,ev,set_var
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project,_=cuts(root)
            for path in project.rglob('scene.gbsres'):
                scene=json.loads(path.read_text())
                scene['script'].insert(0,branch(ev('where','EVENT_IF_ACTOR_AT_POSITION',actorId='player',x=num(0),y=num(0)),
                                              [set_var(30,1)],[set_var(30,2)]))
                write_json(path,scene)
            m=export_project(project,root/'export')
            self.assertIn('actor_positions',m['features'])
            if ENV[0]:build_rom(root/'export',ENV[0])

@unittest.skipUnless(ENV[3],'requires Clang wasm and Node')
class OriginalVMTests(unittest.TestCase):
    def test_all_word_values_preserve_id_and_match_original_position_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);export_project(project_case(root),root/'export')
            generated=re.search(r'void ngc_actor_get_pos\([^)]*\)\s*\{.*?\n\}',
                                (root/'export/src/scene_actors.c').read_text(),re.S).group(0)
            source=reference_source()+'''\nstatic unsigned short act_x[2],act_y[2];\n'''+generated+'''
unsigned exhaustive(void){unsigned i,errors=0;unsigned short p[3];SCRIPT_CTX ctx;ctx.stack_ptr=stack;
for(i=0;i<65536;i++){act_x[1]=actors[1].pos.x=i;act_y[1]=actors[1].pos.y=65535-i;
p[0]=script_memory[0]=257;p[1]=p[2]=0;vm_actor_get_pos(&ctx,0);ngc_actor_get_pos((UBYTE)p[0],p);
if(p[0]!=(UWORD)script_memory[0]||p[1]!=(UWORD)script_memory[1]||p[2]!=(UWORD)script_memory[2])errors++;}return errors;}
'''
            (root/'reference.c').write_text(source)
            p=subprocess.run([ENV[3],'--target=wasm32','-nostdlib','-O2','-Wno-everything','-Wl,--no-entry','-Wl,--export-all',
                              str(root/'reference.c'),'-o',str(root/'reference.wasm')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            js="const fs=require('fs');const e=new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[1]))).exports;process.stdout.write(String(e.exhaustive()));"
            p=subprocess.run(['node','-e',js,str(root/'reference.wasm')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout,'0')

    def test_signed_relative_and_logical_quantization_boundary_cases(self):
        rows=[{'player':[32768,65535],'actor':[32767,0]},
              {'player':[1,1],'actor':[96*32+31,32*32+31]},
              {'player':[1,1],'actor':[12*256+255,4*256+255]},
              {'player':[0,0],'actor':[65535,65535]}]
        with tempfile.TemporaryDirectory() as tmp:
            result=reference_run_conditions(Path(tmp),rows,ENV[3])
        self.assertEqual(result[0]['0'],1) # signed -32768 < 32767
        self.assertEqual(result[0]['3'],0) # signed -1 > 0 is false
        self.assertEqual(result[1]['5'],1) # fractional pixel is inside the cell
        self.assertEqual(result[2]['4'],1) # fractional tile is inside the cell
        self.assertEqual(result[3]['4'],0)

@unittest.skipUnless(all(ENV),'requires Toshiba, native emulator, BIOS, Clang and Node')
class ActorConditionRomTests(unittest.TestCase):
    def test_platformer_branches_pixels_physics_and_equality(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'proof',*ENV,frames=540)
        self.assertEqual(r['condition_mismatches'],0,r['condition_first'])
        self.assertEqual(r['pixel_mismatch_frames'],0,r['pixel_first'])
        self.assertEqual(r['physics_mismatch_ticks'],0,r['physics_first'])
        self.assertEqual(r['dropped_frames'],[])
        for i in range(6):self.assertEqual(r['values_seen'][str(i)],[0,1])
        for i in range(8,12):self.assertEqual(r['values_seen'][str(i)],[0])
        self.assertEqual(r['values_seen']['6'],[0]);self.assertEqual(r['values_seen']['14'],[0])
        self.assertEqual(r['values_seen']['13'],[7])

    def test_mutation_position_reader_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,frames=300,witness='position')
        self.assertGreater(r['condition_mismatches'],0)

    def test_mutation_non_strict_comparison_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:r=run(Path(tmp)/'witness',*ENV,frames=300,witness='comparison')
        self.assertGreater(r['condition_mismatches'],0)

if __name__=='__main__':unittest.main()
