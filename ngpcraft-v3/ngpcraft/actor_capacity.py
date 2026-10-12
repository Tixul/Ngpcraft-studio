"""Explicit NGPC scene capacity; player is additional to this NPC count.

GB Studio 4.3.2 src/consts.ts keeps twenty authored actors. The NGPC arrays
are specialized to the actual scene count, never the configured ceiling.
"""
GBS_ACTOR_CAPACITY = 20
# Raised from 30 to 64 (2026-10-12, large scenes): only the actors near the
# screen are active and run their update scripts; the RAM and stack audits of
# the build still refuse a game that does not fit.
NGPC_ACTOR_CAPACITY = 64


def actor_capacity(project):
    ext = project.ngpc["ngpc"]
    return ext["actor_capacity"] if "actor_capacity" in ext else GBS_ACTOR_CAPACITY
