"""GB Studio fonts, read as GB Studio reads them (lib/fonts/fontData.ts).

read_font_data ports readFileToFontData line for line, with the helpers of
shared/lib/tiles/indexedImage.ts it uses (sliceIndexedImage,
trimIndexedImageHorizontal, indexedImageTo2bppTileData) and its own
fontDataIndexFn. The result is GB Studio's CompiledFontData: the glyph data
stays in Game Boy 2bpp form here; the NGPC runtime converts it (ui_text.py).
Checked against the real function (tests/test_fonts.py, tools/gbstudio_ts.py).
"""
from dataclasses import dataclass
import json
from pathlib import Path

from .compiler import CompileError
from .png import read_png

WHITE, LIGHT, MID, DARK, TRANSPARENT = 0, 1, 2, 3, 255
TILE_SIZE = 8
FIRST_CHAR = 32


@dataclass(frozen=True)
class FontData:
    name: str
    table: tuple
    widths: tuple
    data: bytes
    is_variable_width: bool
    is_1bit: bool
    mapping: dict

    def as_gbstudio(self):
        """The CompiledFontData object, with GB Studio's field names."""
        return {"name": self.name, "table": list(self.table), "widths": list(self.widths),
                "data": list(self.data), "isVariableWidth": self.is_variable_width, "is1Bit": self.is_1bit,
                "mapping": self.mapping}


def font_data_index(r, g, b, _a):
    """fontData.ts fontDataIndexFn."""
    if g > 249 or (r > 249 and b > 249):
        return TRANSPARENT
    if g < 65:
        return DARK
    if g < 130:
        return MID
    if g < 205:
        return LIGHT
    return WHITE


SLICE_FILL = 0     # indexedImage.ts has its own Color enum: there Transparent = 0


def _slice(data, width, height, sx, sy, w, h):
    """sliceIndexedImage: outside the image = indexedImage.ts Color.Transparent
    (0, white), not fontData.ts Color.Transparent (255)."""
    out = []
    for y in range(sy, sy + h):
        for x in range(sx, sx + w):
            out.append(data[y * width + x] if 0 <= x < width and 0 <= y < height else SLICE_FILL)
    return out


def _trim_horizontal(tile, trim_value):
    """trimIndexedImageHorizontal on an 8x8 tile: (data, width)."""
    min_x, max_x = TILE_SIZE, 0
    for y in range(TILE_SIZE):
        for x in range(TILE_SIZE):
            if tile[y * TILE_SIZE + x] != trim_value:
                min_x = min(min_x, x)
                max_x = max(max_x, x)
    w = max(0, max_x - min_x + 1)
    return _slice(tile, TILE_SIZE, TILE_SIZE, min_x, 0, w, TILE_SIZE), w


def to_2bpp(tile):
    """indexedImageTo2bppTileData: bin2(value) keeps the two low binary digits
    of value.toString(2).padStart(2, '0'), so Transparent (255 = 11111111)
    gives bits '1','1' (colour 3)."""
    out = bytearray()
    for y in range(TILE_SIZE):
        lo = hi = 0
        for x in range(TILE_SIZE):
            binary = format(tile[y * TILE_SIZE + x], "b").rjust(2, "0")
            lo = (lo << 1) | int(binary[1])
            hi = (hi << 1) | int(binary[0])
        out += bytes([lo, hi])
    return bytes(out)


def read_metadata(path):
    """The .json next to the image: `mapping` and `table` objects. GB Studio
    ignores a missing or unreadable file (empty catch); kept."""
    try:
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(meta, dict):
        return {}, {}

    def obj(value):                # `value && typeof value === "object"`; an array's keys are "0", "1"...
        if isinstance(value, list):
            return {str(i): v for i, v in enumerate(value)}
        return value if isinstance(value, dict) else {}
    return obj(meta.get("mapping")), obj(meta.get("table"))


def read_font_data(path):
    """fontData.ts readFileToFontData."""
    path = Path(path)
    width, height, rgba = read_png(path)
    image = [font_data_index(*p) for p in rgba]
    tile_w, tile_h = width // TILE_SIZE, height // TILE_SIZE
    mapping, table_mapping = read_metadata(path)
    is_1bit = not any(v in (LIGHT, MID) for v in image)
    is_variable = False
    chars = []
    for ty in range(tile_h):
        for tx in range(tile_w):
            tile = _slice(image, width, height, tx * TILE_SIZE, ty * TILE_SIZE, TILE_SIZE, TILE_SIZE)
            trimmed, w = _trim_horizontal(tile, TRANSPARENT)
            if w < TILE_SIZE:
                is_variable = True
            chars.append((w, tuple(_slice(trimmed, w, TILE_SIZE, 0, 0, TILE_SIZE, TILE_SIZE))))
    unique, keys = {}, []
    for char in chars:
        unique.setdefault(char, len(unique))          # hashChar: width + data
        keys.append(char)
    table = ([0] * FIRST_CHAR if tile_h < 16 else []) + [unique[k] for k in keys]
    if table_mapping:
        codes = [ord(k[0]) for k in table_mapping if k and ord(k[0]) < 256]
        top = max(codes) + 1 if codes else None      # Math.max() of none: -Infinity, no growth
        if top is not None and len(table) < top:
            table += [0] * (top - len(table))
        for k, v in table_mapping.items():
            if k and ord(k[0]) < 256:
                table[ord(k[0])] = v
    ordered = sorted(unique, key=unique.get)
    return FontData(path.name, tuple(table), tuple(w for w, _ in ordered),
                    b"".join(to_2bpp(d) for _, d in ordered), is_variable, is_1bit, mapping)
