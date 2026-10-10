"""Scene-local residency from reachable GBVM spritesheet instructions.

The bank pointer retains a stable project ID across shared/custom scripts.
Only sheets referenced by the scene's reachable scripts are converted/uploaded.
"""
import re

def far_assets(texts, sprites, opcode):
    symbols = set()
    for text in texts:
        symbols.update(re.findall(r"^\s*" + re.escape(opcode) + r"\s+[^,]+,\s*___bank_(\w+),\s*_\1\s*$", text, re.M))
    return tuple((i, s["id"]) for i, s in enumerate(sprites) if s["symbol"] in symbols)

def sprite_assets(texts, sprites):
    return far_assets(texts, sprites, "VM_ACTOR_SET_SPRITESHEET")

def emote_assets(texts, emotes):
    return far_assets(texts, emotes, "VM_ACTOR_EMOTE")

def global_projectile_symbols(shared):
    """loadProjectile tables: _global_projectiles_N = N (table index), bank 0."""
    return {t["symbol"]: k for k, t in enumerate(shared.get("globalProjectiles", ()))}


# dialogueFrameSetTiles names tileset_default_frame for an unknown tileset:
# the default frame (emit_ui frame support).
DEFAULT_FRAME = 0xFFFF


def bank_assets(shared):
    return {**global_projectile_symbols(shared), "tileset_default_frame": DEFAULT_FRAME,
            **{s["symbol"]: i for kind in ("sprites", "emotes", "tilesets") for i, s in enumerate(shared[kind])},
            **{s["dataName"] + "_Data": i for i, s in enumerate(shared["nativeMusicSelected"] if "nativeMusicSelected" in shared else [])}}
