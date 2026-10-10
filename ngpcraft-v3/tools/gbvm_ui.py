"""Oracle of GBVM's dialogue box (ui.c, vm_ui.c, ui_a.s), Game Boy semantics.

Independent of the NGPC port (ngpcraft/gbvm/emit_ui.py prints on one bit
plane): this model keeps Game Boy 2bpp tiles (two planes, ui_a.s masks and
shifts, the inverse map of 1-bit fonts), the window map in GB tile numbers
and CGB banks, and steps the window and the printer frame by frame as
ui_update and ui_run_modal do. It then draws what the window shows below its
line, with the scene's UI colours (palette 7, M2_TEXT.md D4 for 4-colour
tiles): every pixel there is the box's, sprites included (D3).

Frame model (docs/M2_TEXT.md section 5): a probe frame k is the picture
finished at frame entry k. Its window position is the one committed at entry
k - 1 (after logic L(k-1)); its tiles and map are VRAM written during L(k),
before the beam reaches the box (the logic ends in the first lines).
"""
from dataclasses import dataclass, field

from ngpcraft.fonts import read_font_data
from ngpcraft.ui_text import _merge_four, _ui_image

TEXT_BUFFER_START, TEXT_BUFFER_START_BANK1 = 0xCC, 0xC0


class AvatarGlyph(bytes):
    """A GB 2bpp tile loaded from an avatar font (shown with M2_TEXT.md D4)."""
TIME_MASKS = (0x00, 0x00, 0x01, 0x03, 0x07, 0x0F, 0x1F, 0x3F)
J_A, J_B = 0x10, 0x20


