import os
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from ngpcraft.scene_assets import BackgroundSource
from ngpcraft.sprites import HardwareSprite,NgpcSheet
from tools.lab_priority_complement import clip_frame,source_scene,build_case


class ClipTests(unittest.TestCase):
    def sheet(self,flip=False):
        spr=HardwareSprite(0,0,0,flip,flip,0)
        return NgpcSheet("test","8x8",(tuple((x+y*8)%4 for y in range(8) for x in range(8)),),((spr,),),(0,),())

    def test_gb_zero_survives_even_when_rgb_is_identical(self):
        source=BackgroundSource(8,8,(0x111,)*64,bytes(n%4 for n in range(64)),bytes([128]),"test")
        sheet=self.sheet()
        tile=clip_frame(source,sheet,sheet.frames[0],0,0)[0][1]
        # Use an opaque sprite at every source index to distinguish GB0.
        sheet=replace(sheet,tiles=((1,)*64,))
        tile=clip_frame(source,sheet,sheet.frames[0],0,0)[0][1]
        self.assertEqual(tile,tuple(1 if n%4==0 else 0 for n in range(64)))

    def test_flips_are_flattened_before_world_space_mask(self):
        sheet=self.sheet(True)
        source=BackgroundSource(8,8,(0x111,)*64,bytes([1])+bytes(63),bytes([128]),"test")
        spr,tile=clip_frame(source,sheet,sheet.frames[0],0,0)[0]
        self.assertFalse(spr.hflip or spr.vflip)
        self.assertEqual(tile[0],0)
        self.assertEqual(tile[1:],tuple(reversed(sheet.tiles[0]))[1:])

    def test_outside_background_and_nonpriority_cells_are_preserved(self):
        sheet=self.sheet()
        source=replace(source_scene(),attributes=bytes(len(source_scene().attributes)))
        self.assertEqual(clip_frame(source,sheet,sheet.frames[0],0,0)[0][1],sheet.tiles[0])
        source=replace(source,attributes=bytes([128])*len(source.attributes))
        self.assertEqual(clip_frame(source,sheet,sheet.frames[0],-8,-8)[0][1],sheet.tiles[0])


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA","NGC_TEST_EMULATOR","NGC_TEST_BIOS")),"requires Toshiba, native emulator and BIOS")
class ClipRomTests(unittest.TestCase):
    def test_exact_composition_and_unclipped_witness(self):
        from tools.probe_stream import observe
        import json
        with tempfile.TemporaryDirectory() as temp:
            for witness in (False,True):
                out=Path(temp)/str(witness)
                build_case(out,os.environ["NGC_TEST_TOSHIBA"],witness=witness)
                allocation=json.loads((out/"allocation.json").read_text())
                self.assertGreater(allocation["complement_cells"],0)
                self.assertLessEqual(allocation["total_tiles"],512)
                result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
                self.assertEqual(result["frames"],40)
                self.assertEqual(result["passed"],not witness)
                self.assertEqual(len(result["frames_with_mismatch"]),40 if witness else 0)
                self.assertFalse(result["dropped_frames"])


class DynamicBudgetTests(unittest.TestCase):
    def test_tile_reservation_refuses_overflow(self):
        from ngpcraft.sprite_priority import masked_sprites_source
        from ngpcraft.compiler import CompileError
        for base,slots in ((505,8),(-1,8),(0,65),(0,0)):
            with self.assertRaisesRegex(CompileError,"reservation exceeds VRAM"):
                masked_sprites_source("",[],source_scene(),base,slots)


