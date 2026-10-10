"""Projectile launch arithmetic; integration into scene exports is still pending.

GBVM bd6f41c projectiles.c / sincos.h, MIT (c) 2020 Toxa.
Positions wrap in unsigned 16-bit subpixels; deltas are signed 16-bit.
The initial offset deliberately rounds after EACH 255-subpixel chunk.
"""
import json
from pathlib import Path
from .background_rom import c_array


def projectile_motion_source():
    frozen = json.loads((Path(__file__).parent / "runtime/projectile_motion.json").read_text(encoding="utf-8"))
    lines = ["/* GBVM bd6f41c projectiles.c / sincos.h; MIT (c) 2020 Toxa. */"]
    c_array(lines, "proj_sine", frozen["sine_wave"], "signed char")
    lines.extend('''
/* animation direction: down=0, right=1, up=2, left=3. */
unsigned char ngc_projectile_direction(unsigned char angle)
{
    if (angle > 224u) return 2u;
    if (angle >= 160u) return 3u;
    if (angle > 96u) return 0u;
    if (angle >= 32u) return 1u;
    return 2u;
}

/* pos = unsigned x,y in 1/32 px; delta = signed x,y in 1/32 px/tick.
 * No actor, asset, pool or VM state is implicit in this arithmetic kernel. */
void ngc_projectile_motion(unsigned short *pos, short *delta,
                           unsigned char angle, unsigned char speed,
                           unsigned short initial_offset)
{
    short sinv;
    short cosv;
    if (angle == 192u) {
        pos[0] = (unsigned short)(pos[0] - initial_offset);
        delta[0] = (short)(-(short)speed); delta[1] = 0;
    } else if (angle == 64u) {
        pos[0] = (unsigned short)(pos[0] + initial_offset);
        delta[0] = (short)speed; delta[1] = 0;
    } else if (angle == 0u) {
        pos[1] = (unsigned short)(pos[1] - initial_offset);
        delta[0] = 0; delta[1] = (short)(-(short)speed);
    } else if (angle == 128u) {
        pos[1] = (unsigned short)(pos[1] + initial_offset);
        delta[0] = 0; delta[1] = (short)speed;
    } else {
        sinv = (short)proj_sine[angle];
        cosv = (short)proj_sine[(unsigned char)((unsigned short)angle + 64u)];
        while (initial_offset > 255u) {
            pos[0] = (unsigned short)(pos[0] + ((sinv * (short)255) >> 7));
            pos[1] = (unsigned short)(pos[1] - ((cosv * (short)255) >> 7));
            initial_offset = (unsigned short)(initial_offset - 255u);
        }
        if (initial_offset != 0u) {
            pos[0] = (unsigned short)(pos[0] + ((sinv * (short)initial_offset) >> 7));
            pos[1] = (unsigned short)(pos[1] - ((cosv * (short)initial_offset) >> 7));
        }
        delta[0] = (short)((sinv * (short)speed) >> 7);
        delta[1] = (short)(-((cosv * (short)speed) >> 7));
    }
}
'''.strip().splitlines())
    return lines + [""]
