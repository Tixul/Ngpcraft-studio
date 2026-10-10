"""Scene assets read from a loaded GB Studio project: background colours, sprite
sheets, sprite palettes. No image library (ngpcraft.png).

GB Studio 4.3.2 sources: shared/lib/resources/compression.ts
(decompress8bitNumberString), consts.ts (DMG_PALETTE), lib/compiler/
compileData.ts (getPalette, getBackgroundPalette, getSpritePalette),
shared/lib/tiles/tileData.ts (background index thresholds), sprites/
spriteData.ts (spriteDataIndexFn). Where GB Studio repairs silently (invalid
tileColors -> [], unknown palette id -> fallback), NGPCraft refuses with the place.
"""
from dataclasses import dataclass
from pathlib import PurePosixPath

from .compiler import CompileError
from .png import read_png
from .sprites import Indexed, sprite_index

DMG_PALETTE = ("E8F8E0", "B0F088", "509878", "202850")


def rgb444(color):
    """K2GE colour word from a GB Studio hex colour: R in bits 0-3 (>> 4 per channel)."""
    return sum((int(color[j:j + 2], 16) >> 4) << (j * 2) for j in (0, 2, 4))


def decompress_tile_colors(text, where, what="tileColors"):
    """decompress8bitNumberString (tileColors, scene collisions); where GB Studio
    returns [] or guesses on a malformed string, refused."""
    out, pos = [], 0
    while pos < len(text):
        value = int(text[pos:pos + 2], 16)
        pos += 2
        if pos >= len(text):
            raise CompileError(f"{where}: {what} truncated (GB Studio would use no {what})")
        if text[pos] == "!":
            count, pos = 1, pos + 1
        else:
            end = text.find("+", pos)
            if end <= pos:
                raise CompileError(f"{where}: {what} count invalid (GB Studio would use no {what})")
            count, pos = int(text[pos:end], 16), end + 1
        out.extend([value] * count)
    return out


def _by_id(project, kind):
    return {res["id"]: (rel, res) for rel, res in project.by_type[kind]}


def get_palette(project, pid, fallback, where):
    """compileData getPalette, unknown ids refused instead of replaced."""
    lookup = _by_id(project, "palette")
    for candidate in (pid, fallback):
        if candidate == "dmg":
            return DMG_PALETTE
        if candidate:
            if candidate not in lookup:
                raise CompileError(f"{where}: unknown palette {candidate} (GB Studio would fall back silently)")
            return tuple(lookup[candidate][1]["colors"])
    return DMG_PALETTE


def _color_project(project, where):
    if project.settings["colorMode"] not in ("color", "mixed", "mono"):
        raise CompileError(f"{where}: project colour mode {project.settings['colorMode']!r}: "
                           "expected mono, mixed or color")
    if project.settings["colorMode"] == "mono" and project.settings["sgbEnabled"]:
        raise CompileError(f"{where}: SGB colourisation of a mono project is not delivered")


def mono_palette(project, scene, key, default_key):
    """GB Studio getDMGPalette: shade registers rendered in its DMG palette.

    The NGPC has no DMG LCD: use the reference's four declared RGB colours,
    quantized by rgb444, preserving the authored BGP/OBP shade mapping.
    """
    shades = scene[key] if scene.get(key) else project.settings[default_key]
    return tuple(rgb444(DMG_PALETTE[index]) for index in shades)


