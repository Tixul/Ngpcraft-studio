"""New behavior revealed by the MicroDoom legacy comparison."""
import json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from ngpcraft.project import load_project,write_project
from ngpcraft.gbvm.builder import ScriptBuilder,NotDelivered
from ngpcraft.gbvm.events import _script_symbol
from ngpcraft.scene_collisions import collision_capacity
from ngpcraft.scene_assets import sprite_palettes,mono_palette,background_source,rgb444,DMG_PALETTE
from tools.lab_actor_reads import project_case,run
from tools.demo_gbs_scene import write_json

class LegacyComparisonTests(unittest.TestCase):
    def test_partial_settings_merge_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=project_case(root)
            write_json(path/'project/user_settings.gbsres',{'_resourceType':'settings','startX':9,'comparisonAnnotation':'preserved'})
            project=load_project(path)
            self.assertEqual(project.settings['startX'],9)
            self.assertEqual(len(project.by_type['settings']),2)
            out=write_project(project,root/'roundtrip')
            self.assertEqual(json.loads((out/'project/user_settings.gbsres').read_text()),json.loads((path/'project/user_settings.gbsres').read_text()))
            self.assertEqual(load_project(out).settings['startX'],9)

    def test_duplicate_symbols_have_distinct_entries(self):
        shared={'symbols':{}}
        self.assertEqual([_script_symbol('actor_zombi2_update',shared) for _ in range(3)],
                         ['actor_zombi2_update','actor_zombi2_update_0','actor_zombi2_update_1'])

    def test_fetch_shares_xy_and_reloads_next_expression(self):
        out=[];b=ScriptBuilder(out,{})
        fetch=[{'local':name,'value':{'type':'actorPosition','target':'player'}} for name in ('x','y')]
        lookup=b._perform_fetch_operations(fetch)
        self.assertEqual(lookup['x'],lookup['y'])
        b._perform_fetch_operations(fetch)
        self.assertEqual(sum('VM_ACTOR_GET_POS' in row for row in out),2)
        with self.assertRaises(NotDelivered):b._perform_fetch_operations([{'local':'frame','value':{'type':'actorFrame','target':'player'}}])

    def test_custom_direction_reference_is_indirect(self):
        out=[];b=ScriptBuilder(out,{})
        b.actor_get_direction({'type':'argument','symbol':'.SCRIPT_ARG_0_VARIABLE','indirect':True})
        self.assertTrue(any('VM_ACTOR_GET_DIR' in row for row in out))
        self.assertTrue(any('VM_SET_INDIRECT' in row for row in out))

    def test_monochrome_bg_and_sprite_shade_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=project_case(Path(tmp));settings=path/'project/settings.gbsres'
            data=json.loads(settings.read_text());data.update(colorMode='mono',sgbEnabled=False,defaultMonoOBP0=[0,1,3],defaultMonoOBP1=[0,2,3])
            write_json(settings,data);project=load_project(path);scene=project.scenes[0]
            palettes=sprite_palettes(project,scene)
            self.assertEqual(palettes[0],tuple(rgb444(DMG_PALETTE[i]) for i in (0,1,3)))
            self.assertEqual(palettes[1],tuple(rgb444(DMG_PALETTE[i]) for i in (0,2,3)))
            bg=background_source(project,scene);colors=mono_palette(project,scene,'monoBGP','defaultMonoBGP')
            self.assertEqual(bg.pixels,tuple(colors[i] for i in bg.indices));self.assertEqual(set(bg.attributes),{0})

    def test_modal_text_cannot_grow_contact_queue(self):
        def capacity(events):
            return collision_capacity([SimpleNamespace(path='actor',branches=[(0,'script')],entity={'script':events})])
        self.assertEqual(capacity([{'command':'EVENT_TEXT','args':{'text':'Wait for A'}}]),4)
        self.assertEqual(capacity([{'command':'EVENT_TEXT','args':{'text':'Timed','closeWhen':'text'}}]),8)
        self.assertEqual(capacity([{'command':'EVENT_TEXT','args':{'text':'Timed','closeWhen':'text','closeDelayUnits':'frames','closeDelayFrames':120}}]),16)
        self.assertEqual(capacity([{'command':'EVENT_SWITCH_SCENE'},{'command':'EVENT_LOOP'}]),4)
        self.assertGreater(capacity([{'command':'EVENT_FADE_OUT','args':{'speed':6}}]),4)

    @unittest.skipUnless(all(os.environ.get(k) for k in ('NGC_TEST_TOSHIBA','NGC_TEST_EMULATOR','NGC_TEST_BIOS')),'native lab tools required')
    def test_native_live_properties_and_four_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=run(Path(tmp)/'lab',os.environ['NGC_TEST_TOSHIBA'],os.environ['NGC_TEST_EMULATOR'],os.environ['NGC_TEST_BIOS'])
            self.assertEqual(report['mismatches'],{})
