"""Lossless VM snapshot oracle, pinned GBVM bd6f41c (M5 step 3).

All allocated script memory is kept, including inactive stacks. Context
base_addr and ID are reconstructed from script_runner_init's reverse stack
allocation. Every other SCRIPT_CTX field, and all four scheduler pointers,
are kept. The outer journal supplies the build signature and CRC.
"""
import struct
from dataclasses import dataclass

MAGIC = b"NV1"
HEADER_BYTES = 12
CONTEXT_BYTES = 17
FIELDS = ("PC", "bank", "next", "update_fn", "update_fn_bank", "stack_ptr", "base_addr",
          "ID", "hthread", "terminated", "waitable", "lock_count", "flags")
HEADS = ("first_ctx", "free_ctxs", "old_executing_ctx", "executing_ctx")


@dataclass(frozen=True)
class Layout:
    contexts: int
    heap_words: int
    stack_words: int
    memory_address: int

    def __post_init__(self):
        if not (type(self.contexts) is int and 1 <= self.contexts <= 255
                and type(self.heap_words) is int and 0 <= self.heap_words <= 32768
                and type(self.stack_words) is int and 1 <= self.stack_words <= 64
                and type(self.memory_address) is int and self.memory_address % 2 == 0
                and 0x4000 <= self.memory_address < 0x6C00
                and self.words <= 32768 and self.memory_address + self.words * 2 <= 0x6C00):
            raise ValueError("invalid target VM layout")

    @property
    def words(self):
        return self.heap_words + self.contexts * self.stack_words

    @property
    def size(self):
        return HEADER_BYTES + CONTEXT_BYTES * self.contexts + 2 * self.words

    def base(self, index):
        return self.memory_address + 2 * (self.heap_words + (self.contexts - 1 - index) * self.stack_words)


def _number(value, maximum, label):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{label}: out of range")
    return value


def _ref(value, layout):
    return 0 if value is None else _number(value, layout.contexts - 1, "context reference") + 1


def encode(layout, snapshot):
    """Snapshot uses context indices (None = NULL) and actual target pointers."""
    contexts = snapshot["contexts"]
    memory = snapshot["memory"]
    if len(contexts) != layout.contexts or len(memory) != layout.words:
        raise ValueError("VM snapshot dimensions differ from the build")
    data = bytearray(MAGIC + bytes([layout.contexts]) + struct.pack("<H", layout.heap_words)
                     + bytes([layout.stack_words]))
    data += bytes(_ref(snapshot[name], layout) for name in HEADS)
    data.append(_number(snapshot["vm_lock_state"], 255, "vm_lock_state"))
    # Variables stay at a fixed offset for SAVE_PEEK; all stack words follow.
    for word in memory:
        data += struct.pack("<H", _number(word, 65535, "script memory word"))
    for index, ctx in enumerate(contexts):
        if set(ctx) != set(FIELDS):
            raise ValueError("unclassified context field")
        if ctx["ID"] != index + 1 or ctx["base_addr"] != layout.base(index):
            raise ValueError("context identity or base differs from script_runner_init")
        for name in ("PC", "update_fn"):
            _number(ctx[name], 0xFFFFFF, name)
        sp = ctx["stack_ptr"]
        if sp == 0:
            offset = 0xFFFF
        elif (type(sp) is int and sp % 2 == 0 and layout.base(index) <= sp
              <= layout.base(index) + 2 * layout.stack_words):
            offset = (sp - layout.memory_address) // 2
        else:
            raise ValueError("context stack pointer outside its allocated stack")
        handle = _number(ctx["hthread"], 0xFFFF, "hthread")
        if handle and (handle % 2 or not 0x4000 <= handle <= 0x6BFE):
            raise ValueError("hthread outside target work RAM")
        data += ctx["PC"].to_bytes(3, "little")
        data += bytes([_number(ctx["bank"], 255, "bank"), _ref(ctx["next"], layout)])
        data += ctx["update_fn"].to_bytes(3, "little")
        data.append(_number(ctx["update_fn_bank"], 255, "update_fn_bank"))
        data += struct.pack("<HH", offset, handle)
        data += bytes(_number(ctx[name], 255, name) for name in ("terminated", "waitable", "lock_count", "flags"))
    assert len(data) == layout.size
    return bytes(data)


