"""Shared SCR1 UI palette allocation, budgets and scene switching tables."""
import unittest
from ngpcraft.compiler import CompileError
from ngpcraft.gbvm.emit_ui import ui_support
from ngpcraft.ui_text import font_table, scene_ui
from tools.lab_text_split import prepare


class SharedPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project, cls.scene, cls.bg, *rest = prepare()
        cls.base = len(cls.bg.palettes[0])
        cls.plain = scene_ui(cls.project, cls.scene, 0, [])
        cls.shared = scene_ui(cls.project, cls.scene, 0, [], palette_base=cls.base)

    def test_palette_shift_preserves_pixels_and_color_indices(self):
        a, b = self.plain, self.shared
        self.assertGreater(self.base, 0)
        self.assertEqual(a.tiles, b.tiles)
        self.assertEqual(a.palettes, b.palettes)
        self.assertEqual((a.ink, a.paper), (b.ink, b.paper))
        self.assertEqual(b.attrs, tuple(v + (self.base << 9) for v in a.attrs))
        self.assertEqual(b.text_attr, a.text_attr + (self.base << 9))

    def test_combined_palette_budget_refuses_overflow_with_scene_location(self):
        # Deterministic two UI palettes; reserve fifteen slots for decor.
        from unittest.mock import patch
        with patch('ngpcraft.ui_text.pack_palettes', return_value=[(0, 1, 2), (3, 4, 5)]):
            with self.assertRaisesRegex(CompileError, 'scene town/Sample Town: SCR1 needs 15 decor palettes \\+ 2 UI palettes'):
                scene_ui(self.project, self.scene, 0, [], palette_base=15)

    def test_invalid_palette_reservations_are_explicitly_refused(self):
        for base in (-1, 16, True, 1.5):
            with self.subTest(base=base), self.assertRaises(CompileError):
                scene_ui(self.project, self.scene, 0, [], palette_base=base)

    def test_plain_export_has_no_shared_palette_table(self):
        support, api = ui_support(font_table(self.project), [self.plain], '(UWORD*)0x8280')
        self.assertNotIn('ngc_ui_pal_base', support + api)
        self.assertIn('dst = (UWORD*)0x8280;', api)

    def test_scene_switch_loads_each_scenes_reserved_palette_address(self):
        support, api = ui_support(font_table(self.project), [self.plain, self.shared], 'ngc_palette_shadow + 64')
        self.assertIn('ngc_ui_pal_base', support)
        self.assertIn('dst = (ngc_palette_shadow + 64) + ((UWORD)ngc_ui_pal_base[scene] << 2);', api)
        self.assertIn(f'    0, {self.base},\n', support)
