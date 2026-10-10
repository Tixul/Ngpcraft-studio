"""GB Studio scene parallax on the NGPC raster (ECARTS §6, docs/M6_PARALLAX.md).

GB Studio: generateGBVMData.ts compileParallax writes up to 3 PARALLAX_STEP rows
(parallax.h); GBVM scroll.c scroll_viewport gives each upper slice the x scroll
draw_scroll_x shifted by its speed and loads its columns over its own rows; the
LCD interrupt (parallax.c parallax_LCD_isr) sets SCX/SCY when each slice begins.
NGPC: the same slices of the same background plane; the scroll register of the
plane changes only at the lines where a slice begins (Timer0 chain,
runtime/parallax.c), nothing at all in a scene without parallax.
"""
from .compiler import CompileError

SCREEN_WIDTH_TILES = 20          # generateGBVMData.ts: parallax only if scene.width > SCREEN_WIDTH
PARALLAX_MAX_HEIGHT = 20         # parallax.h
# ParallaxSpeedSelect.tsx: 0..8 = speed 1, 1/2 .. 1/256 (x >> n); 128 = fixed
# position, compiled as wrapSigned8Bit(128) = -128: x << 128 is 0 on SDCC.
FIXED = -128


def wrap_signed_8bit(value):
    """shared/lib/helpers/8bit.ts wrapSigned8Bit."""
    u = (256 + (value % 256)) % 256
    return u - 256 if u >= 128 else u


def _step(start, end, speed):
    return (start, end, wrap_signed_8bit(speed))


def compile_parallax(scene, where=None):
    """generateGBVMData.ts compileParallax: the scene's PARALLAX_STEP(start, end,
    shift) rows, () without parallax (or a scene not wider than the screen)."""
    where = where or f"scene {scene['name']}"
    layers = scene.get("parallax")
    if not layers or scene["width"] <= SCREEN_WIDTH_TILES:
        return ()
    if not isinstance(layers, list) or len(layers) > 3:
        raise CompileError(f"{where}: parallax must hold 1 to 3 layers")
    for k, layer in enumerate(layers):
        if (not isinstance(layer, dict) or type(layer.get("height")) is not int or type(layer.get("speed")) is not int
                or layer["height"] < 0 or layer["speed"] not in (*range(9), 128)):
            raise CompileError(f"{where}: parallax layer {k}: height and speed (0..8 or 128 fixed) expected, "
                               f"got {layer!r}")
    row, steps = 0, []
    for k, layer in enumerate(layers):
        last = k == len(layers) - 1
        if len(layers) < 3 and last and layer["speed"] != 0:
            steps.append(_step(row, 18, layer["speed"]))
        elif last:
            steps.append(_step(row, 0, layer["speed"]))
        else:
            steps.append(_step(row, row + layer["height"], layer["speed"]))
            row += layer["height"]
    if len(layers) < 3:
        steps.append(_step(18, 0, 0))
    return tuple(steps)


def bands(steps, rows, view_h, top, where):
    """The slices the NGPC shows: [(first row, end row, shift or None)], and the
    screen lines where slices 1.. begin. A slice is the step's rows (the last
    one: to the bottom of the map) at its x scroll: GBVM's last slice (next_y 0)
    follows the camera (shift None). Slices starting below the view are not
    shown, so they cost no split (the 144 px view never shows GB row 18)."""
    out = []
    for start, end, shift in steps:
        if start * 8 >= view_h:
            break
        main = end == 0
        stop = rows if main else min(end, rows)
        if not main and shift not in (*range(9), FIXED):
            raise CompileError(f"{where}: parallax shift {shift} is not produced by GB Studio 4.3.2")
        out.append((start, stop, None if main else shift))
        if main:
            break
    if out[0][0] != 0:
        raise CompileError(f"{where}: parallax must start at row 0")
    lines = [top + start * 8 for start, _, _ in out[1:]]
    return out, lines


def share_timer0(gbvm_c):
    """The text split (runtime/text_split.c in gbvm.c: text on the background
    plane, Overlay Set Scanline Cutoff) and the parallax chain both use Timer0.
    GBVM runs parallax_LCD_isr instead of simple_LCD_isr in a parallax scene
    (core.c), so the window is never cut there: the split stays off while a
    parallax scene is loaded, and re-installs its own Timer0 vector when it
    arms elsewhere (a parallax scene installed the chain's)."""
    head = "static void ngc_split_arm(unsigned char line, unsigned char x, unsigned char y)\n{\n"
    start = "    *(volatile unsigned char *)0x0024 &= 0x3Cu;\n"
    if gbvm_c.count(head) != 1 or gbvm_c.count(start) != 1:
        raise CompileError("text split: Timer0 anchors changed (ngpcraft/parallax.py share_timer0)")
    off = ("    if (ngc_parallax_n) {  /* parallax scene: parallax_LCD_isr, no window cut (GBVM core.c) */\n"
           "        split_active = 0u;\n        split_line = 152u;\n        return;\n    }\n")
    gbvm_c = gbvm_c.replace(head, "extern unsigned char ngc_parallax_n;\n" + head + off)
    return gbvm_c.replace(start, "    *(NgcSplitInterrupt **)0x6FD4 = ngc_split_irq;\n" + start)
