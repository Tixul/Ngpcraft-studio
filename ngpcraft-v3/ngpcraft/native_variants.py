"""Colour variants of a native background (docs/PLAN_MEGAMAN.md L5, decision 0.6).

ngpcraft.json `resources.<background id>.ngpc_color_variants`: recoloured
copies of the background PNG, `[{"name": ..., "image": "<project path>.png"}]`.
Variant 0 is the background as drawn; variant k (1..) the k-th entry. A
variant only recolours: same tiles, same palette of each cell, so switching is
rewriting the plane's palettes and the backdrop. The rule, checked pixel by
pixel: inside one native palette a colour always becomes the same colour, and
the backdrop too (anything else = refusal, with the first cell and colours).
A transparent variant pixel keeps the backdrop.
"""
from pathlib import PurePosixPath

from .compiler import CompileError

KEY = "ngpc_color_variants"
MAX_VARIANTS = 15             # + the original: 16 colour sets per background


def validate_variants(value, where, root):
    """Raise ValueError on a malformed list (ngpcraft.json load)."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_VARIANTS:
        raise ValueError(f"{where}: expected a list of 1..{MAX_VARIANTS} variants")
    names = set()
    for i, entry in enumerate(value):
        at = f"{where}[{i}]"
        if not isinstance(entry, dict) or set(entry) != {"name", "image"}:
            raise ValueError(f"{at}: expected {{\"name\", \"image\"}}")
        name, image = entry["name"], entry["image"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{at}.name: expected a non-empty text")
        if name in names:
            raise ValueError(f"{at}.name: {name!r} used twice")
        names.add(name)
        if (not isinstance(image, str) or not image.lower().endswith(".png") or "\\" in image
                or PurePosixPath(image).is_absolute() or ".." in PurePosixPath(image).parts):
            raise ValueError(f"{at}.image: expected a project-relative path to a .png (forward slashes)")
        if root is not None and not (root / PurePosixPath(image)).is_file():
            raise ValueError(f"{at}.image: {image} not found in the project")


def variant_pixels(rgba):
    """RGB444 words of a variant PNG, None for transparent (alpha < 128)."""
    from .native_color import to_rgb444
    return tuple(None if a < 128 else to_rgb444(r, g, b) for r, g, b, a in rgba)


def variant_palettes(asset, base, variant, where):
    """(palettes, backdrop) of one variant, in the layout of asset.palettes[0].

    asset: the compiled native background (background.Background, one plane);
    base: its RGB444 pixels; variant: variant_pixels() of the recoloured PNG."""
    if len(asset.palettes) != 1 or asset.complement_cells:
        raise CompileError(f"{where}: colour variants need a background on one plane")
    if asset.animated_cells:
        raise CompileError(f"{where}: colour variants with replaced tiles are not delivered")
    if asset.recycle:
        raise CompileError(f"{where}: colour variants of a background over 16 palettes (recycled slots) "
                           "are not delivered (docs/PALETTE_RECYCLE.md)")
    if len(variant) != len(base):
        raise CompileError(f"{where}: the variant image must have the background's size")
    width = asset.width
    cols = width // 8
    palettes = asset.palettes[0]
    mapping = [dict() for _ in palettes]
    backdrop = []
    for n, word in enumerate(asset.maps[0]):
        slot = (word >> 9) & 15
        tx, ty = n % cols, n // cols
        for y in range(8):
            for x in range(8):
                i = (ty * 8 + y) * width + tx * 8 + x
                old, new = base[i], variant[i]
                if old == asset.backdrop:
                    if new is not None:
                        backdrop.append((new, tx, ty))
                    continue
                if new is None:
                    raise CompileError(f"{where}: tile ({tx}, {ty}): transparent pixel ({x}, {y}) where the "
                                       f"background has colour {old:03X} (only the backdrop may be transparent)")
                seen = mapping[slot].setdefault(old, new)
                if seen != new:
                    raise CompileError(f"{where}: tile ({tx}, {ty}): colour {old:03X} of palette {slot} becomes "
                                       f"{seen:03X} and {new:03X}: a variant may only recolour")
    colours = {c for c, _, _ in backdrop}
    if len(colours) > 1:
        first = {c: (tx, ty) for c, tx, ty in reversed(backdrop)}
        raise CompileError(f"{where}: the backdrop becomes " + ", ".join(
            f"{c:03X} (tile {first[c]})" for c in sorted(colours)) + ": one colour only")
    new_backdrop = colours.pop() if colours else asset.backdrop
    out = []
    for slot, palette in enumerate(palettes):
        out.append(tuple(mapping[slot].get(c, c) for c in palette))
    return tuple(out), new_backdrop


def runtime_source(per_scene, *, scenes, shadow, fade):
    """C89 for gbvm.c: ngc_bg_variant(THIS), called by VM_CALL_NATIVE with the
    variant index on the VM stack (the GB Studio plugin idiom: push, call, pop).

    per_scene: one report["color_variants"] per scene index ({"plane", "sets"}),
    None for a scene without the scene engine. Writes like vm_load_palette
    with COMMIT: palette RAM now, and the shadow fades start from; an index
    without a variant leaves the colours unchanged."""
    table, at, words, count, base = [], [], [], [], []
    for entry in per_scene:
        at.append(len(table))
        if entry is None:
            words.append(0); count.append(0); base.append(0)
            continue
        sets = entry["sets"]
        n = len(sets[0][0])
        if any(len(w) != n for w, _ in sets):
            raise CompileError("colour variants: palette sets of different sizes")
        for w, backdrop in sets:
            table += list(w) + [backdrop]
        words.append(n); count.append(len(sets)); base.append(64 * entry["plane"])
    if len(table) > 32767:
        raise CompileError("colour variants: table larger than 64 KiB")
    arr = lambda name, ctype, values: f"static const {ctype} {name}[{len(values)}] = {{{', '.join(f'{v}u' for v in values)}}};"
    lines = ["/* NGPC background colour variants (ngpcraft/native_variants.py, PLAN_MEGAMAN L5). */",
             arr("ngc_variant_words", "UWORD", table or [0]),
             arr("ngc_variant_at", "UWORD", at), arr("ngc_variant_n", "UBYTE", words),
             arr("ngc_variant_count", "UBYTE", count), arr("ngc_variant_base", "UBYTE", base)]
    if scenes:
        lines.append("extern unsigned char ngc_scene;")
    k = "ngc_scene" if scenes else "0"
    lines += ["static void ngc_bg_variant(SCRIPT_CTX * THIS)", "{",
              "    const UWORD * src;", "    volatile UWORD * dst;", "    UWORD v;", "    UBYTE n;", "    UBYTE i;",
              "    UBYTE b;",
              "    v = (UWORD)*(THIS->stack_ptr - 1);",
              f"    if (v >= (UWORD)ngc_variant_count[{k}]) return;     /* no such variant: unchanged */",
              f"    n = ngc_variant_n[{k}];",
              f"    b = ngc_variant_base[{k}];",
              f"    src = ngc_variant_words + ngc_variant_at[{k}] + v * (UWORD)(n + 1u);",
              "    dst = (volatile UWORD *)0x8200 + b;",
              "    for (i = 0u; i < n; ++i) {",
              *(["        ngc_palette_shadow[(UWORD)b + i] = src[i];"] if shadow else []),
              "        dst[i] = src[i];",
              "    }",
              *(["    ngc_palette_shadow[192] = src[n];"] if shadow else []),
              "    *(volatile UWORD *)0x83E0 = src[n];",
              *(["    ngc_staged_step = 0xFFu;           /* the staged fade step used the old colours */"] if fade else []),
              "}"]
    return "\n".join(lines) + "\n"
