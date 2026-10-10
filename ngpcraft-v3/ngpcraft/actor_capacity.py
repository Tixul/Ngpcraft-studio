"""Explicit NGPC scene capacity; player is additional to this NPC count.

GB Studio 4.3.2 src/consts.ts keeps twenty authored actors. The NGPC arrays
are specialized to the actual scene count, never the configured ceiling.
"""
GBS_ACTOR_CAPACITY = 20
NGPC_ACTOR_CAPACITY = 30


def actor_capacity(project):
    ext = project.ngpc["ngpc"]
    return ext["actor_capacity"] if "actor_capacity" in ext else GBS_ACTOR_CAPACITY