@dataclass
class Ui:
    font: object                       # ngpcraft.fonts.FontData (GB 2bpp glyphs)
    avatar_fonts: tuple = ()           # compileAvatarFont: (recode length, [16-byte GB tiles]) per chunk
    font_idx: int = 0                  # font of ui_print_render: ui_fonts index (text fonts, then avatar fonts)
    text_fonts: tuple = ()             # ui_fonts text fonts in order (ui_text.used_fonts); () = (font,)
    win_x: int = 0
    win_y: int = 144
    dest_x: int = 0
    dest_y: int = 144
    win_speed: int = 1
    text_drawn: bool = True
    text_draw_speed: int = 1
    current_text_speed: int = 0
    text_ff: bool = False
    text_ff_joypad: bool = True
    text_bkg_fill: int = 0x00
    text_options: int = 0
    in_speed: int = 0
    out_speed: int = 0
    tile: int = TEXT_BUFFER_START
    bank: int = 0
    prev_tile: int = TEXT_BUFFER_START
    prev_bank: int = 0
    offset: int = 0
    vwf: list = field(default_factory=lambda: [0] * 32)
    tiles: dict = field(default_factory=dict)          # (tile, bank) -> 16 bytes GB 2bpp
    cells: dict = field(default_factory=dict)          # (x, y) of the window map -> (tile, bank)
    text: bytes = b""
    ptr: int = -1
    dest: int = 33                                     # window map cell index (y * 32 + x)
    base: int = 33
    scroll: tuple = (0, 8, 20, 8)                      # x, y, w, h
    saved: tuple = ()

    # -- vm_ui.c -------------------------------------------------------------
    def overlay_clear(self, x, y, w, h, color, options):
        self.text_bkg_fill = 0x00 if color else 0xFF
        if options & 1:                                  # UI_DRAW_FRAME
            self.draw_frame(x, y, w, h)
            if options & 2:
                self.scroll = (x + 1, y + 1, w - 2, h - 2)
        else:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.cells[(xx, yy)] = (0xC9 if color else 0xCA, 0)
            if options & 2:
                self.scroll = (x, y, w, h)

    def overlay_show(self, x, y, color, options):
        """vm_ui.c vm_overlay_show: clear from 0,0 to the screen's corner, then ui_set_pos."""
        if x < 20 and y < 18:
            self.overlay_clear(0, 0, 20 - x, 18 - y, color, options)
        self.win_x = self.dest_x = x * 8
        self.win_y = self.dest_y = y * 8

    def draw_frame(self, x, y, w, h):
        if h == 0:
            return
        rows = [0xC0] + [0xC3] * max(0, h - 2) + ([0xC6] if h > 1 else [])
        for j, t in enumerate(rows):
            for i in range(w):
                tile = t if i == 0 else t + 2 if i == w - 1 else t + 1
                self.cells[(x + i, y + j)] = (tile, 0)

    def move_to(self, x, y, speed):
        speed = self.in_speed if speed == -1 else self.out_speed if speed == -2 else speed
        self.dest_x, self.dest_y = x * 8, y * 8
        if speed == -3:
            self.win_x, self.win_y = self.dest_x, self.dest_y
        else:
            self.win_speed = speed

    def set_scroll(self, x, y, w, h):
        self.scroll = (x, y, w, h)

    def display_text(self, text, options=0, start_tile=0xFF):
        self.text = text + b"\0"
        self.text_options = options
        self.text_drawn = False
        self.text_ff = False
        if start_tile < 116:
            if start_tile >= 52:
                self.set_start_tile(TEXT_BUFFER_START_BANK1 + start_tile - 52, 1)
            else:
                self.set_start_tile(TEXT_BUFFER_START + start_tile, 0)

    def set_start_tile(self, tile, bank):
        self.prev_tile = self.tile = tile
        self.prev_bank = self.bank = bank
        self.offset = 0
        self.vwf = [self.text_bkg_fill] * 32

    # -- ui.c ------------------------------------------------------------------
    def next_tile(self):
        self.prev_bank, self.prev_tile = self.bank, self.tile
        self.tile = (self.tile + 1) & 0xFF
        if self.tile == 0:
            self.bank = (self.bank + 1) & 1
            self.tile = TEXT_BUFFER_START_BANK1 if self.bank else TEXT_BUFFER_START

    def print_reset(self):
        if self.offset:
            self.next_tile()
        self.offset = 0
        self.vwf = [self.text_bkg_fill] * 32

    def load_wram_tile(self):
        self.tiles[(self.tile, self.bank)] = bytes(self.vwf[:16])

    def shift_char(self, dest, glyph, rotate, mask, inverse):
        """ui_a.s ui_print_shift_char on both planes."""
        for row in range(8):
            c = glyph[2 * row] ^ inverse
            b = glyph[2 * row + 1] ^ inverse
            if rotate & 0x80:
                n = rotate & 0x7F
                c, b = (c << n) & 0xFF, (b << n) & 0xFF
            else:
                c, b = c >> rotate, b >> rotate
            for k, v in ((0, c), (1, b)):
                i = dest + 2 * row + k
                self.vwf[i] = (self.vwf[i] & mask) | (~mask & 0xFF & v)

    def print_render(self, ch):
        fonts = self.text_fonts or (self.font,)
        if self.font_idx >= len(fonts):
            # Avatar font: FONT_RECODE, mask 0x3F, table 0..n-1, no widths.
            length, tiles = self.avatar_fonts[self.font_idx - len(fonts)]
            letter = ch & 0x3F
            if letter >= length:
                raise ValueError(f"avatar letter {letter} outside the recode table")
            if self.offset:
                self.next_tile()
            self.tiles[(self.tile, self.bank)] = AvatarGlyph(tiles[letter])
            self.next_tile()
            self.offset = 0
            return True
        f = fonts[self.font_idx]
        mask_bits =0x7F if len(f.table) <= 128 else 0xFF
        letter = f.table[ch & mask_bits]
        glyph = f.data[letter * 16:(letter + 1) * 16]
        if f.is_variable_width:
            inverse = self.text_bkg_fill if f.is_1bit else 0
            width = f.widths[letter]
            # ui_a.s ui_print_make_mask_lr
            if width == 0:
                masks = 0xFFFF
            else:
                a, d = 0xFF >> width, 0xFF
                for _ in range(self.offset):
                    a, d = (a >> 1) | 0x80, (d >> 1) | ((a & 1) << 7)
                masks = a | (d << 8)
            self.shift_char(0, glyph, self.offset, masks & 0xFF, inverse)
            if self.offset + width > 8:
                self.shift_char(16, glyph, (8 - self.offset) | 0x80, masks >> 8, inverse)
            self.offset += width
            self.load_wram_tile()
            if self.offset > 7:
                self.vwf = self.vwf[16:] + [self.text_bkg_fill] * 16
                self.offset -= 8
                self.next_tile()
                if self.offset:
                    self.load_wram_tile()
                return True
            return False
        if self.offset:
            self.next_tile()
        self.tiles[(self.tile, self.bank)] = bytes(glyph)
        self.next_tile()
        self.offset = 0
        return True

    def draw_char(self, joy_pressed):
        """ui_draw_text_buffer_char (left to right, codes of the delivered texts)."""
        if self.ptr < 0:
            self.current_text_speed = TIME_MASKS[self.text_draw_speed]
            self.saved = (self.text_bkg_fill, self.text_ff_joypad, self.text_draw_speed)
            self.ptr = 0
            if not self.text_options & 1:
                self.base = self.dest = 33
                self.print_reset()
        while True:
            ch = self.text[self.ptr]
            if ch == 0:
                self.ptr = -1
                self.text_drawn = True
                self.font_idx = 0
                self.text_bkg_fill, self.text_ff_joypad, self.text_draw_speed = self.saved
                return False
            if ch == 0x01:
                self.ptr += 1
                self.text_draw_speed = (self.text[self.ptr] - 1) & 7
                self.current_text_speed = TIME_MASKS[self.text_draw_speed]
            elif ch == 0x0A:
                self.base += 32
                self.dest = self.base
                if self.offset:
                    self.print_reset()
            elif ch == 0x0D:
                x, y, w, h = self.scroll
                start = y * 32 + x
                if self.dest + 32 > ((start + (h << 5)) & 0xFFE0) - 1:
                    for yy in range(y, y + h - 1):
                        for xx in range(x, x + w):
                            self.cells[(xx, yy)] = self.cells.get((xx, yy + 1))
                    for xx in range(x, x + w):
                        self.cells[(xx, y + h - 1)] = (0xC9 if self.text_bkg_fill == 0 else 0xCA, 0)
                    self.dest = self.base
                else:
                    self.base += 32
                    self.dest = self.base
                if self.offset:
                    self.print_reset()
            elif ch == 0x03:
                # gotoxy: text_render_base_addr + (x - 1) + (y - 1) * 32 (window map: base 0)
                x, y = self.text[self.ptr + 1], self.text[self.ptr + 2]
                self.ptr += 2
                self.base = self.dest = (x - 1) + (y - 1) * 32
                if self.offset:
                    self.print_reset()
            elif ch == 0x02:
                # Font change (the saved font is restored at the end).
                was_vwf = self._font_vwf()
                self.ptr += 1
                self.font_idx = self.text[self.ptr] - 1
                if self.offset and was_vwf and not self._font_vwf():
                    self.dest += 1
            elif ch == 0x04:
                # Relative goto: signed bytes, a positive one minus 1.
                dx, dy = (b - 256 if b > 127 else b for b in self.text[self.ptr + 1:self.ptr + 3])
                self.ptr += 2
                dx, dy = dx - (dx > 0), dy - (dy > 0)
                self.dest += dx + dy * 32
                self.base = self.dest
                if self.offset:
                    self.print_reset()
            elif ch in (0x06, 0x07, 0x08, 0x0B):
                raise NotImplementedError(f"text code {ch:#04x} not modelled")
            elif ch == 0x09:
                pass
            else:
                if ch == 0x05:
                    self.ptr += 1
                    ch = self.text[self.ptr]
                if self.print_render(ch):
                    self.cells[divmod(self.dest, 32)[::-1]] = (self.prev_tile, self.prev_bank)
                    self.dest += 1
                if self.offset:
                    self.cells[divmod(self.dest, 32)[::-1]] = (self.tile, self.bank)
                self.ptr += 1
                return True
            self.ptr += 1

    def _font_vwf(self):
        fonts = self.text_fonts or (self.font,)
        return self.font_idx < len(fonts) and fonts[self.font_idx].is_variable_width

    def update(self, game_time, joy_pressed):
        """ui.c ui_update."""
        flag = False
        if self.win_y != self.dest_y:
            if (game_time & TIME_MASKS[self.win_speed]) == 0:
                step = 2 if self.win_speed == 0 else 1
                self.win_y += step if self.win_y < self.dest_y else -step
            flag = True
        if self.win_x != self.dest_x:
            if (game_time & TIME_MASKS[self.win_speed]) == 0:
                step = 2 if self.win_speed == 0 else 1
                self.win_x += step if self.win_x < self.dest_x else -step
            flag = True
        if flag or self.text_drawn:
            return
        if self.text_ff_joypad and joy_pressed & (J_A | J_B):
            self.text_ff = True
        elif game_time & self.current_text_speed:
            return
        while True:
            self.draw_char(joy_pressed)
            if not ((self.text_ff or self.text_draw_speed == 0) and not self.text_drawn):
                break

    def wait_ok(self, flags, joy_pressed):
        """ui_run_modal / vm_overlay_wait conditions."""
        if flags & 1 and (self.win_x != self.dest_x or self.win_y != self.dest_y):
            return False
        if flags & 2 and not self.text_drawn:
            return False
        if flags & 4 and not joy_pressed & J_A:
            return False
        if flags & 8 and not joy_pressed & J_B:
            return False
        if flags & 16 and not joy_pressed:
            return False
        return True


