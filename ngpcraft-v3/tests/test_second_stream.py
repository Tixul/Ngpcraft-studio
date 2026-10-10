"""Project integration and independent native proof of the second background."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.background_stream import stream_source
from ngpcraft.compiler import CompileError
from ngpcraft.pipeline import export_project
from ngpcraft.project import ProjectError, load_project, write_project
from ngpcraft.runtime_units import public_symbols
from ngpcraft.scene_assets import decompress_tile_colors
from ngpcraft.second_background import (compile_second_background, SCROLL_RATES, is_rate,
                                        loop_source, rate_offset)
from ngpcraft.second_stream import second_stream_source
from ngpcraft.toolchain import build_rom
from tools.check_text import check, passed
from tools.demo_gbs_scene import write_json
from tools.demo_scene_switch import TOWN, CAVE, GBS2, ev, num, set_script
from tools.demo_second_background import project_case
from tools.lab_second_stream import _source, build_case, observe

ROOT = Path(__file__).resolve().parents[1]
ENV = [os.environ.get(k) for k in ('NGC_TEST_TOSHIBA', 'NGC_TEST_EMULATOR', 'NGC_TEST_BIOS')]


class SecondStreamTests(unittest.TestCase):
    def allocation(self, main_size=(512, 512), front_size=(320, 272)):
        return compile_second_background(_source(*main_size, False), _source(*front_size, True))

    def test_shared_bank_single_load_and_no_mutual_plane_clear(self):
        source = second_stream_source(self.allocation())
        self.assertEqual(source.count('main_bg_tiles['), 2)  # declaration and loader
        self.assertNotIn('front_bg_tiles', source)
        self.assertNotIn('Clear the unused plane', source)
        self.assertEqual(source.count('0x8005 ='), 1)
        self.assertEqual(source.count('0x8030 ='), 1)
        self.assertIn('unsigned short ngc_front_scroll[2];', source)

    def test_maps_keep_dimensions_and_two_full_repaints_interleave_screen_rows(self):
        source = second_stream_source(self.allocation(), scroll_x='1/2', scroll_y='1/4')
        self.assertIn('main_bg_map[4096]', source)
        self.assertIn('front_bg_map[1360]', source)
        self.assertIn('ngc_front_scroll_set((short)(ngc_scroll_next[0] >> 1), (short)(ngc_scroll_next[1] >> 2))', source)
        self.assertIn('ngc_main_stream_row((short)(ty + c), tx);', source)
        self.assertIn('ngc_front_stream_row((short)(fy + c), fx);', source)

    def test_text_reserves_front_ring_only_for_small_maps(self):
        allocation = self.allocation((192, 160), (192, 160))
        ordinary = second_stream_source(allocation)
        text = second_stream_source(allocation, reserve_ui=True)
        self.assertNotIn('ngc_front_stream_row(', ordinary)
        self.assertIn('ngc_front_stream_row(', text)
        self.assertNotIn('ngc_main_stream_row(', text)
        self.assertNotIn('ngc_split_', text)

    def test_fixed_and_all_declared_rates_are_compile_time_values(self):
        allocation = self.allocation((160, 152), (160, 152))
        for rate, shift in SCROLL_RATES.items():
            source = second_stream_source(allocation, scroll_x=rate, scroll_y=rate)
            wanted = 'ngc_front_scroll_set(0, 0);' if shift is None else f'ngc_front_scroll_set((short)(ngc_scroll_next[0] >> {shift}), (short)(ngc_scroll_next[1] >> {shift}));'
            self.assertIn(wanted, source)
        for rate in (True, 0, None, [], '0', '1/3', '1/512'):
            with self.subTest(rate=rate), self.assertRaisesRegex(CompileError, 'scroll_x: expected'):
                second_stream_source(allocation, scroll_x=rate)

    def test_percent_rates_are_floor_of_p_times_n_over_100(self):
        allocation = self.allocation((160, 152), (160, 152))
        source = second_stream_source(allocation, scroll_x='75%', scroll_y='150%')
        self.assertIn('ngc_front_scroll_set((short)(ngc_scroll_next[0] / 100u * 75u + '
                      'ngc_scroll_next[0] % 100u * 75u / 100u), (short)(ngc_scroll_next[1] / 100u * 150u + '
                      'ngc_scroll_next[1] % 100u * 150u / 100u));', source)
        # The 16-bit split is exact over every clamped scroll and rate.
        for n in range(0, 401):
            for p in range(0, 2041):
                self.assertEqual(p // 100 * n + p % 100 * n // 100, p * n // 100)
                self.assertLess(p % 100 * n, 65536)
        for p in range(0, 1881, 7):
            self.assertEqual(rate_offset('50%', p), rate_offset('1/2', p))
            self.assertEqual(rate_offset('100%', p), p)
            self.assertEqual(rate_offset('0%', p), 0)
            self.assertEqual(rate_offset('75%', p), p * 75 // 100)
        for rate in ('401%', '-5%', '07%', '5.5%', '%', '1000%', ' 5%', 50):
            with self.subTest(rate=rate):
                self.assertFalse(is_rate(rate))
                with self.assertRaisesRegex(CompileError, 'scroll_x: expected'):
                    second_stream_source(allocation, scroll_x=rate)

    def test_loop_repeats_the_image_tile_aligned_and_refuses_over_2040(self):
        small = _source(64, 32, True)
        looped = loop_source(small, 200, 150)
        self.assertEqual((looped.width, looped.height), (200, 152))
        for y in range(0, 152, 5):
            for x in range(0, 200, 3):
                self.assertEqual(looped.pixels[y*200+x], small.pixels[(y % 32)*64 + x % 64])
                self.assertEqual(looped.indices[y*200+x], small.indices[(y % 32)*64 + x % 64])
        self.assertEqual(len(looped.attributes), 25 * 19)
        self.assertIs(loop_source(small, 0, 0), small)
        with self.assertRaisesRegex(CompileError, 'over 2040 px'):
            loop_source(small, 2048, 0)
        # Repeated tiles share the bank: no more patterns than the original.
        main = _source(160, 152, False)
        one = compile_second_background(main, _source(160, 152, True))
        many = compile_second_background(main, loop_source(_source(160, 152, True), 960, 152))
        self.assertEqual(len(many.main.tiles), len(one.main.tiles))

    def test_view_and_bank_mismatches_are_not_silently_repaired(self):
        allocation = self.allocation((160, 152), (160, 144))
        with self.assertRaisesRegex(CompileError, 'cannot fill.*no implicit padding'):
            second_stream_source(allocation)
        allocation = self.allocation((160, 152), (160, 152))
        with self.assertRaisesRegex(CompileError, 'same tile bank/backdrop'):
            second_stream_source(replace(allocation, front=replace(allocation.front, backdrop=4095)))

    def test_feature_selects_one_extra_global_in_the_existing_stream_object(self):
        ordinary = public_symbols(['streamed_background'])
        second = public_symbols(['streamed_background', 'second_background'])
        self.assertEqual(second-ordinary, {'_ngc_front_scroll'})
        source = stream_source(self.allocation().main, front_plane=1)
        self.assertNotIn('ngc_front_scroll', source)


class SecondProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project, self.ids = project_case(self.root)
        self.path = self.project/'ngpcraft.json'
        self.sidecar = json.loads(self.path.read_text(encoding='utf-8'))

    def config(self):
        return self.sidecar['resources'][self.ids[TOWN]]['second_background']

    def refused(self, message):
        write_json(self.path, self.sidecar)
        with self.assertRaisesRegex(ProjectError, message):
            load_project(self.project)

    def test_extension_roundtrip_preserves_explicit_reference_and_rates(self):
        project = load_project(self.project)
        write_project(project, self.root/'roundtrip')
        self.assertEqual(load_project(self.root/'roundtrip').ngpc, project.ngpc)

    def test_unknown_nested_fields_and_missing_rates_are_located(self):
        self.config()['speed'] = 0
        self.refused('second_background.speed: unknown field')
        del self.config()['speed']
        del self.config()['scroll_y']
        self.refused('second_background.scroll_y: required field')

    def test_reference_must_be_background_and_owner_must_be_scene(self):
        self.config()['background_id'] = self.ids[CAVE]
        self.refused('background_id:.*not a background resource')
        self.sidecar['resources'] = {self.config()['background_id']: {'second_background': {**self.config(), 'background_id':'no-such-id'}}}
        self.refused('background_id: unknown background id')
        project = load_project_without_extension(self.project)
        bid = project.by_type['background'][0][1]['id']
        self.sidecar['resources'] = {bid: {'second_background': {'background_id':bid, 'scroll_x':'1', 'scroll_y':'fixed'}}}
        self.refused('extension belongs to a scene resource')

    def test_types_are_strict_including_fixed_and_boolean(self):
        for value in (0, True, None, [], '0', '1/3'):
            self.config()['scroll_x'] = value
            self.refused('second_background.scroll_x: expected')
        self.config()['scroll_x'] = 'fixed'
        write_json(self.path, self.sidecar)
        load_project(self.project)

    def test_percent_rates_and_loops_are_accepted_with_strict_types(self):
        self.config().update({'scroll_x': '75%', 'scroll_y': '0%', 'loop_x': True, 'loop_y': False})
        write_json(self.path, self.sidecar)
        load_project(self.project)
        self.config()['scroll_x'] = '401%'
        self.refused(r'second_background.scroll_x: expected .*0% .. 400%')
        self.config()['scroll_x'] = '75%'
        for value in ('yes', 1, None):
            self.config()['loop_x'] = value
            self.refused('second_background.loop_x: expected true or false')

    def test_project_loop_and_percent_reach_the_exported_scene(self):
        # Main 512 px wide: the camera reaches 352, at 150% SCR1 reaches 528,
        # so the 320 px image is repeated to 160 + 528 = 688 px.
        self.config().update({'scroll_x': '150%', 'scroll_y': '1', 'loop_x': True})
        write_json(self.path, self.sidecar)
        manifest = export_project(self.project, self.root/'export')
        town = next(r for r in manifest['scene_engine']['scenes'] if r['id'] == self.ids[TOWN])
        self.assertEqual(town['second_background']['second_size_px'], [688, 320])
        sources = '\n'.join(p.read_text(encoding='utf-8', errors='replace')
                            for p in (self.root/'export').rglob('*.c'))
        self.assertIn('ngc_scroll_next[0] / 100u * 150u + ngc_scroll_next[0] % 100u * 150u / 100u', sources)

    def test_logo_extension_is_refused_instead_of_scripts_only_fallback(self):
        scene_path = next((self.project/'project/scenes').rglob('sample_town/scene.gbsres'))
        scene = json.loads(scene_path.read_text(encoding='utf-8'))
        scene['type'] = 'LOGO'
        write_json(scene_path, scene)
        self.refused('second_background: LOGO scenes')

    def test_text_routing_front_palettes_and_normal_scene_tracking(self):
        manifest = export_project(self.project, self.root/'export')
        self.assertIn('second_background', manifest['features'])
        self.assertIn('text_shared', manifest['features'])
        reports = manifest['scene_engine']['scenes']
        town = next(r for r in reports if r['id'] == self.ids[TOWN])
        self.assertEqual(town['planes']['front_usage'], 'second_background')
        self.assertTrue(town['second_background']['runtime_supported'])
        self.assertEqual(town['ui_palette_base'], town['second_background']['palette_counts']['second'])
        c = (self.root/'export/src/gbvm.c').read_text(encoding='utf-8')
        self.assertIn('((ngc_front_scroll[1] >> 3) + 20u)', c)
        self.assertIn('0x8032 = (unsigned char)ngc_front_scroll[0]', c)
        streaming = (self.root/'export/src/scene_stream.c').read_text(encoding='utf-8')
        self.assertIn('ngc_front_scroll[1] = ngc_scroll[1];', streaming)

    def test_no_text_has_no_raster_or_ui_buffer(self):
        for name in (TOWN, CAVE):
            folder = next(p.parent for p in (self.project/'project/scenes').rglob('scene.gbsres') if p.parent.name == name)
            data = json.loads((folder/'scene.gbsres').read_text(encoding='utf-8'))
            set_script(folder, [e for e in data['script'] if e['command'] != 'EVENT_TEXT'])
        manifest = export_project(self.project, self.root/'export')
        self.assertIn('second_background', manifest['features'])
        self.assertNotIn('text', manifest['features'])
        c = (self.root/'export/src/gbvm.c').read_text(encoding='utf-8')
        self.assertNotIn('ngc_split_', c)
        self.assertNotIn('ngc_ui_map[1024]', c)

    def test_unreachable_second_decor_emits_neither_feature_nor_global(self):
        settings_path = self.project/'project/settings.gbsres'
        settings = json.loads(settings_path.read_text(encoding='utf-8'))
        settings['startSceneId'] = self.ids[CAVE]
        write_json(settings_path, settings)
        cave = next(p.parent for p in (self.project/'project/scenes').rglob('scene.gbsres') if p.parent.name == CAVE)
        set_script(cave, [])
        manifest = export_project(self.project, self.root/'export')
        self.assertNotIn('second_background', manifest['features'])
        self.assertNotIn('ngc_front_scroll', (self.root/'export/src/scene_stream.c').read_text(encoding='utf-8'))

    def test_case_c_is_refused_at_export_with_tile_location(self):
        project = load_project(self.project)
        scene = next(s for s in project.scenes if s['id'] == self.ids[TOWN])
        rel, bg = next((r,b) for r,b in project.by_type['background'] if b['id'] == scene['backgroundId'])
        bg['tileColors'] = '80!'
        write_json(self.project/PurePosixPath(rel), bg)
        with self.assertRaisesRegex(CompileError, r'second_background.main: tile\[0,0\]: case C:.*priority'):
            export_project(self.project, self.root/'export')
        # Restore released Town's palettes/PNG but remove only the priority flag.
        original = json.loads((GBS2/PurePosixPath(rel)).read_text(encoding='utf-8'))
        original['tileColors'] = ''.join(f'{a & 127:02x}!' for a in decompress_tile_colors(original['tileColors'], 'source'))
        write_json(self.project/PurePosixPath(rel), original)
        shutil.copy2(GBS2/PurePosixPath(rel).with_suffix(''), (self.project/PurePosixPath(rel)).with_suffix(''))
        with self.assertRaisesRegex(CompileError, r'second_background.main: tile\[\d+,\d+\]: case C:.*A4'):
            export_project(self.project, self.root/'export')


def load_project_without_extension(root):
    path = root/'ngpcraft.json'
    data = path.read_bytes()
    write_json(path, {'schema':'ngpcraft-project/1'})
    project = load_project(root)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)
    return project


@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class SecondStreamRomTests(unittest.TestCase):
    def test_independent_rings_jumps_clamps_and_fixed_or_slow_rates(self):
        for x, y in (('1/2', '1'), ('fixed', 'fixed'), ('1/256', '1/8'), ('1', '1')):
            with self.subTest(x=x, y=y), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest, build = build_case(root, Path(ENV[0]), scroll_x=x, scroll_y=y,
                                             front_size=(448,448) if x == '1' else (320,272))
                self.assertIn('_ngc_front_scroll', build['link_audit']['public_symbols'])
                result = observe(root, Path(ENV[1]), Path(ENV[2]))
                self.assertTrue(result['passed'], result['frames_with_mismatch'][:5])
                self.assertGreater(result['negative_missing_front_pixels'], 0)

    def test_percent_rates_including_faster_than_the_camera(self):
        for x, y, size in (('75%', '33%', (448, 448)), ('150%', '1', (320, 272))):
            with self.subTest(x=x, y=y), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                build_case(root, Path(ENV[0]), scroll_x=x, scroll_y=y, front_size=size)
                result = observe(root, Path(ENV[1]), Path(ENV[2]))
                self.assertTrue(result['passed'], result['frames_with_mismatch'][:5])

    def test_small_looping_image_wraps_on_both_axes(self):
        for x, y in (('1/2', '1'), ('75%', '150%')):
            with self.subTest(x=x, y=y), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                build_case(root, Path(ENV[0]), scroll_x=x, scroll_y=y, front_size=(64, 48),
                           loop_x=True, loop_y=True)
                result = observe(root, Path(ENV[1]), Path(ENV[2]))
                self.assertTrue(result['passed'], result['frames_with_mismatch'][:5])
                self.assertGreater(result['negative_missing_front_pixels'], 0)

    def test_incorrect_front_rate_negative_witness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, Path(ENV[0]), witness=True)
            result = observe(root, Path(ENV[1]), Path(ENV[2]))
            self.assertFalse(result['passed'])
            self.assertTrue(result['frames_with_mismatch'])
            self.assertTrue(result['register_errors'])

    def test_letterboxed_main_and_larger_front_share_the_main_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_case(root, Path(ENV[0]), main_size=(448, 144), front_size=(320, 320), scroll_y='1')
            result = observe(root, Path(ENV[1]), Path(ENV[2]))
            self.assertTrue(result['passed'], result['frames_with_mismatch'][:5])


@unittest.skipUnless(all(ENV), 'requires Toshiba, native emulator and BIOS')
class SecondTextRomTests(unittest.TestCase):
    def run_case(self, *, transitions=False, witness=False, letterboxed=False, warp=False, scene_witness=False, fades=True, rate=60, load_ui_witness=False):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        project, ids = project_case(root, transitions=transitions, wait=120)
        if warp:
            folder = next(p.parent for p in (project/'project/scenes').rglob('scene.gbsres') if p.parent.name == TOWN)
            data = json.loads((folder/'scene.gbsres').read_text(encoding='utf-8'))
            body = data['script'][0]['children']['true']
            # One page isolates camera/raster behavior from scheduler gaps
            # between multiple VM text instructions.
            body[0]['args']['text'] = [body[0]['args']['text'][0]]
            body.insert(0, ev('camera-warp', 'EVENT_ACTOR_SET_POSITION', actorId='player', x=num(48), y=num(48), units='tiles'))
            set_script(folder, data['script'])
        if letterboxed:
            from tools.demo_second_background import _png
            loaded = load_project(project)
            scene = next(s for s in loaded.scenes if s['id'] == ids[TOWN])
            rel, bg = next((r,b) for r,b in loaded.by_type['background'] if b['id'] == scene['backgroundId'])
            bg['height'] = 18
            write_json(project/PurePosixPath(rel), bg)
            _png((project/PurePosixPath(rel)).with_suffix(''), 512, 144, False)
            path = project/'project/settings.gbsres'
            settings = json.loads(path.read_text(encoding='utf-8'))
            settings['startY'] = 12
            write_json(path, settings)
        if not fades:
            def remove_fades(events):
                for event in events:
                    if event['command'] in ('EVENT_SWITCH_SCENE', 'EVENT_SCENE_POP_STATE'):
                        event['args']['fadeSpeed'] = '0'
                    if event['command'] == 'EVENT_TEXT':
                        # As in the camera-warp proof, one page isolates palette,
                        # raster and scene loading from VM quantum gaps between
                        # consecutive text instructions (the UI oracle has no VM).
                        event['args']['text'] = [event['args']['text'][0]]
                    for children in event.get('children', {}).values():
                        remove_fades(children)
            for path in (project/'project/scenes').rglob('scene.gbsres'):
                scene = json.loads(path.read_text(encoding='utf-8'))
                scene['autoFadeSpeed'] = None
                remove_fades(scene['script'])
                write_json(path, scene)
        if rate != 60:
            path = project/'ngpcraft.json'
            config = json.loads(path.read_text(encoding='utf-8'))
            config['ngpc'] = {'frame_rate': rate}
            write_json(path, config)
        manifest = export_project(project, root/'export')
        self.assertEqual('fades' in manifest['features'], fades)
        if load_ui_witness:
            path = root/'export/src/game.c'
            source = path.read_text(encoding='utf-8')
            old = "    ngc_ui_commit();  /* cut: close the previous scene's window before showing colours */"
            self.assertEqual(source.count(old), 1)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(source.replace(old, '    /* witness: old UI scroll retained */').encode('utf-8'))
            temporary.replace(path)
            manifest['sha256']['src/game.c'] = hashlib.sha256(path.read_bytes()).hexdigest()
            write_json(root/'export/export.json', manifest)
        if scene_witness:
            path = root/'export/src/game.c'
            c = path.read_text(encoding='utf-8')
            old = '    if (keep) ngc_vm_frame_end();  /* second decor: publish the new palette in VBlank */'
            self.assertEqual(c.count(old), 1)
            temporary = path.with_suffix('.tmp')
            # A faster engine (2026-10-05) ends this load inside VBlank: without
            # the sync, a load that ends late (here ~70 lines, burnt) publishes
            # the new palette mid-picture; the sync is what makes it wait.
            temporary.write_text(c.replace(old, '    { volatile unsigned short d; for (d = 0u; d < 3000u; ++d) { } }'),
                                 encoding='utf-8')
            temporary.replace(path)
            manifest['sha256']['src/game.c'] = hashlib.sha256(path.read_bytes()).hexdigest()
            write_json(root/'export/export.json', manifest)
        if witness:
            path = root/'export/src/gbvm.c'
            c = path.read_text(encoding='utf-8')
            old = '    row = (unsigned short)(((ngc_front_scroll[1] >> 3) + 20u) & 31u);'
            self.assertEqual(c.count(old), 1)
            temporary = path.with_suffix('.tmp')
            c = c.replace('extern unsigned short ngc_front_scroll[2];', 'extern unsigned short ngc_front_scroll[2];\nextern unsigned short ngc_scroll[2];')
            temporary.write_text(c.replace(old, '    row = (unsigned short)(((ngc_scroll[1] >> 3) + 20u) & 31u);'), encoding='utf-8')
            temporary.replace(path)
            manifest['sha256']['src/gbvm.c'] = hashlib.sha256(path.read_bytes()).hexdigest()
            write_json(root/'export/export.json', manifest)
        build_rom(root/'export', Path(ENV[0]))
        frames = 960 if transitions else 420
        write_json(root/'inputs.json', [[k,k+8,16] for k in range(150,frames+1,150)])
        result = subprocess.run([sys.executable, '-B', str(ROOT/'tools/probe_gbvm.py'), str(root/'export'),
            '--emulator', ENV[1], '--bios', ENV[2], '--out', str(root/'probe'), '--frames', str(frames),
            '--inputs', str(root/'inputs.json'), '--shots', ','.join(map(str,range(1,frames+1))),
            '--sprite-mask-trace', '--duty-cycle', '0.25'], cwd=ROOT, capture_output=True, text=True, timeout=150)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        town = check(root, scene_folder='Sample Town', offset=4 if letterboxed else 8,
                     bottom=148 if letterboxed else 152, committed_map=True)
        return root, town

    def test_dialogue_shares_front_rows_and_restores_decor_and_sprites(self):
        _, town = self.run_case()
        self.assertTrue(passed(town), town['background_errors'][:5])
        self.assertGreater(town['second_pixels_checked'], 0)
        self.assertGreater(town['hidden_sprite_samples'], 0)
        self.assertGreater(town['visible_sprite_samples'], 0)

    def test_ui_rows_using_main_y_are_detected_by_source_pixel_oracle(self):
        _, town = self.run_case(witness=True)
        self.assertFalse(passed(town))
        self.assertTrue(town['background_errors'])

    def test_shared_text_in_letterboxed_view_uses_the_front_scroll(self):
        _, town = self.run_case(letterboxed=True)
        self.assertTrue(passed(town), town['background_errors'][:5])
        self.assertGreater(town['second_pixels_checked'], 0)

    def test_second_background_to_free_front_and_back(self):
        root, town = self.run_case(transitions=True)
        cave = check(root)
        self.assertTrue(passed(town), town['background_errors'][:5])
        self.assertTrue(passed(cave), cave['wrong_frames'][:5])
        self.assertGreaterEqual(len(town['starts']), 2)
        self.assertGreaterEqual(len(cave['starts']), 1)

    def test_cut_between_second_background_and_free_front_at_sixty(self):
        self.cut_with_text(60)

    def test_cut_between_second_background_and_free_front_at_thirty(self):
        self.cut_with_text(30)

    def test_cut_retaining_the_previous_ui_scroll_is_detected(self):
        root, town = self.run_case(transitions=True, fades=False, load_ui_witness=True)
        cave = check(root)
        self.assertTrue(passed(town), town['background_errors'][:5])
        self.assertFalse(passed(cave))
        self.assertTrue(cave['background_errors'])
        self.assertFalse(cave['wrong_frames'])  # the new dialogue is correct

    def cut_with_text(self, rate):
        root, town = self.run_case(transitions=True, fades=False, rate=rate)
        cave = check(root)
        self.assertTrue(passed(town), town['background_errors'][:5])
        self.assertTrue(passed(cave), cave['wrong_frames'][:5])
        self.assertGreaterEqual(len(town['starts']), 2)
        self.assertGreaterEqual(len(cave['starts']), 1)
        self.assertGreater(town['second_pixels_checked'], 0)
        self.assertGreater(town['hidden_sprite_samples'], 0)
        self.assertGreater(town['visible_sprite_samples'], 0)

    def test_camera_warp_into_dialogue_keeps_source_pixels(self):
        _, town = self.run_case(warp=True)
        self.assertTrue(passed(town), town['background_errors'][:5])

    def test_unsynchronized_scene_palette_publication_is_detected(self):
        root, _ = self.run_case(transitions=True, scene_witness=True)
        cave = check(root)
        self.assertFalse(passed(cave))
        self.assertTrue(cave['background_errors'])