@unittest.skipUnless(all(os.environ.get(k) for k in ("NGC_TEST_TOSHIBA","NGC_TEST_EMULATOR","NGC_TEST_BIOS")),"requires Toshiba, native emulator and BIOS")
class DynamicRomTests(unittest.TestCase):
    def test_dynamic_composition_and_missing_mask_witness(self):
        from tools.probe_stream import observe
        import json
        with tempfile.TemporaryDirectory() as temp:
            for witness in (False,True):
                out=Path(temp)/str(witness)
                build_case(out,os.environ["NGC_TEST_TOSHIBA"],witness=witness,dynamic=True)
                manifest=json.loads((out/"export/export.json").read_text())
                self.assertEqual(manifest["dynamic_priority"]["cache_tiles"],8)
                self.assertEqual(manifest["dynamic_priority"]["shadow_bytes"],128)
                allocation=json.loads((out/"allocation.json").read_text())
                self.assertEqual(allocation["total_tiles"],45)
                result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
                self.assertEqual(result["passed"],not witness)
                self.assertEqual(len(result["frames_with_mismatch"]),40 if witness else 0)
                self.assertFalse(result["dropped_frames"])
                self.assertLess(result["max_commit_lines"],result["vblank_lines"])

    def test_default_export_does_not_include_dynamic_mask(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"case"
            build_case(out,os.environ["NGC_TEST_TOSHIBA"])
            manifest=json.loads((out/"export/export.json").read_text())
            self.assertNotIn("dynamic_priority",manifest)
            source=(out/"export/src/scene_sprites.c").read_text()
            self.assertNotIn("ngc_mask_",source)

    def test_dynamic_mask_at_background_edges(self):
        from tools.probe_stream import observe
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"edge"
            build_case(out,os.environ["NGC_TEST_TOSHIBA"],dynamic=True,edge=True)
            result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
            self.assertTrue(result["passed"])
            self.assertFalse(result["frames_with_mismatch"])

    def test_cache_survives_frame_changes_and_slot_reuse(self):
        from tools.probe_stream import observe
        from ngpcraft.sprite_priority import masked_sprites_source
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            for witness in (False,True):
                out=Path(temp)/str(witness)
                def emitter(*args, **kwargs):
                    source=masked_sprites_source(*args, **kwargs)
                    if witness: source=source.replace("cache->key == key", "1u")
                    return source
                with patch("ngpcraft.sprite_priority.masked_sprites_source",side_effect=emitter):
                    build_case(out,os.environ["NGC_TEST_TOSHIBA"],dynamic=True,stationary=True,stress=True)
                result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
                self.assertEqual(result["passed"],not witness)
                if witness:self.assertGreater(len(result["frames_with_mismatch"]),0)

    def test_stationary_world_sprites_reuse_mask_while_camera_moves(self):
        from tools.probe_stream import observe
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"stationary"
            build_case(out,os.environ["NGC_TEST_TOSHIBA"],dynamic=True,stationary=True)
            result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
            self.assertTrue(result["passed"])
            self.assertLess(result["max_tick_duration_lines"],40)
            self.assertLess(result["max_commit_lines"],5)

    def test_sparse_priority_entry_and_exit_and_wrong_bypass_witness(self):
        from tools.probe_stream import observe
        from ngpcraft.sprite_priority import masked_sprites_source
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            for witness in (False,True):
                out=Path(temp)/str(witness)
                def emitter(*args, **kwargs):
                    source=masked_sprites_source(*args, **kwargs)
                    if witness:source=source.replace("if (sx > 95 ||", "if (1u || sx > 95 ||")
                    return source
                with patch("ngpcraft.sprite_priority.masked_sprites_source",side_effect=emitter):
                    build_case(out,os.environ["NGC_TEST_TOSHIBA"],dynamic=True,sparse=True,count=80)
                result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
                self.assertEqual(result["passed"],not witness)
                if witness:self.assertGreater(len(result["frames_with_mismatch"]),0)

    def test_real_town_export_and_missing_mask_witness(self):
        import json
        from test_scene_export import export_case
        from ngpcraft.project import load_project
        from ngpcraft.scene_assets import background_source
        from ngpcraft.sprite_priority import masked_sprites_source
        from tools.probe_stream import observe
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            for witness in (False,True):
                out=Path(temp)/str(witness)
                out.mkdir()
                def emitter(*args, **kwargs):
                    renderer=masked_sprites_source(*args, **kwargs)
                    if witness: renderer=renderer.replace("if (sx > ","if (1u || sx > ",1)
                    return renderer
                with patch("ngpcraft.sprite_priority.masked_sprites_source",side_effect=emitter):
                    manifest=export_case(out,start=(12,43),ticks=60)
                report=manifest["scene_engine"]
                self.assertEqual(report["background_priority"]["status"],"emitted_dynamic")
                self.assertEqual(report["dynamic_priority"]["cache_tiles"],40)
                project=load_project(out/"project")
                source=background_source(project,project.scenes[0])
                mask=bytes(bool(source.attributes[(y//8)*(source.width//8)+x//8]&128) and bool(source.indices[y*source.width+x])
                           for y in range(source.height) for x in range(source.width))
                (out/"scene.gb-priority").write_bytes(mask)
                data=json.loads((out/"source.json").read_text())
                data["gb_priority_mask"]="scene.gb-priority"
                (out/"source.json").write_text(json.dumps(data))
                result,_=observe(out,os.environ["NGC_TEST_EMULATOR"],os.environ["NGC_TEST_BIOS"])
                self.assertEqual(result["passed"],not witness)
                if witness:self.assertGreater(len(result["frames_with_mismatch"]),0)