def decode(layout, data):
    """Validate a complete blob before returning any restorable state."""
    if len(data) != layout.size or data[:3] != MAGIC:
        raise ValueError("invalid VM snapshot length or version")
    if data[3] != layout.contexts or int.from_bytes(data[4:6], "little") != layout.heap_words or data[6] != layout.stack_words:
        raise ValueError("VM snapshot dimensions differ from the build")

    def ref(value):
        if value > layout.contexts:
            raise ValueError("context reference out of range")
        return None if value == 0 else value - 1

    state = {name: ref(data[7 + k]) for k, name in enumerate(HEADS)}
    state["vm_lock_state"] = data[11]
    contexts, pos = [], HEADER_BYTES + 2 * layout.words
    for index in range(layout.contexts):
        record = data[pos:pos + CONTEXT_BYTES]
        offset = int.from_bytes(record[9:11], "little")
        ctx = {"PC": int.from_bytes(record[:3], "little"), "bank": record[3], "next": ref(record[4]),
               "update_fn": int.from_bytes(record[5:8], "little"), "update_fn_bank": record[8],
               "stack_ptr": 0 if offset == 0xFFFF else layout.memory_address + 2 * offset,
               "base_addr": layout.base(index), "ID": index + 1,
               "hthread": int.from_bytes(record[11:13], "little"), "terminated": record[13],
               "waitable": record[14], "lock_count": record[15], "flags": record[16]}
        contexts.append(ctx)
        pos += CONTEXT_BYTES
    state["contexts"] = contexts
    state["memory"] = list(struct.unpack("<" + "H" * layout.words,
                                        data[HEADER_BYTES:HEADER_BYTES + 2 * layout.words]))
    # The same range/invariant checks apply to inputs and restored data.
    encode(layout, state)
    return state


def budget(report, manifest):
    """Target-measured raw state with the seven VM save points replaced by NV1.

    Other selected fields retain their measured size. This does not activate
    save events or generate the remaining restore descriptors.
    """
    from ngpcraft.save_state import check_capacity
    vm = manifest["gbvm"]
    count, stride = vm["stack_budget"]["contexts"], vm["stack_budget"]["words_per_context"]
    heap = vm["heap_words"]
    if not (1 <= count <= 255 and 1 <= stride <= 64 and 0 <= heap <= 32768):
        raise ValueError("invalid compiled VM dimensions")
    names = {"CTXS", "script_memory", "vm_lock_state", *HEADS}
    points = [v for v in report["variables"] if v["name"] in names and v["decision"] == "save"]
    if len(points) != len(names) or {v["name"] for v in points} != names:
        raise ValueError("VM save points missing or duplicated in target inventory")
    sizes = {v["name"]: v["target_bytes"] for v in points}
    if sizes["script_memory"] != 2 * (heap + count * stride) or sizes["vm_lock_state"] != 1:
        raise ValueError("target memory dimensions differ from the VM manifest")
    if any(sizes[name] != 4 for name in HEADS) or sizes["CTXS"] % count:
        raise ValueError("unsupported target context ABI")
    packed = HEADER_BYTES + CONTEXT_BYTES * count + 2 * (heap + count * stride)
    total = report["raw_state_max_bytes"] - sum(sizes.values()) + packed
    pages = check_capacity(total, "NV1 save state")
    return {"format": "NV1", "contexts": count, "heap_words": heap, "stack_words": stride,
            "vm_bytes": packed, "other_state_bytes": total - packed, "payload_max_bytes": total,
            "pages_per_slot": pages, "pages_with_recovery": 3 * pages + 1,
            "payload_margin_bytes": 2537 - total, "all_allocated_stack_words_preserved": True}
