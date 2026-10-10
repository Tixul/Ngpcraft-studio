"""Host oracle of the V3 save journal (docs/M5_SAVE.md, step 1).

Two 8 KiB flash blocks used as a journal (Over Rev v31,
04_MY_PROJECTS/Racer_test-project/Over_rev/src/core/ngpc_flash.c),
generalised to records of several 256-byte pages and to GB Studio's three
save slots (D5_SAUVEGARDE.md section 6). The C runtime (step 2) must give
the same flash bytes as this model for the same operations.

The chip model: programming only clears bits (new = old & data), byte by
byte in address order; erasing sets a block to 0xFF. A power cut is an
exception raised after a given number of programmed bytes, or in the middle
of an erase (the first bytes of the block erased, the rest unchanged).
"""
import binascii
from dataclasses import dataclass

PAGE = 256
PAGES = 32
BLOCK = PAGE * PAGES
HEADER = 22
MAGIC = b"NGS1"
STATE, CLEARED = 1, 2
SLOTS = 3


class PowerCut(Exception):
    """The supply dropped: the operation stopped where it was."""


class Chip:
    """Two erase blocks of the cartridge flash, with an optional power cut."""

    def __init__(self):
        self.blocks = [bytearray(b"\xff" * BLOCK) for _ in range(2)]
        self.budget = None          # programmed bytes left before a power cut
        self.erase_cut = None       # bytes erased before a power cut, for the next erase
        self.programmed = 0
        self.erases = 0

    def copy(self):
        other = Chip()
        other.blocks = [bytearray(b) for b in self.blocks]
        other.programmed, other.erases = self.programmed, self.erases
        return other

    def program(self, block, offset, data):
        for i, value in enumerate(data):
            if self.budget is not None:
                if self.budget == 0:
                    raise PowerCut()
                self.budget -= 1
            self.blocks[block][offset + i] &= value
            self.programmed += 1

    def erase(self, block):
        self.erases += 1
        if self.erase_cut is not None:
            cut, self.erase_cut = self.erase_cut, None
            self.blocks[block][:cut] = b"\xff" * cut
            raise PowerCut()
        self.blocks[block][:] = b"\xff" * BLOCK


def crc16_reference(data, crc=0xFFFF):
    """CRC16-CCITT, initial 0xFFFF, bit by bit as ngpc_flash.c crc_byte."""
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc16(data, crc=0xFFFF):
    """The same CRC (polynomial 0x1021, MSB first), from the standard library."""
    return binascii.crc_hqx(bytes(data), crc)


def newer(a, b):
    """Sequence a after b, modulo 2^32 (ngpc_flash.c ngpc_flash_init)."""
    delta = (a - b) & 0xFFFFFFFF
    return delta != 0 and delta < 0x80000000


def pages_for(length):
    return (HEADER + length + 1 + PAGE - 1) // PAGE


def encode(kind, slot, sequence, signature, data):
    """One record: header, data, 0xFF padding, end byte 0 (the last one)."""
    pages = pages_for(len(data))
    if not 1 <= pages <= PAGES:
        raise ValueError(f"record of {len(data)} bytes does not fit in a block")
    out = bytearray(b"\xff" * (pages * PAGE))
    out[0:4] = MAGIC
    out[4], out[5], out[6], out[7] = kind, slot, pages, 0
    out[8:12] = (sequence & 0xFFFFFFFF).to_bytes(4, "little")
    out[12:16] = (signature & 0xFFFFFFFF).to_bytes(4, "little")
    out[16:18] = len(data).to_bytes(2, "little")
    out[HEADER:HEADER + len(data)] = data
    out[-1] = 0
    crc = crc16(out[HEADER:-1], crc16(out[0:18]))
    out[18:20] = crc.to_bytes(2, "little")
    out[20], out[21] = out[18] ^ 0xFF, out[19] ^ 0xFF
    return bytes(out)


@dataclass(frozen=True)
class Record:
    block: int
    page: int
    kind: int
    slot: int
    pages: int
    sequence: int
    signature: int
    data: bytes

    def raw(self, chip):
        start = self.page * PAGE
        return bytes(chip.blocks[self.block][start:start + self.pages * PAGE])


def _plausible(head):
    return head[0:4] == MAGIC and 1 <= head[6] <= PAGES


