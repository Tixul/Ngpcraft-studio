"""Runtime background/UI palette changes on the NGPC (docs/M4_PALETTES.md).

GB Studio writes CGB palette slots (vm_palette.c vm_load_palette). The V3
packs a scene's RGB colours into K2GE palettes, merging colours of different
GB slots. A slot a reachable script may rewrite is compiled by IDENTITY
instead: each of its pixels is the symbol (slot, GB colour index), packed
like a colour, then resolved to RGB444 for the ROM. Every K2GE entry holding
a symbol is listed, so VM_LOAD_PALETTE rewrites exactly the entries of the
slots in its mask. Unchanged slots keep the RGB packing.
"""
import re

from .compiler import CompileError

SYMBOL_BASE = 0x1000
SHADOW_BACKDROP = 192          # ngc_palette_shadow[192] = 0x83E0
LOAD = re.compile(r"^\s*VM_LOAD_PALETTE\s+([^,\n]+),\s*([^\n;]+)", re.M)


def symbol(slot, index):
    return SYMBOL_BASE + 4 * slot + index


def is_symbol(value):
    return value >= SYMBOL_BASE


def slot_of(value):
    return (value - SYMBOL_BASE) >> 2, (value - SYMBOL_BASE) & 3


def changed_slots(texts, where):
    """Background slots the VM_LOAD_PALETTE of these assembled scripts write."""
    slots = set()
    for match in LOAD.finditer("\n".join(texts)):
        mask, options = match[1].strip(), match[2].strip()
        if not re.fullmatch(r"\d+", mask):
            raise CompileError(f"{where}: VM_LOAD_PALETTE {mask}, {options}: only constant masks are delivered")
        if ".PALETTE_SPRITE" in options:
            continue            # sprite palettes map one to one: no identity packing (handler_source)
        if ".PALETTE_BKG" not in options:
            raise CompileError(f"{where}: VM_LOAD_PALETTE {mask}, {options}: no palette type")
        slots |= {i for i in range(8) if int(mask) >> i & 1}
    return frozenset(slots)


def changed_sprite_slots(texts, where):
    """Sprite palette slots (0..7) the VM_LOAD_PALETTE of these scripts write:
    native sprite palettes must not be placed there."""
    slots = set()
    for match in LOAD.finditer("\n".join(texts)):
        mask, options = match[1].strip(), match[2].strip()
        if ".PALETTE_SPRITE" not in options:
            continue
        if not re.fullmatch(r"\d+", mask):
            raise CompileError(f"{where}: VM_LOAD_PALETTE {mask}, {options}: only constant masks are delivered")
        slots |= {i for i in range(8) if int(mask) >> i & 1}
    return frozenset(slots)