class Palette:
    """GB colour index of a window tile pixel -> RGB444 (M2_TEXT.md D4)."""

    def __init__(self, project, colors):
        self.colors = colors
        warnings = []
        frame = _ui_image(project, "assets/ui/frame.png", warnings)
        cursor = _ui_image(project, "assets/ui/cursor.png", warnings)
        fixed = frame[:9] + [(0,) * 64, (3,) * 64, cursor[0]]
        self.fixed = [_merge_four(t, colors, "oracle", []) for t in fixed]

    def pixel(self, ui, tile, bank, x, y):
        if bank == 0 and 0xC0 <= tile < TEXT_BUFFER_START:
            return self.fixed[tile - 0xC0][y * 8 + x]
        data = ui.tiles.get((tile, bank))
        if data is None:
            return None
        if isinstance(data, AvatarGlyph):
            indexes = [((data[2 * r] >> (7 - c)) & 1) | (((data[2 * r + 1] >> (7 - c)) & 1) << 1)
                       for r in range(8) for c in range(8)]
            return _merge_four(indexes, self.colors, "oracle avatar", [])[y * 8 + x]
        lo, hi = data[2 * y], data[2 * y + 1]
        index = ((lo >> (7 - x)) & 1) | (((hi >> (7 - x)) & 1) << 1)
        return self.colors[index]


def box_pixels(ui, palette, win_y, offset, bottom, win_x=0):
    """{(x, y): RGB444} of the screen the window covers (window line
    = offset + win_y, down to `bottom`; columns from win_x pixels, the
    lateral menu's window), from the map and tiles of `ui`."""
    out = {}
    top = offset + win_y
    for sy in range(max(top, 0), bottom):
        for sx in range(max(win_x, 0), 160):
            cell = ui.cells.get(((sx - win_x) // 8, (sy - top) // 8))
            if cell is None:
                out[(sx, sy)] = None
                continue
            out[(sx, sy)] = palette.pixel(ui, cell[0], cell[1], sx % 8, (sy - top) % 8)
    return out


def rgb(c):
    """K2GE RGB444 (R in bits 0..3) -> the emulator's framebuffer RGB."""
    return ((c & 15) * 17, ((c >> 4) & 15) * 17, ((c >> 8) & 15) * 17)


def load_font(path):
    return read_font_data(path)
