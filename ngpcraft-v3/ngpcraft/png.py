"""Minimal PNG reader (ISO/IEC 15948) for project assets: RGBA pixels, no dependency.

GB Studio reads PNGs as RGBA (pngjs) before indexing them. Supported: colour
types 0, 2, 3, 4, 6; bit depths 1-8, and 16 for types 0, 2, 4, 6 (scaled to 8
bits as pngjs 6 format-normaliser.js scaleDepth: floor(v * 255 / 65535 + 0.5));
no interlacing (refused). tRNS is honoured for palette, grey and RGB images
(compared at the image's own depth, as pngjs replaceTransparentColor). Anything else is refused
with the file name, never guessed.
"""
import struct
import zlib

from .compiler import CompileError

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path):
    """(width, height, [(r, g, b, a), ...]) row by row."""
    with open(path, "rb") as stream:
        data = stream.read()
    where = str(path)
    if data[:8] != SIGNATURE:
        raise CompileError(f"{where}: not a PNG file")
    pos, header, palette, alpha, chunks = 8, None, None, None, []
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length:
            raise CompileError(f"{where}: truncated chunk {kind!r}")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            alpha = body
        elif kind == b"IDAT":
            chunks.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if header is None or not chunks:
        raise CompileError(f"{where}: missing IHDR or IDAT")
    width, height, depth, color, compression, filtering, interlace = header
    if (color not in CHANNELS or depth not in (1, 2, 4, 8, 16) or (color in (2, 4, 6) and depth < 8)
            or (color == 3 and depth == 16)):
        raise CompileError(f"{where}: colour type {color} with {depth}-bit samples not supported")
    if compression or filtering or interlace:
        raise CompileError(f"{where}: interlaced or non-standard PNG not supported")
    if color == 3 and palette is None:
        raise CompileError(f"{where}: palette image without PLTE")
    raw = zlib.decompress(b"".join(chunks))
    bpp = max(1, CHANNELS[color] * depth // 8)
    stride = (width * CHANNELS[color] * depth + 7) // 8
    if len(raw) < height * (stride + 1):
        raise CompileError(f"{where}: image data too short")
    rows, prev = [], bytearray(stride)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif kind == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 255
            elif kind != 0:
                raise CompileError(f"{where}: row {y} has unknown filter {kind}")
        rows.append(line)
        prev = line
    pixels = []
    scale = 1 if depth == 16 else 255 // ((1 << depth) - 1)
    key = struct.unpack(">H", alpha[:2])[0] if alpha and color == 0 else None
    rgb_key = struct.unpack(">HHH", alpha[:6]) if alpha and color == 2 else None
    for line in rows:
        if depth == 16:
            raw16 = struct.unpack(f">{len(line) // 2}H", bytes(line))
            samples = [(v * 255 + 32767) // 65535 for v in raw16]
        elif depth == 8:
            raw16 = samples = line
        else:
            per = 8 // depth
            raw16 = samples = [(line[x // per] >> (8 - depth * (x % per + 1))) & ((1 << depth) - 1)
                               for x in range(width)]
        for x in range(width):
            if color == 3:
                index = samples[x]
                if index >= len(palette):
                    raise CompileError(f"{where}: palette index {index} out of range")
                r, g, b = palette[index]
                pixels.append((r, g, b, alpha[index] if alpha and index < len(alpha) else 255))
            elif color == 0:
                v = samples[x]
                pixels.append((v * scale, v * scale, v * scale, 0 if raw16[x] == key else 255))
            elif color == 4:
                v, a = samples[2 * x], samples[2 * x + 1]
                pixels.append((v, v, v, a))
            elif color == 2:
                r, g, b = samples[3 * x:3 * x + 3]
                pixels.append((r, g, b, 0 if tuple(raw16[3 * x:3 * x + 3]) == rgb_key else 255))
            else:
                pixels.append(tuple(samples[4 * x:4 * x + 4]))
    return width, height, pixels