def symbolic_pixels(source, slots_rgb, dynamic):
    """background_source pixels with the dynamic slots' colours as symbols."""
    columns = source.width // 8
    out = []
    for n, (rgb, index) in enumerate(zip(source.pixels, source.indices)):
        slot = source.attributes[(n // source.width // 8) * columns + (n % source.width) // 8] & 7
        out.append(symbol(slot, index) if slot in dynamic else rgb)
        if slot not in dynamic and rgb != slots_rgb[slot][index]:
            raise CompileError("palette symbols: source pixel does not match its slot colour (internal)")
    return tuple(out)


def resolve(value, slots_rgb):
    if not is_symbol(value):
        return value
    slot, index = slot_of(value)
    return slots_rgb[slot][index]


def resolve_palettes(palettes, slots_rgb, first_word):
    """(RGB palettes, [(shadow index, slot, GB index)]): palette k entry j is the
    K2GE word first_word + 4k + 1 + j (palette_words: entry 0 transparent)."""
    entries, out = [], []
    for k, palette in enumerate(palettes):
        for j, value in enumerate(palette):
            if is_symbol(value):
                entries.append((first_word + 4 * k + 1 + j, *slot_of(value)))
        out.append(tuple(resolve(v, slots_rgb) for v in palette))
    return tuple(out), entries


def resolve_background(asset, slots_rgb):
    """Background with RGB444 palettes and backdrop, and its symbol entries."""
    from dataclasses import replace
    entries, palettes = [], []
    for plane, plane_palettes in zip(asset.planes, asset.palettes):
        resolved, found = resolve_palettes(plane_palettes, slots_rgb, 64 * plane)
        palettes.append(resolved)
        entries += found
    if is_symbol(asset.backdrop):
        entries.append((SHADOW_BACKDROP, *slot_of(asset.backdrop)))
    return replace(asset, palettes=tuple(palettes), backdrop=resolve(asset.backdrop, slots_rgb)), entries


def map_bytes(entries):
    """Count, then (shadow index, slot << 2 | GB index) per entry."""
    entries = sorted(set(entries))
    if len(entries) > 255:
        raise CompileError(f"palette map: {len(entries)} entries, 255 at most")
    out = [len(entries)]
    for index, slot, gb in entries:
        out += [index, slot << 2 | gb]
    return out


def handler_source(maps, *, scenes, shadow, fade):
    """C89 vm_load_palette (vm_palette.c) on the K2GE, one map per scene index.

    GBVM: each set MASK bit is followed by one palette_entry_t (.CGB_PAL: four
    RGB555 words, low byte first); PC skips them. COMMIT writes the colours at
    once (set_bkg_palette, even faded out); the buffers fades start from (the
    shadow) always take them. RGB555 >> 1 per channel equals scene_assets.rgb444
    of the same hex colour (parseR/G/B: floor(h * 32 / 256), blue at least 1)."""
    data, starts = [], []
    for entries in maps:
        starts.append(len(data))
        data += map_bytes(entries)
    if len(data) > 65535:
        raise CompileError("palette map: more than 64 KiB")
    lines = ["/* vm_palette.c vm_load_palette on the K2GE (docs/M4_PALETTES.md). */",
             f"static const UBYTE ngc_palette_map[{len(data)}] = {{{', '.join(f'{v}u' for v in data)}}};",
             f"static const UWORD ngc_palette_map_at[{len(starts)}] = {{{', '.join(f'{v}u' for v in starts)}}};"]
    if scenes:
        lines.append("extern unsigned char ngc_scene;")
    lines += ["static void vm_load_palette(SCRIPT_CTX * THIS, UBYTE mask, UBYTE options)", "{",
              "    const UBYTE * src;", "    const UBYTE * map;", "    const UBYTE * p;", "    UBYTE at[8];",
              "    UBYTE i;", "    UBYTE n;", "    UBYTE count;", "    UWORD c;",
              "    src = THIS->PC;", "    n = 0u;",
              "    for (i = 0u; i < 8u; ++i) {",
              "        if (mask & (UBYTE)(1u << i)) { at[i] = n; n = (UBYTE)(n + 8u); } else at[i] = 0xFFu;",
              "    }",
              "    THIS->PC += n;",
              "    if (options & 4u) {             /* .PALETTE_SPRITE: GB sprite slot k is K2GE sprite palette k */",
              "        for (i = 0u; i < 8u; ++i) {",
              "            if (at[i] == 0xFFu) continue;",
              "            for (count = 1u; count < 4u; ++count) {   /* entries [c0, c0, c1, c3]: 1..3 */",
              "                p = src + at[i] + (count << 1);",
              "                c = (UWORD)((UWORD)p[0] | ((UWORD)p[1] << 8));",
              "                c = (UWORD)(((c >> 1) & 0x000Fu) | (((c >> 6) & 0x000Fu) << 4) | (((c >> 11) & 0x000Fu) << 8));",
              *(["                ngc_palette_shadow[((UWORD)i << 2) + count] = c;"] if shadow else []),
              "                if (options & 1u) ((volatile UWORD *)0x8200)[((UWORD)i << 2) + count] = c;",
              "            }",
              "        }",
              *(["        ngc_staged_step = 0xFFu;"] if fade else []),
              "        return;",
              "    }",
              "    if (!(options & 2u)) return;    /* .PALETTE_BKG */",
              f"    map = ngc_palette_map + ngc_palette_map_at[{'ngc_scene' if scenes else '0'}];",
              "    for (count = *map++; count != 0u; --count, map += 2) {",
              "        i = at[map[1] >> 2];",
              "        if (i == 0xFFu) continue;",
              "        p = src + i + ((map[1] & 3u) << 1);",
              "        c = (UWORD)((UWORD)p[0] | ((UWORD)p[1] << 8));",
              "        c = (UWORD)(((c >> 1) & 0x000Fu) | (((c >> 6) & 0x000Fu) << 4) | (((c >> 11) & 0x000Fu) << 8));",
              *(["        ngc_palette_shadow[map[0]] = c;"] if shadow else []),
              "        if (options & 1u) {          /* .PALETTE_COMMIT */",
              "            if (map[0] == 192u) *(volatile UWORD *)0x83E0 = c;",
              "            else ((volatile UWORD *)0x8200)[map[0]] = c;",
              "        }",
              "    }",
              *(["    ngc_staged_step = 0xFFu;           /* the staged fade step used the old colours */"] if fade else []),
              "}"]
    return "\n".join(lines) + "\n"
