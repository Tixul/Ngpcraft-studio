import json
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from ngpcraft.background_priority import compile_priority_background
from ngpcraft.compiler import CompileError
from tools.lab_background_priority import source_scene, build_case


def layer_pixel(asset,plane,x,y):
    layer=asset.planes.index(plane)
    cell=asset.maps[layer][(y//8)*(asset.width//8)+x//8]
    sx=7-x%8 if cell&0x8000 else x%8
    sy=7-y%8 if cell&0x4000 else y%8
    tile=asset.tiles[(cell&511)-asset.tile_base]
    word=int.from_bytes(tile[sy*2:sy*2+2],"little")
    index=(word >> (14-2*sx))&3
    return None if index==0 else asset.palettes[layer][(cell>>9)&15][index-1]


class PriorityAllocationTests(unittest.TestCase):
    def test_mask_preserves_gb_zero_and_duplicate_backdrop_colour(self):
        source=source_scene()
        asset=compile_priority_background(source)
        self.assertEqual(asset.planes,(1,2))
        for y in range(source.height):
            for x in range(source.width):
                n=y*source.width+x
                masked=bool(source.attributes[(y//8)*(source.width//8)+x//8]&128) and bool(source.indices[n])
                front=layer_pixel(asset,1,x,y)
                self.assertEqual(front,source.pixels[n] if masked else None)
                back=layer_pixel(asset,2,x,y)
                self.assertEqual(front if front is not None else back if back is not None else asset.backdrop,source.pixels[n])

    def test_no_priority_needs_no_mask_plane_or_extra_tiles(self):
        from ngpcraft.scene_planes import compile_scene_background
        source=source_scene()
        source=replace(source,attributes=bytes(len(source.attributes)))
        self.assertEqual(compile_priority_background(source),compile_scene_background(source.width,source.height,source.pixels,where="test"))

    def test_priority_on_gb_zero_needs_no_mask(self):
        from ngpcraft.scene_planes import compile_scene_background
        source=source_scene()
        source=replace(source,indices=bytes(len(source.indices)))
        self.assertEqual(compile_priority_background(source),
                         compile_scene_background(source.width,source.height,source.pixels,where="test"))

    def test_invalid_indices_and_conflicting_complement_are_refused(self):
        source=source_scene()
        with self.assertRaisesRegex(CompileError,"invalid GB source"):
            compile_priority_background(replace(source,indices=bytes([4])*len(source.indices)))
        pixels=tuple((n//64)%2*16+n%4 for n in range(len(source.pixels)))
        with self.assertRaisesRegex(CompileError,"conflicts with A4"):
            compile_priority_background(replace(source,pixels=pixels))


class PriorityBackdropAuditTests(unittest.TestCase):
    def test_disjoint_four_colour_cells_prove_impossibility(self):
        from ngpcraft.scene_assets import BackgroundSource
        from ngpcraft.background_priority import priority_backdrop_audit
        source=BackgroundSource(16,8,tuple(x//8*4+x%4 for y in range(8) for x in range(16)),bytes(128),bytes(2),"test")
        report=priority_backdrop_audit(source)
        self.assertEqual(report["colour_condition"],"impossible")
        self.assertEqual([v["tile"] for v in report["conflict_witness"]],[[0,0],[1,0]])
        self.assertEqual(report["candidate_backdrops"],[])

    def test_masked_colours_do_not_constrain_rear_backdrop(self):
        from ngpcraft.scene_assets import BackgroundSource
        from ngpcraft.background_priority import priority_backdrop_audit
        source=BackgroundSource(16,8,tuple(x//8*4+x%4 for y in range(8) for x in range(16)),bytes([1])*128,bytes([0,128]),"test")
        report=priority_backdrop_audit(source)
        self.assertEqual(report["candidate_backdrops"],[0,1,2,3])
        self.assertEqual(report["colour_condition"],"possible")
        self.assertFalse(report["budgets_checked"])


class PriorityProjectTests(unittest.TestCase):
    def test_priority_with_text_selects_shared_vblank_restore(self):
        from tools.demo_text_priority import project_case
        from ngpcraft.pipeline import export_project
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            project=project_case(out)
            manifest=export_project(project,out/"export")
            self.assertEqual(manifest["scene_engine"]["background_priority"]["status"],"emitted")
            self.assertIn("text_shared",manifest["features"])
            self.assertIn("ngc_ui_vblank();",(out/"export/src/startup.c").read_text())

    def test_priority_without_text_has_no_shared_text_runtime(self):
        from tools.demo_text_priority import project_case
        from ngpcraft.pipeline import export_project
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            project=project_case(out,dialogue=False)
            manifest=export_project(project,out/"export")
            self.assertEqual(manifest["scene_engine"]["background_priority"]["status"],"emitted")
            self.assertNotIn("text",manifest["features"])
            self.assertNotIn("text_shared",manifest["features"])
            self.assertNotIn("ngc_ui_sprite_cut",(out/"export/src/scene_sprites.c").read_text())
            self.assertNotIn("ngc_ui_vblank",(out/"export/src/startup.c").read_text())


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA","NGC_TEST_EMULATOR","NGC_TEST_BIOS")),"requires Toshiba, native emulator and BIOS")
class PriorityRomTests(unittest.TestCase):
    def check(self,witness):
        from tools.probe_stream import observe
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"case"
            build_case(out,os.environ["NGC_TEST_TOSHIBA"],front_witness=witness,count=60)
            result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
            return result

    def test_priority_dialogue_and_missing_vblank_restore_witness(self):
        import subprocess,sys,hashlib
        from tools.demo_text_priority import build_case as project_build
        from tools.demo_gbs_scene import write_json
        from tools.check_text import check,passed,proof_profiles
        from ngpcraft.toolchain import build_rom
        for witness in (False,True):
            with self.subTest(witness=witness),tempfile.TemporaryDirectory() as temp:
                out=Path(temp)
                if witness:
                    from tools.demo_text_priority import project_case
                    from ngpcraft.pipeline import export_project
                    project=project_case(out)
                    export_project(project,out/"export")
                else:
                    project_build(out,os.environ["NGC_TEST_TOSHIBA"])
                if witness:
                    path=out/"export/src/startup.c"
                    text=path.read_text().replace("    ngc_ui_vblank();", "    /* witness: omitted VBlank restore */")
                    tmp=path.with_suffix(".tmp")
                    tmp.write_text(text,encoding="utf-8")
                    tmp.replace(path)
                    manifest=json.loads((out/"export/export.json").read_text())
                    manifest["sha256"]["src/startup.c"]=hashlib.sha256(path.read_bytes()).hexdigest()
                    write_json(out/"export/export.json",manifest)
                    build_rom(out/"export",os.environ["NGC_TEST_TOSHIBA"])
                write_json(out/"inputs.json",[[150,158,16],[300,308,16]])
                proc=subprocess.run([sys.executable,"-B","-m","tools.probe_gbvm",str(out/"export"),
                    "--emulator",os.environ["NGC_TEST_EMULATOR"],"--bios",os.environ["NGC_TEST_BIOS"],
                    "--out",str(out/"probe"),"--frames","420","--inputs",str(out/"inputs.json"),
                    "--shots",",".join(map(str,range(1,421))),"--sprite-mask-trace","--duty-cycle","0.25"],
                    capture_output=True,text=True,timeout=180)
                self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
                result=check(out,**proof_profiles(out)[0])
                self.assertGreater(result["compared_frames"],300)
                if witness:
                    self.assertFalse(passed(result))
                    self.assertTrue(result["background_errors"])
                else:
                    self.assertTrue(passed(result),json.dumps(result))

    def test_ordinary_project_priority_mask_in_native_rom(self):
        from tools.demo_text_priority import build_case as project_build,prepare_mask_probe
        from tools.probe_stream import observe
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            project_build(out,os.environ["NGC_TEST_TOSHIBA"],dialogue=False)
            prepare_mask_probe(out,count=60)
            result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
            self.assertTrue(result["passed"],json.dumps(result))
            self.assertEqual(result["checked_scroll_planes"],[1,2])

    def test_priority_mask_and_scroll_in_native_rom(self):
        result=self.check(False)
        self.assertTrue(result["passed"],json.dumps(result))
        self.assertEqual(result["checked_scroll_planes"],[1,2])

    def test_front_sprite_mutation_is_detected(self):
        result=self.check(True)
        self.assertFalse(result["passed"])
        self.assertGreater(len(result["frames_with_mismatch"]),0)
