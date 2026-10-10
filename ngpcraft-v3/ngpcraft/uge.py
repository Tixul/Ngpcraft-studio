"""Strict UGE v6 intake, compared with released GB Studio 4.3.2.

Layout/normalization: src/shared/lib/uge/ugeHelper.ts loadUGESong and
compactUGESong, MIT, Chris Maltby (see GBSTUDIO_UGE_LICENSE.txt).
No sound conversion or playback is performed. Malformed values which the
reference would clamp, overwrite or decode with replacement are refused.
"""
import hashlib
from pathlib import Path

from .audio_assets import MusicError

EFFECTS = (
    "arpeggio", "porta_up", "porta_down", "tone_portamento", "vibrato",
    "master_volume", "call_routine", "note_delay", "pan", "duty",
    "volume_slide", "position_jump", "volume", "pattern_break", "note_cut", "speed",
)
KINDS = ("duty", "duty", "wave", "noise")
GROUPS = {"duty": "dutyInstruments", "wave": "waveInstruments", "noise": "noiseInstruments"}


class _Reader:
    def __init__(self, data, where):
        self.data, self.where, self.offset = data, where, 0
        self.reserved = []

    def fail(self, field, message):
        raise MusicError(f"{self.where}.{field} (byte {self.offset}): {message}")

    def take(self, count, field):
        if count > len(self.data) - self.offset:
            self.fail(field, f"truncated: need {count} bytes, have {len(self.data) - self.offset}")
        out = self.data[self.offset:self.offset + count]
        self.offset += count
        return out

    def number(self, field, width=4, high=0xFFFFFFFF):
        start = self.offset
        value = int.from_bytes(self.take(width, field), "little")
        if value > high:
            raise MusicError(f"{self.where}.{field} (byte {start}): expected 0..{high}, got {value}")
        return value

    def text(self, field):
        start = self.offset
        raw = self.take(256, field)
        try:
            return raw[1:1 + raw[0]].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MusicError(f"{self.where}.{field} (byte {start}): invalid UTF-8") from exc

    def unused(self, field):
        start = self.offset
        value = self.number(field)
        if value:
            self.reserved.append({"field": field, "byte": start, "value": value})


def _cell(reader, loc, subpattern=False):
    note = reader.number(loc + ".note")
    if not subpattern and note != 90 and note > 71:
        reader.fail(loc + ".note", f"expected note 0..71 or empty 90, got {note}")
    if subpattern:
        reader.unused(loc + ".unused")
        second = reader.number(loc + ".jump", high=64)
    else:
        second = reader.number(loc + ".instrument", high=15)
        reader.unused(loc + ".unused")
    effect = reader.number(loc + ".effectCode", high=15)
    param = reader.number(loc + ".effectParam", width=1)
    empty = effect == 0 and param == 0
    return {"note": None if note == 90 else note,
            ("jump" if subpattern else "instrument"): second if subpattern else (second - 1 if second else None),
            "effectCode": None if empty else effect, "effectParam": None if empty else param}


def _instrument(reader, index):
    loc = f"instruments[{index}]"
    kind = reader.number(loc + ".type", high=2)
    name = reader.text(loc + ".name")
    length = reader.number(loc + ".length", high=256 if kind == 1 else 64)
    length_enabled = reader.number(loc + ".lengthEnabled", width=1, high=1)
    volume = reader.number(loc + ".initialVolume", width=1, high=15)
    volume_direction = reader.number(loc + ".volumeDirection", high=1)
    amount = reader.number(loc + ".volumeSweepAmount", width=1, high=7)
    sweep_time = reader.number(loc + ".freqSweepTime", high=7)
    sweep_direction = reader.number(loc + ".freqSweepDirection", high=1)
    sweep_shift = reader.number(loc + ".freqSweepShift", high=7)
    duty = reader.number(loc + ".duty", width=1, high=3)
    wave_volume = reader.number(loc + ".waveOutputLevel", high=3)
    wave_index = reader.number(loc + ".waveWaveformIndex", high=15)
    noise_step = reader.number(loc + ".noiseCounterStep", high=1)
    enabled = reader.number(loc + ".subpatternEnabled", width=1, high=1)
    subpattern = [_cell(reader, f"{loc}.subpattern[{row}]", True) for row in range(64)]
    item = {"name": name, "length": ((256 if kind == 1 else 64) - length) if length_enabled else None,
            "subpatternEnabled": bool(enabled), "subpattern": subpattern}
    if kind == 1:
        item.update(volume=wave_volume, waveIndex=wave_index)
    else:
        item.update(initialVolume=volume, volumeSweepChange=(8 - amount) * (-1 if volume_direction else 1) if amount else 0)
        if kind == 0:
            item.update(dutyCycle=duty, frequencySweepTime=sweep_time,
                        frequencySweepShift=-sweep_shift if sweep_direction else sweep_shift)
        else:
            item["bitCount"] = 7 if noise_step else 15
    return ("dutyInstruments", "waveInstruments", "noiseInstruments")[kind], item


