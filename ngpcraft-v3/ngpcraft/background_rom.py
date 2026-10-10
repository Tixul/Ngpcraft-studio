"""Explicit static-background proof export; not the GB Studio project pipeline."""
import hashlib
import json
import os
from pathlib import Path

from .compiler import CompileError
from .runtime_units import select_units, describe_unit, emit_unit_files


def c_array(lines, name, values, ctype="unsigned short"):
    lines.append(f"static const {ctype} {name}[{len(values)}] = {{")
    if ctype == "unsigned short":
        text = [f"0x{v:04X}u" for v in values]
    else:
        text = [str(v) for v in values]
    for offset in range(0, len(text), 12):
        lines.append("    " + ", ".join(text[offset:offset+12]) + ",")
    lines.append("};")


def tile_words(asset):
    return [int.from_bytes(t[i:i+2], "little") for t in asset.tiles for i in range(0, 16, 2)]


def palette_words(palettes):
    return [v for p in palettes for v in (0,) + p + (0,)*(3-len(p))]


def background_source(asset):
    if asset.width != 160 or asset.height != 152:
        raise CompileError("background ROM: explicit 160x152 viewport required; no implicit padding/crop")
    lines = ["/* Original V3 static background loader; SNK K2GETechRef 4-3-5, 4-4, 4-12.",
             " * Called once before interrupts by the existing startup. No frame-time work. */"]
    c_array(lines, "bg_tiles", tile_words(asset))
    for plane, palettes, tilemap in zip(asset.planes, asset.palettes, asset.maps):
        c_array(lines, f"bg_pal{plane}", palette_words(palettes))
        c_array(lines, f"bg_map{plane}", tilemap)
    lines.extend(["void ngc_background_load(void)", "{", "    volatile unsigned short *dst;",
                  "    unsigned short i;", "    unsigned short x;", "    unsigned short y;",
                  "    unsigned short source;",
                  f"    dst = (volatile unsigned short *)0x{0xA000+asset.tile_base*16:04X};",
                  f"    for (i = 0u; i < {len(asset.tiles)*8}u; ++i) dst[i] = bg_tiles[i];"])
    for plane, palettes, tilemap in zip(asset.planes, asset.palettes, asset.maps):
        lines.extend([f"    dst = (volatile unsigned short *)0x{0x8280+(plane-1)*128:04X};",
                      f"    for (i = 0u; i < {len(palettes)*4}u; ++i) dst[i] = bg_pal{plane}[i];",
                      f"    dst = (volatile unsigned short *)0x{0x9000+(plane-1)*2048:04X};",
                      "    source = 0u;", "    for (y = 0u; y < 19u; ++y) {",
                      "        for (x = 0u; x < 20u; ++x) {",
                      f"            dst[y * 32u + x] = bg_map{plane}[source++];",
                      "        }", "    }"])
    lines.extend([f"    *(volatile unsigned short *)0x83E0 = 0x{asset.backdrop:04X}u;",
                  "    *(volatile unsigned char *)0x8118 = 0x80u;",
                  f"    *(volatile unsigned char *)0x8030 = {0 if asset.planes[0] == 1 else 128}u;",
                  "    *(volatile unsigned short *)0x8032 = 0u;",
                  "    *(volatile unsigned short *)0x8034 = 0u;", "}"])
    return "\n".join(lines) + "\n"


def write_export(features, files, purpose, extra, destination, label):
    """Select units, add the manifest, write every file atomically into a new directory."""
    units = select_units(features, "rom")
    files = {**emit_unit_files(units), **files}
    manifest = {"format": "ngpcraft-export/0.1", "target": "ngpc-rom", "complete_rom": False,
                "sources": sorted(p for p in files if p.endswith(".c")), "features": features,
                "runtime_units": [describe_unit(u) for u in units],
                "purpose": purpose, **extra,
                "sha256": {p: hashlib.sha256(s.encode("utf-8")).hexdigest() for p,s in files.items()}}
    files["export.json"] = json.dumps(manifest, indent=2) + "\n"
    destination = Path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise CompileError(f"{label}: destination must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temp, path)
    return manifest


def export_background_rom(asset, destination):
    """Diagnostic only: caller supplies the exact viewport, no project is imported."""
    files = {"src/game.h": "void ngc_game_init(void);\nvoid ngc_game_tick(void);\n",
             "src/game.c": '#include "game.h"\nvoid ngc_background_load(void);\n'
                           'void ngc_game_init(void) { ngc_background_load(); }\n'
                           'void ngc_game_tick(void) {}\n',
             "src/scene_background.c": background_source(asset)}
    return write_export(["static_background"], files,
                        "static background proof, no GB Studio gameplay/events",
                        {"background": asset.report()}, destination, "background ROM")
