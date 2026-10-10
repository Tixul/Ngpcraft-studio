"""Dialogue avatars (EVENT_TEXT avatarId), data side (docs/M4_AVATARS.md).

GB Studio 4.3.2:
- compileData.ts precompileAvatars: the avatars named by an event's
  `avatarId` (string), in walkScenesScripts order, each once; an id that
  names no avatar is skipped (the builder then prints no avatar);
- compileAvatars.ts: readFileToTilesData (8x8 tiles row by row, green
  channel) and its warning when the image is not 16 px tall and a multiple
  of 16 px wide;
- compileData.ts / generateGBVMData.ts compileAvatarFont: chunks of 16
  avatars; one font per chunk, FONT_RECODE, mask 0x3F, recode table 0..4n-1,
  no widths (fixed 8 px), bitmaps = the avatars' tiles concatenated. A wider
  avatar shifts its successors (GB Studio indexes 4 tiles per avatar):
  reproduced as is;
- scriptBuilderBase.ts _getAvatarCode: speed 0, font fonts.length + i / 16,
  characters 64 + (i * 4) % 64 .. + 3 on two rows, speed 2, goto (+0, -1),
  font 0.

NGPC: a glyph a scene's texts can print is read from its assembled texts
(the font codes they contain); only those are encoded, as K2GE tiles of the
scene's UI palette 7 (ui_text.scene_ui), D4 merge for 4 colours.
"""
from pathlib import PurePosixPath

from .compiler import CompileError
from .png import read_png
from .ui_text import tile_index

AVATAR_FONT_SIZE = 16
# ui.c ui_draw_text_buffer_char: argument bytes after each byte value (a
# printed character has none; 0x09, 0x0A and 0x0D neither).
TEXT_ARGS = [0] * 256
for _code, _count in ((0x01, 1), (0x02, 1), (0x03, 2), (0x04, 2), (0x05, 1), (0x06, 1), (0x07, 1), (0x08, 1),
                      (0x0B, 1)):
    TEXT_ARGS[_code] = _count
CONTROL = frozenset((0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0D))


def used_avatars(project):
    """precompileAvatars: [(path, resource)] in GB Studio's order."""
    from .projectiles import walk_scene_events
    lookup = {res["id"]: (rel, res) for rel, res in project.by_type["avatar"]}
    used, seen = [], set()

    def visit(event, here):
        args = event.get("args")
        if not args:
            return
        avatar_id = args.get("avatarId")
        if isinstance(avatar_id, str) and avatar_id and avatar_id not in seen and avatar_id in lookup:
            used.append(lookup[avatar_id])
            seen.add(avatar_id)
    for scene in project.scenes:
        walk_scene_events(project, scene, visit, "avatar scan")
    return used


def avatar_options(project):
    """The builder's `avatars` option (scriptBuilder avatars.findIndex)."""
    return [{"id": res["id"]} for _, res in used_avatars(project)]


def read_avatar_tiles(project, rel, warnings):
    """readFileToTilesData: GB colour indexes of each 8x8 tile, row by row."""
    path = project.root / PurePosixPath(rel).with_suffix("")
    if not path.is_file():
        raise CompileError(f"avatar {rel}: image {path.name} not found")
    width, height, rgba = read_png(path)
    image = [tile_index(*p) for p in rgba]
    tiles = [tuple(image[(ty * 8 + y) * width + tx * 8 + x] for y in range(8) for x in range(8))
             for ty in range(height // 8) for tx in range(width // 8)]
    if len(tiles) % 4:
        warnings.append(f"Avatar '{path.name}' has invalid dimensions and may not appear correctly. "
                        "Must be 16px tall and a multiple of 16px wide.")
    return tiles


def avatar_fonts(project, warnings):
    """compileAvatarFont: per chunk of 16 avatars, (recode length, tiles)."""
    used = used_avatars(project)
    fonts = []
    for k in range(0, len(used), AVATAR_FONT_SIZE):
        chunk = used[k:k + AVATAR_FONT_SIZE]
        tiles = [t for rel, _ in chunk for t in read_avatar_tiles(project, rel, warnings)]
        fonts.append((4 * len(chunk), tiles))
    return fonts


def printed_glyphs(texts, font_count, where):
    """{(avatar font, letter)} the texts print with an avatar font: font codes
    \\002 n select font n - 1; fonts from font_count on are avatar fonts."""
    from .gbvm.assembler import _asciz
    import re
    found = set()
    for text in texts:
        for raw in re.findall(r'^\s*\.asciz "(.*)"\s*$', text, re.M):
            data = _asciz(raw, where)[:-1]
            font, i = 0, 0
            while i < len(data):
                c = data[i]
                if c == 0x02:
                    font = data[i + 1] - 1
                elif c == 0x05 and font >= font_count:
                    found.add((font - font_count, data[i + 1]))
                elif c not in CONTROL and font >= font_count:
                    found.add((font - font_count, c))
                i += 1 + TEXT_ARGS[c]
    return found


def printed_fonts(texts, font_count, where, symbols=None):
    """(text fonts a script selects, a font is chosen at run time): codes
    \\002 n below the avatar fonts, and `%f` (vm_ui.c load_text: the font of a
    variable, any ui_fonts entry). Font 0 is always present (ui_init)."""
    from .gbvm.assembler import _asciz
    import re
    found, variable = {0}, False
    # Set Font: VM_SET_FONT <symbol> (font_constants) or an index.
    for text in texts:
        for operand in re.findall(r"^\s*VM_SET_FONT\s+(\w+)", text, re.M):
            index = int(operand) if operand.isdigit() else (symbols or {}).get(operand)
            if index is None:
                raise CompileError(f"{where}: VM_SET_FONT {operand}: no font of that symbol")
            if index < font_count:
                found.add(index)
    for text in texts:
        for raw in re.findall(r'^\s*\.asciz "(.*)"\s*$', text, re.M):
            data = _asciz(raw, where)[:-1]
            i = 0
            while i < len(data):
                c = data[i]
                if c == 0x25 and i + 1 < len(data):          # printf: %% literal, %f font variable
                    variable = variable or data[i + 1] == 0x66
                    i += 2
                    continue
                if c == 0x02 and data[i + 1] - 1 < font_count:
                    found.add(data[i + 1] - 1)
                i += 1 + TEXT_ARGS[c]
    return found, variable


def scene_glyphs(fonts, printed, where):
    """[(slot, GB tile)] of the glyphs printed in a scene: slot = font * 64 +
    letter (recode table: identity on 0..4n-1, mask 0x3F)."""
    out = []
    for font, ch in sorted(printed):
        if font >= len(fonts):
            raise CompileError(f"{where}: text selects font {font} beyond the avatar fonts")
        length, tiles = fonts[font]
        letter = ch & 0x3F
        if letter >= length or letter >= len(tiles):
            raise CompileError(f"{where}: avatar character {ch} reads outside avatar font {font} "
                               f"({length} entries, {len(tiles)} tiles): GB Studio would read past its data")
        out.append((font * 64 + letter, tiles[letter]))
    return out
