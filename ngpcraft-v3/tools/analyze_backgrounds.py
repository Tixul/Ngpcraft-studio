"""Static M2 study, not a runtime exporter. Requires Pillow for PNG inspection.

GB Studio 4.3.2: compression.ts, tileData.ts, compileData.ts/getBackgroundPalette.
No project files are modified. Output is a new directory; RGB444 uses >> 4.
"""
import argparse
import collections
import json
from pathlib import Path

from PIL import Image
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ngpcraft.background import pack_palettes


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def decompress(text):
    out, pos = [], 0
    while pos < len(text):
        value = int(text[pos:pos + 2], 16)
        pos += 2
        if pos >= len(text):
            raise ValueError("tileColors: missing count")
        if text[pos] == "!":
            count, pos = 1, pos + 1
        else:
            end = text.find("+", pos)
            if end <= pos:
                raise ValueError("tileColors: invalid count")
            count, pos = int(text[pos:end], 16), end + 1
        out.extend([value] * count)
    return out


def rgb444(color):
    return tuple(int(color[n:n + 2], 16) >> 4 for n in (0, 2, 4))


def analyze(project, output):
    output.mkdir(parents=True, exist_ok=False)
    palettes = {p["id"]: p["colors"] for p in
                (read(f) for f in (project / "project/palettes").glob("*.gbsres"))}
    palettes["dmg"] = ["E8F8E0", "B0F088", "509878", "202850"]
    settings = read(project / "project/settings.gbsres")
    if settings["colorMode"] not in ("mixed", "color"):
        raise ValueError("Study supports color backgrounds only")
    defaults = settings["defaultBackgroundPaletteIds"]
    backgrounds = {read(f)["id"]: (read(f), f.with_suffix(""))
                   for f in (project / "assets/backgrounds").glob("*.gbsres")}
    rows = []
    for path in sorted((project / "project/scenes").rglob("scene.gbsres")):
        scene = read(path)
        bg, png = backgrounds[scene["backgroundId"]]
        if bg.get("autoColor") or bg.get("monoOverrideId"):
            raise ValueError(f"{path}: autoColor/monoOverride outside this study")
        ids = scene.get("paletteIds") or []
        slots = []
        for i in range(8):
            pid = ids[i] if i < len(ids) and ids[i] else defaults[i]
            if pid not in palettes:
                raise ValueError(f"{path}: unknown palette {pid}")
            slots.append(tuple(rgb444(c) for c in palettes[pid]))
        attrs = decompress(bg.get("tileColors") or "")
        with Image.open(png) as source:
            img = source.convert("RGB")
        w, h = img.size
        if w % 8 or h % 8:
            raise ValueError(f"{png}: non tile aligned dimensions")
        pixels = img.load()
        tiles, c0_usage = [], collections.Counter()
        for ty in range(h // 8):
            for tx in range(w // 8):
                i = ty * (w // 8) + tx
                attr = attrs[i] if i < len(attrs) else 0
                indices = tuple(3 if pixels[tx*8+x, ty*8+y][1] < 65 else
                                2 if pixels[tx*8+x, ty*8+y][1] < 130 else
                                1 if pixels[tx*8+x, ty*8+y][1] < 205 else 0
                                for y in range(8) for x in range(8))
                colors = tuple(slots[attr & 7][j] for j in indices)
                if 0 in indices:
                    c0_usage[slots[attr & 7][0]] += 1
                tiles.append((tx, ty, colors, attr))
        # A tile fits one plane iff it needs <=3 opaque colors after removing
        # the backdrop. ANY source index may become transparent, not just 0.
        candidates = sorted({c for _, _, cs, _ in tiles for c in cs})
        counts = {c: sum(len(set(cs) - {c}) > 3 for _, _, cs, _ in tiles)
                  for c in candidates}
        backdrop = min(candidates, key=lambda c: (counts[c], -c0_usage[c], c))
        old_backdrop = c0_usage.most_common(1)[0][0] if c0_usage else backdrop
        old_bad = sum(len(set(cs)) == 4 and slots[a & 7][0] != old_backdrop
                      for _, _, cs, a in tiles)
        reference = Image.new("RGB", (w, h))
        reconstructed = Image.new("RGB", (w, h))
        overlay = Image.new("RGB", (w, h))
        patterns, front_palettes, back_palettes = set(), set(), set()
        requirements = [tuple(sorted(set(cs) - {backdrop})) for _, _, cs, _ in tiles]
        packed_front = pack_palettes([r[:3] for r in requirements])
        packed_back = pack_palettes([r[3:] for r in requirements if len(r) > 3])
        bad_tiles = []
        for tx, ty, colors, attr in tiles:
            opaque = sorted(set(colors) - {backdrop})
            front = next(p for p in packed_front if set(opaque[:3]) <= set(p))
            back = next((p for p in packed_back if set(opaque[3:]) <= set(p)), ()) if len(opaque) > 3 else ()
            front_palettes.add(front)
            if back:
                back_palettes.add(back)
                bad_tiles.append([tx, ty])
            f = tuple(front.index(c)+1 if c in front else 0 for c in colors)
            b = tuple(back.index(c)+1 if c in back else 0 for c in colors)
            patterns.add(f)
            patterns.add(b)
            for n, expected in enumerate(colors):
                actual = front[f[n]-1] if f[n] else back[b[n]-1] if b[n] else backdrop
                if actual != expected:
                    raise AssertionError((path, tx, ty, n))
                x, y = n % 8, n // 8
                if attr & 32:
                    x = 7-x
                if attr & 64:
                    y = 7-y
                xy = tx*8+x, ty*8+y
                reference.putpixel(xy, tuple(v*17 for v in expected))
                reconstructed.putpixel(xy, tuple(v*17 for v in actual))
                overlay.putpixel(xy, (255, 0, 255) if back else tuple(v*17 for v in expected))
        name = path.parent.name
        if bad_tiles:
            reference.save(output / f"{name}-reference.png")
            reconstructed.save(output / f"{name}-reconstructed.png")
            overlay.save(output / f"{name}-affected.png")
        rows.append(dict(scene=name, dimensions=[w,h], tiles=len(tiles),
                         previous_rule_rgb444=old_bad, minimum_second_plane_tiles=counts[backdrop],
                         backdrop_rgb444="".join(f"{v:X}" for v in backdrop),
                         affected_coordinates=bad_tiles,
                         candidate_two_plane_patterns=len(patterns),
                         candidate_front_palette_sets=len(front_palettes),
                         candidate_back_palette_sets=len(back_palettes),
                         palette_budget_ok=len(packed_front) <= 16 and len(packed_back) <= 16,
                         pixel_mismatches=0,
                         priority_tiles=sum(bool(a & 128) for _,_,_,a in tiles)))
    report = dict(scope="Static manual-color gbs2 backgrounds; RGB444; no runtime or palette animation proof",
                  palette_budget="Greedy feasible allocation, not optimal; static colors only",
                  scenes=rows)
    temp = output / "report.json.tmp"
    temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temp.replace(output / "report.json")
    for r in rows:
        print(f"{r['scene']:24s} tiles={r['tiles']:4d} old={r['previous_rule_rgb444']:3d} "
              f"min_second={r['minimum_second_plane_tiles']:3d} backdrop={r['backdrop_rgb444']} "
              f"patterns={r['candidate_two_plane_patterns']:3d} "
              f"palette_sets={r['candidate_front_palette_sets']}/{r['candidate_back_palette_sets']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    analyze(args.project, args.out)
