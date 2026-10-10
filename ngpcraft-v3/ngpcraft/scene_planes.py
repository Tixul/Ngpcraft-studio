"""A2/A4 scene allocation: main background behind, colour complement in front.

The generic background packer remains front-to-back. Its two layers have
disjoint opaque pixels, so their ordering can be reversed without changing
the reconstructed image. No extra tiles, palettes or per-frame code.
Text rendering and raster sharing are NOT delivered by this allocator.
"""
from dataclasses import replace

from .background import compile_background

FRONT_PLANE = 1
BACK_PLANE = 2


def compile_scene_background(width, height, pixels, *, where, animated=None, symbols=False, backdrop=None):
    asset = compile_background(width, height, pixels, planes=(BACK_PLANE, FRONT_PLANE), where=where,
                               animated=animated, symbols=symbols, backdrop=backdrop)
    if asset.complement_cells:
        # The packer's primary layer belongs behind; its fourth-colour
        # complement belongs in front. Keep the asset's front-to-back contract.
        asset = replace(asset, planes=asset.planes[::-1], palettes=asset.palettes[::-1],
                        maps=asset.maps[::-1])
    return asset


def plane_report(asset, *, text=False, second=False):
    shared = bool(second or asset.complement_cells or asset.priority_cells)
    return {"case": "B" if shared else "A", "front_plane": FRONT_PLANE,
            "background_plane": BACK_PLANE,
            "front_usage": "second_background" if second else "priority_mask" if asset.priority_cells else "colour_complement" if shared else "text" if text else "free",
            "complement_cells": len(asset.complement_cells),
            "text_plane": FRONT_PLANE, "text_requires_raster": shared,
            "text_status": "emitted" if text else "not_delivered",
            "raster_status": "emitted" if shared and text else "not_delivered",
            "backdrop_rgb444": asset.backdrop}
