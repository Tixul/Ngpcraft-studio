"""GB Studio sprite PNGs as indexed images (readFileToIndexedImage +
spriteDataIndexFn, via ngpcraft.png), and the editor's frame drawing rule used
as a test oracle."""
import json
from pathlib import Path

from ngpcraft.png import read_png
from ngpcraft.sprites import Indexed, sprite_index, TRANSPARENT


def read_indexed(path):
    width, height, rgba = read_png(path)
    return Indexed(width, height, [sprite_index(*p) for p in rgba])


def load_sheets(project):
    """{name: (sheet json, indexed image)} for every sprite sheet of a project."""
    out = {}
    for res in sorted((Path(project) / "assets/sprites").rglob("*.gbsres")):
        sheet = json.loads(res.read_text(encoding="utf-8"))
        out[sheet["name"]] = (sheet, read_indexed(res.with_suffix("")))
    return out


def editor_frame(sheet, image, frame, mode, mirrored):
    """MetaspriteCanvas.worker: tiles in list order, later on top; whole canvas
    mirrored for a flipped animation. Returns {(x, y): (value, paletteIndex)}."""
    w, h = sheet["canvasWidth"], sheet["canvasHeight"]
    tall = 8 if mode == "8x8" else 16
    canvas = {}
    for tile in frame["tiles"]:
        draw_x = max(0, w // 2 - 8) + tile["x"]
        draw_y = h - tall - tile["y"]
        for y in range(tall):
            for x in range(8):
                sx = 7 - x if tile["flipX"] else x
                sy = tall - 1 - y if tile["flipY"] else y
                px, py = tile["sliceX"] + sx, tile["sliceY"] + sy
                if not (0 <= px < image.width and 0 <= py < image.height):
                    continue
                value = image.data[px + py * image.width]
                cx, cy = draw_x + x, draw_y + y
                if value != TRANSPARENT and 0 <= cx < w and 0 <= cy < h:
                    canvas[(cx, cy)] = (value, tile["paletteIndex"] & 7)
    if mirrored:
        canvas = {(w - 1 - x, y): v for (x, y), v in canvas.items()}
    return canvas