@dataclass(frozen=True)
class BackgroundSource:
    """GB indices after flips, before RGB packing; attributes stay tile-local."""
    width: int
    height: int
    pixels: tuple
    indices: bytes
    attributes: bytes
    resource: str
    # ngpc_color "native" (native_color.py): the backdrop the plan chose, given
    # to compile_background; None = GB Studio colours, compile_background picks.
    backdrop: int = None

    def priority_report(self):
        columns = self.width // 8
        cells = [n for n, attr in enumerate(self.attributes) if attr & 0x80]
        opaque = sum(bool(index) and bool(self.attributes[
            (y // 8) * columns + x // 8] & 0x80)
            for y in range(self.height) for x in range(self.width)
            for index in (self.indices[y * self.width + x],))
        return {"resource": self.resource, "priority_cells": len(cells),
                "priority_opaque_pixels": opaque,
                "first_priority_cell": [cells[0] % columns, cells[0] // columns] if cells else None,
                "status": "not_emitted" if cells else "not_requested"}


def background_pixels(project, scene):
    """Compatibility view of the one source decoder, used by existing probes."""
    source = background_source(project, scene)
    return source.width, source.height, source.pixels


def background_slots(project, scene, where):
    """The scene's eight GB background palettes, RGB444 (index 0..3 per slot)."""
    if project.settings["colorMode"] == "mono":
        return [mono_palette(project, scene, "monoBGP", "defaultMonoBGP")] * 8
    ids, defaults = scene.get("paletteIds") or [], project.settings["defaultBackgroundPaletteIds"]
    return [tuple(rgb444(c) for c in get_palette(project, ids[i] if i < len(ids) else "",
                                                  defaults[i] if i < len(defaults) else "", where))
            for i in range(8)]


def background_source(project, scene):
    """Preserve GB transparency and priority independently of RGB444 colours."""
    where = f"scene {scene['name']}"
    _color_project(project, where)
    backgrounds = _by_id(project, "background")
    if scene["backgroundId"] not in backgrounds:
        raise CompileError(f"{where}: unknown background {scene['backgroundId']}")
    rel, bg = backgrounds[scene["backgroundId"]]
    from .native_color import is_native, native_source
    # GB Studio's "Automatic" palettes (autoColor) take the PNG's own colours:
    # on the NGPC that is its native colour mode (docs/M7_NATIVE_COLOR.md).
    # A mono project ignores autoColor, as GB Studio does.
    auto = bool(bg.get("autoColor")) and project.settings.get("colorMode") != "mono"
    if is_native(project, bg["id"]) or auto:
        # paletteIds and tileColors are GB Studio's colours: ignored.
        width, height, rgba = read_png(project.root / PurePosixPath(rel).with_suffix(""))
        if width % 8 or height % 8:
            raise CompileError(f"{rel}: image {width}x{height} is not tile aligned")
        attrs = decompress_tile_colors(bg.get("tileColors") or "", f"{rel}")
        attrs = bytes(attrs[n] if n < len(attrs) else 0 for n in range((width // 8) * (height // 8)))
        from .native_color import backdrop_choice
        pixels, indices, attrs, backdrop = native_source(project, rel, width, height, rgba, attrs, where,
                                                         backdrop_choice(project, bg["id"]))
        return BackgroundSource(width, height, pixels, indices, attrs, rel, backdrop)
    if bg.get("monoOverrideId"):
        raise CompileError(f"{where}: monoOverride backgrounds are not supported yet")
    slots = background_slots(project, scene, where)
    attrs = decompress_tile_colors(bg.get("tileColors") or "", f"{rel}")
    width, height, rgba = read_png(project.root / PurePosixPath(rel).with_suffix(""))
    if width % 8 or height % 8:
        raise CompileError(f"{rel}: image {width}x{height} is not tile aligned")
    attrs = bytes(attrs[n] if n < len(attrs) else 0
                  for n in range((width // 8) * (height // 8)))
    if project.settings["colorMode"] == "mono":
        # DMG has no CGB background attributes (palettes/flips/priority).
        attrs = bytes(len(attrs))
    out, indices = [], bytearray()
    for y in range(height):
        for x in range(width):
            n = (y // 8) * (width // 8) + x // 8
            attr = attrs[n] if n < len(attrs) else 0
            sx = (x & ~7) + (7 - (x & 7) if attr & 32 else x & 7)
            sy = (y & ~7) + (7 - (y & 7) if attr & 64 else y & 7)
            green = rgba[sy * width + sx][1]
            index = 3 if green < 65 else 2 if green < 130 else 1 if green < 205 else 0
            out.append(slots[attr & 7][index])
            indices.append(index)
    return BackgroundSource(width, height, tuple(out), bytes(indices), attrs, rel)


def sprite_sheets(project, selected=None):
    """{sheet id: (sheet resource, indexed image)} (readFileToIndexedImage + spriteDataIndexFn)."""
    out = {}
    for rel, sheet in project.by_type["sprite"]:
        if selected is not None and sheet["id"] not in selected:
            continue
        out[sheet["id"]] = (sheet, sheet_image(project, rel, sheet))
    return out


def sheet_image(project, rel, sheet):
    """The indexed image of a sheet: GB Studio indices, or native colours
    (sprites.native_index) for an ngpc_color "native" sheet."""
    from .native_color import is_native
    from .sprites import native_index
    width, height, rgba = read_png(project.root / PurePosixPath(rel).with_suffix(""))
    index = native_index if is_native(project, sheet["id"]) else sprite_index
    return Indexed(width, height, [index(*p) for p in rgba])


def sprite_palettes(project, scene):
    """compileData getSpritePalette per slot: colours [c0, c0, c1, c3]; NGPC slot = (c0, c1, c3)."""
    where = f"scene {scene['name']}"
    _color_project(project, where)
    if project.settings["colorMode"] == "mono":
        zero = mono_palette(project, scene, "monoOBP0", "defaultMonoOBP0")
        one = mono_palette(project, scene, "monoOBP1", "defaultMonoOBP1")
        return (zero, one) + (zero,) * 6
    ids, defaults = scene.get("spritePaletteIds") or [], project.settings["defaultSpritePaletteIds"]
    out = []
    for i in range(8):
        colors = get_palette(project, ids[i] if i < len(ids) else "",
                             defaults[i] if i < len(defaults) else "", where)
        out.append((rgb444(colors[0]), rgb444(colors[1]), rgb444(colors[3])))
    return tuple(out)