def _decode(block, page, raw):
    kind, slot, pages = raw[4], raw[5], raw[6]
    length = int.from_bytes(raw[16:18], "little")
    if kind not in (STATE, CLEARED) or slot >= SLOTS or raw[7] != 0 or raw[-1] != 0:
        return None
    if pages_for(length) != pages or (kind == CLEARED and length):
        return None
    if raw[20] != raw[18] ^ 0xFF or raw[21] != raw[19] ^ 0xFF:
        return None
    if crc16(raw[HEADER:-1], crc16(raw[0:18])) != int.from_bytes(raw[18:20], "little"):
        return None
    return Record(block, page, kind, slot, pages, int.from_bytes(raw[8:12], "little"),
                  int.from_bytes(raw[12:16], "little"), bytes(raw[HEADER:HEADER + length]))


def scan(chip):
    """Valid records of both blocks, and the first free page of each (after
    the last page that is not entirely 0xFF)."""
    records, free = [], []
    for block in range(2):
        data = chip.blocks[block]
        page, end = 0, 0
        while page < PAGES:
            at = page * PAGE
            if data[at:at + PAGE] == b"\xff" * PAGE:
                page += 1
                continue
            head = data[at:at + HEADER]
            step = head[6] if _plausible(head) and page + head[6] <= PAGES else 1
            if step > 1 or _plausible(head):
                record = _decode(block, page, data[at:at + step * PAGE])
                if record is not None:
                    records.append(record)
            page += step
            end = page
        free.append(end)
    return records, free


def latest(records):
    """The newest record of each slot, and the newest record overall."""
    best, top = {}, None
    for r in records:
        if r.slot not in best or newer(r.sequence, best[r.slot].sequence):
            best[r.slot] = r
        if top is None or newer(r.sequence, top.sequence):
            top = r
    return best, top


class Journal:
    def __init__(self, chip, signature):
        self.chip = chip
        self.signature = signature & 0xFFFFFFFF

    def load(self, slot):
        """data_load / If Data Saved: the slot's newest state, None if absent,
        cleared or signed by another build."""
        best, _ = latest(scan(self.chip)[0])
        r = best.get(slot)
        if r is None or r.kind != STATE or r.signature != self.signature:
            return None
        return r.data

    def save(self, slot, data):
        return self._write(slot, STATE, data)

    def clear(self, slot):
        return self._write(slot, CLEARED, b"")

    # -- writing ---------------------------------------------------------------
    def _program(self, block, page, raw):
        self.chip.program(block, page * PAGE, raw)
        start = page * PAGE
        return bytes(self.chip.blocks[block][start:start + len(raw)]) == raw

    def _append(self, block, free, raw):
        pages = len(raw) // PAGE
        if free + pages > PAGES:
            return None
        if self.chip.blocks[block][free * PAGE:(free + pages) * PAGE] != b"\xff" * (pages * PAGE):
            return None
        return free + pages if self._program(block, free, raw) else None

    def _write(self, slot, kind, data):
        records, free = scan(self.chip)
        best, top = latest(records)
        current = top.block if top else 0
        sequence = top.sequence if top else 0
        pages = pages_for(len(data))
        # 1. Append to the current block. A newest record of another slot
        # still in the other block (power cut during a roll) is copied first,
        # so that the other block never holds the only copy of anything.
        moved = [r for r in sorted(best.values(), key=lambda r: r.slot) if r.block != current and r.slot != slot]
        if free[current] + sum(r.pages for r in moved) + pages <= PAGES:
            at = free[current]
            for r in moved:
                sequence += 1
                at = self._append(current, at, encode(r.kind, r.slot, sequence, r.signature, r.data))
                if at is None:
                    return False
            return self._append(current, at, encode(kind, slot, sequence + 1, self.signature, data)) is not None
        # 2. Roll: erase a block holding no unique newest record (the other
        # one, or the current one when it only holds the copies of a roll
        # cut short), copy the other slots' newest records into it from the
        # block kept, then write the new record.
        for target in (1 - current, current):
            if self._erasable(target, records, best):
                break
        else:
            return False
        source = 1 - target
        kept = latest([r for r in records if r.block == source])[0]
        if self.chip.blocks[target] != b"\xff" * BLOCK:
            self.chip.erase(target)
            if self.chip.blocks[target] != b"\xff" * BLOCK:
                return False
        at = 0
        for s in sorted(kept):
            if s == slot:
                continue
            r = kept[s]
            sequence += 1
            at = self._append(target, at, encode(r.kind, r.slot, sequence, r.signature, r.data))
            if at is None:
                return False
        return self._append(target, at, encode(kind, slot, sequence + 1, self.signature, data)) is not None

    @staticmethod
    def _erasable(block, records, best):
        """No slot's newest record is only in this block: each one has an
        identical newest record in the other block."""
        other = latest([r for r in records if r.block != block])[0]
        for s, r in best.items():
            if r.block != block:
                continue
            o = other.get(s)
            if o is None or (o.kind, o.signature, o.data) != (r.kind, r.signature, r.data):
                return False
        return True