def _parse(data, where):
    if not isinstance(data, bytes):
        raise MusicError(f"{where}: expected binary bytes")
    reader = _Reader(data, where)
    version = reader.number("version")
    if version != 6:
        reader.fail("version", f"UGE version {version} is not supported; this intake supports version 6 only")
    song = {"version": 6, "name": reader.text("name"), "artist": reader.text("artist"),
            "comment": reader.text("comment"), "filename": "song",
            "dutyInstruments": [], "waveInstruments": [], "noiseInstruments": []}
    for index in range(45):
        group, item = _instrument(reader, index)
        item["index"] = len(song[group])
        song[group].append(item)
    song["waves"] = [list(reader.take(32, f"waves[{n}]")) for n in range(16)]
    for n, wave in enumerate(song["waves"]):
        if any(v > 15 for v in wave):
            reader.fail(f"waves[{n}]", "expected 32 four-bit samples (0..15)")
    song["ticksPerRow"] = reader.number("ticksPerRow", high=255)
    song["timerEnabled"] = bool(reader.number("timerEnabled", width=1, high=1))
    song["timerDivider"] = reader.number("timerDivider", high=255)
    count = reader.number("patternCount")
    # Bound work by bytes actually present, before iterating untrusted counts.
    if count > (len(data) - reader.offset) // (4 + 64 * 17):
        reader.fail("patternCount", f"{count} patterns exceed the remaining file size")
    patterns = {}
    for n in range(count):
        pattern_id = reader.number(f"patterns[{n}].id")
        if pattern_id in patterns:
            reader.fail(f"patterns[{n}].id", f"duplicate pattern id {pattern_id}; overwriting is forbidden")
        patterns[pattern_id] = [_cell(reader, f"patterns[{pattern_id}].rows[{row}]") for row in range(64)]
    orders = []
    for channel in range(4):
        loc = f"orders[{channel}]"
        stored_count = reader.number(loc + ".storedCount")
        if not stored_count:
            reader.fail(loc, "stored count includes a sentinel and must be at least 1")
        if stored_count > (len(data) - reader.offset) // 4:
            reader.fail(loc, "order count exceeds the remaining file size")
        orders.append([reader.number(f"{loc}[{n}]") for n in range(stored_count - 1)])
        sentinel = reader.number(loc + ".sentinel")
        if sentinel:
            reader.fail(loc + ".sentinel", f"expected unused zero sentinel, got {sentinel}")
    if len({len(order) for order in orders}) != 1:
        reader.fail("orders", "the four channels must have the same sequence length")
    # GB Studio's saver writes sixteen empty routine fields. Its loader and C
    # exporter do not implement custom routine bodies. Refuse nonempty fields
    # rather than inventing a body format from their ignored trailer.
    routines = []
    for n in range(16):
        loc = f"routines[{n}]"
        size = reader.number(loc + ".size")
        if size:
            reader.fail(loc, "nonempty custom routines are not supported")
        routines.append("")
    if reader.offset != len(data):
        reader.fail("trailer", f"{len(data) - reader.offset} unknown trailing bytes")
    used_ids = {pid for order in orders for pid in order}
    missing = used_ids - patterns.keys()
    if missing:
        reader.fail("orders", f"references undefined patterns {sorted(missing)}; blank repair is forbidden")
    # compactUGESong retains entire blocks of four, including unused neighbours.
    # Existing sparse blocks contain explicit blank neighbours in the reference.
    compact, mapping, implicit = [], {}, []
    for block in sorted({pid // 4 for pid in used_ids}):
        for pid in range(block * 4, min(block * 4 + 4, max(patterns) + 1)):
            mapping[pid] = len(compact)
            if pid not in patterns:
                implicit.append(pid)
            compact.append(patterns[pid] if pid in patterns else [
                {"note": None, "instrument": None, "effectCode": None, "effectParam": None} for _ in range(64)])
    song["patterns"] = compact
    sequence = []
    for index in range(len(orders[0])):
        original = [order[index] for order in orders]
        a, b, c, d = original
        sequence.append({"splitPattern": a % 4 != 0 or b != a + 1 or c != a + 2 or d != a + 3,
                         "channels": [mapping[pid] for pid in original]})
    song["sequence"] = sequence
    return song, {"stored_patterns": count, "pattern_id_map": mapping, "implicit_blank_neighbours": implicit,
                  "routines_hex": routines, "nonzero_reserved_fields": reader.reserved}


def parse_uge(data, where="uge"):
    """Return the normalized Song shape of loadUGESong/compactUGESong.

    Values remain in GB Studio units: notes 0..71, local instruments 0..14,
    ticksPerRow in source driver ticks. They are not NGPC notes or frames.
    """
    return _parse(data, where)[0]


def read_uge(path):
    path = Path(path)
    raw = path.read_bytes()
    return parse_uge(raw, str(path)), hashlib.sha256(raw).hexdigest()


def inspect_uge(path, include_song=False):
    """Inventory ordered source cells and dependencies; no playback claims."""
    path = Path(path)
    raw = path.read_bytes()
    song, storage = _parse(raw, str(path))
    effects, references, attacks = {}, {}, [0] * 4
    source_ids = {value: key for key, value in storage["pattern_id_map"].items()}
    for oi, item in enumerate(song["sequence"]):
        for channel, pid in enumerate(item["channels"]):
            kind = KINDS[channel]
            for row, cell in enumerate(song["patterns"][pid]):
                loc = f"sequence[{oi}].channel[{channel}].pattern[{source_ids[pid]}].row[{row}]"
                if cell["note"] is not None:
                    attacks[channel] += 1
                if cell["instrument"] is not None:
                    index = cell["instrument"]
                    if index >= len(song[GROUPS[kind]]):
                        raise MusicError(f"{path}.{loc}: undefined {kind} instrument {index}")
                    references.setdefault((kind, index), []).append(loc)
                if cell["effectCode"] is not None:
                    code = cell["effectCode"]
                    effects.setdefault(f"{code:X}", {"name": EFFECTS[code], "uses": []})["uses"].append(
                        {"location": loc, "param": cell["effectParam"]})
    instruments, adaptations, subpattern_effects = [], [], {}
    for (kind, index), locations in sorted(references.items()):
        item = song[GROUPS[kind]][index]
        instruments.append({"kind": kind, "index": index, "definition": item, "locations": locations})
        needs = []
        if kind == "wave":
            needs.append("wave_to_square: preserve the source waveform in the report; a square approximation needs review")
        if kind == "duty" and item["dutyCycle"] != 2:
            needs.append("duty_cycle: T6W28 square duty is fixed at 50 percent")
        if item["length"] is not None:
            needs.append("length_counter: source hardware duration requires conversion")
        if kind != "wave" and item["volumeSweepChange"]:
            needs.append("volume_envelope: source hardware envelope requires conversion")
        if kind == "duty" and (item["frequencySweepTime"] or item["frequencySweepShift"]):
            needs.append("frequency_sweep: source hardware sweep requires conversion")
        if kind == "noise":
            needs.append("noise: source polynomial/period mapping requires an explicit approximation")
        if item["subpatternEnabled"]:
            needs.append("subpattern: source per-tick instrument program requires conversion")
            for row, cell in enumerate(item["subpattern"]):
                code = cell["effectCode"]
                if code is not None:
                    subpattern_effects.setdefault(f"{code:X}", {"name": EFFECTS[code], "uses": []})["uses"].append(
                        {"location": f"{GROUPS[kind]}[{index}].subpattern[{row}]", "param": cell["effectParam"]})
        if needs:
            adaptations.append({"kind": kind, "index": index, "requirements": needs})
    wave_ids = sorted({i["definition"]["waveIndex"] for i in instruments if i["kind"] == "wave"})
    result = {"format": "ngpcraft-uge-inspection/1", "source": str(path),
              "sha256": hashlib.sha256(raw).hexdigest(), "uge_version": 6,
              "metadata": {n: song[n] for n in ("name", "artist", "comment")},
              "validation": "source-intake-only", "runtime_ready": False,
              "ticks_per_row": song["ticksPerRow"],
              "source_timer": {"enabled": song["timerEnabled"], "divider": song["timerDivider"]},
              "sequence": song["sequence"], "sequence_rows": len(song["sequence"]) * 64,
              "note_attacks_by_channel": attacks, "effects": effects,
              "subpattern_effects": subpattern_effects,
              "instruments": instruments, "instrument_adaptations": adaptations,
              "waves": [{"index": index, "samples": song["waves"][index]} for index in wave_ids],
              "storage": storage,
              "limitations": ["UGE v6 only; earlier versions are explicitly refused.",
                               "Nonempty custom routine fields and malformed source repairs are refused.",
                               "Sequence rows are not elapsed playback time: Bxx/Dxx can change control flow.",
                               "Source notes, effects, timer and instruments are inventoried, not converted.",
                               "No .ngps, driver stream, sound event or ROM is emitted."]}
    if include_song:
        result["song"] = song
    return result
