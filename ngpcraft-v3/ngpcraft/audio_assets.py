"""Strict Sound Creator .ngps v1 intake, without synthesis or runtime claims.

Format authority: SongDocument::to_json and TrackerDocument::to_json in
NGPCraft-Ngpc-sound-creator. Structural note spans follow NoteSpans.cpp;
they describe tracker keys, NOT sounding duration (ADSR/effects can alter it).
Source algorithm: MIT, Copyright (c) 2026 Willy (Tixul).
See SOUND_CREATOR_LICENSE.txt. No template driver or user instrument bank
is embedded here.
"""
import hashlib
import json
from pathlib import Path

from .compiler import CompileError


class MusicError(CompileError):
    pass


def _fields(value, required, optional, where):
    if not isinstance(value, dict):
        raise MusicError(f"{where}: expected an object")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing or extra:
        raise MusicError(f"{where}: missing fields {sorted(missing)}; unknown fields {sorted(extra)}")


def _integer(value, low, high, where):
    # JSON integers may be written 1.0; never accept booleans or truncate.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MusicError(f"{where}: expected integer {low}..{high}")
    if not low <= value <= high or int(value) != value:
        raise MusicError(f"{where}: expected integer {low}..{high}, got {value}")
    return int(value)


def _array(value, low, high, where):
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise MusicError(f"{where}: expected array of {low}..{high} elements")


def validate_song(song, where="song"):
    """Return a normalized copy; reject Sound Creator's silent repairs.

    Counts and ranges are file-format limits of its document classes, not
    proposed NGPC hardware budgets. Unknown effect bytes remain intact:
    intake inventories them and does not claim to execute them.
    """
    _fields(song, {"version", "patterns", "order", "loop_point"}, set(), where)
    _integer(song["version"], 1, 1, f"{where}.version")
    _array(song["patterns"], 1, 64, f"{where}.patterns")
    patterns = []
    for pi, pattern in enumerate(song["patterns"]):
        loc = f"{where}.patterns[{pi}]"
        _fields(pattern, {"length", "channels"}, set(), loc)
        length = _integer(pattern["length"], 16, 128, f"{loc}.length")
        _array(pattern["channels"], 4, 4, f"{loc}.channels")
        channels = []
        for ci, channel in enumerate(pattern["channels"]):
            cloc = f"{loc}.channels[{ci}]"
            _array(channel, length, length, cloc)
            rows = []
            for ri, cell in enumerate(channel):
                cell_loc = f"{cloc}[{ri}]"
                if cell is None:
                    rows.append(None)
                    continue
                _fields(cell, {"n", "i", "a"}, {"f", "p"}, cell_loc)
                note = _integer(cell["n"], 0, 255, f"{cell_loc}.n")
                if 128 <= note <= 254:
                    raise MusicError(f"{cell_loc}.n: expected 0, tracker note 1..127, or note-off 255")
                inst = _integer(cell["i"], 0, 127, f"{cell_loc}.i")
                attn = _integer(cell["a"], 0, 255, f"{cell_loc}.a")
                if 16 <= attn <= 254:
                    raise MusicError(f"{cell_loc}.a: expected attenuation 0..15 or instrument default 255")
                if ("f" in cell) != ("p" in cell):
                    raise MusicError(f"{cell_loc}: effect fields f and p must occur together")
                normalized = {"n": note, "i": inst, "a": attn}
                if "f" in cell:
                    normalized["f"] = _integer(cell["f"], 0, 255, f"{cell_loc}.f")
                    normalized["p"] = _integer(cell["p"], 0, 255, f"{cell_loc}.p")
                rows.append(normalized)
            channels.append(rows)
        patterns.append({"length": length, "channels": channels})
    _array(song["order"], 1, 256, f"{where}.order")
    order = [_integer(value, 0, len(patterns) - 1, f"{where}.order[{i}]")
             for i, value in enumerate(song["order"])]
    loop = _integer(song["loop_point"], 0, len(order) - 1, f"{where}.loop_point")
    return {"version": 1, "patterns": patterns, "order": order, "loop_point": loop}


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise MusicError(f"duplicate JSON key {key!r}")
        obj[key] = value
    return obj


def _finite_json(value):
    raise MusicError(f"non-finite JSON number {value}")


def read_song(path):
    path = Path(path)
    raw = path.read_bytes()
    try:
        song = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=_finite_json)
        return validate_song(song, str(path)), hashlib.sha256(raw).hexdigest()
    except (ValueError, UnicodeError) as exc:
        raise MusicError(f"{path}: {exc}") from exc


def note_spans(channel):
    """NoteSpans::from_channel semantics on a validated single pattern.

    A null cell and n=0 do not end a key. a=255 means instrument default,
    not note-off. Pattern boundaries end these EDITOR spans only. Effects,
    release tails, inter-pattern continuity and loop playback need a sequencer.
    """
    spans, opened = [], None
    for row, cell in enumerate(channel):
        if cell is None or cell["n"] == 0:
            continue
        if opened is not None:
            opened["length_rows"] = row - opened["start_row"]
            opened["ends_with_off"] = cell["n"] == 255
        if cell["n"] == 255:
            opened = None
            continue
        slide = cell.get("f") == 3 and opened is not None
        opened = {"start_row": row, "length_rows": len(channel) - row,
                  "note": cell["n"], "instrument": cell["i"], "attenuation": cell["a"],
                  "slide": slide, "has_fx": bool(cell.get("f")), "ends_with_off": False}
        spans.append(opened)
    return spans


def inspect_song(path, include_spans=False):
    song, digest = read_song(path)
    used = set(song["order"])
    instruments, effects, report = set(), {}, []
    for pi, pattern in enumerate(song["patterns"]):
        channels = []
        for ci, channel in enumerate(pattern["channels"]):
            spans = note_spans(channel)
            channels.append({"channel": ci, "kind": "noise" if ci == 3 else "tone",
                             "note_attacks": len(spans),
                             "held_key_rows": sum(s["length_rows"] for s in spans),
                             **({"editor_spans": spans} if include_spans else {})})
            if pi not in used:
                continue
            instruments.update(s["instrument"] for s in spans)
            for ri, cell in enumerate(channel):
                if cell is not None and (cell.get("f") or cell.get("p")):
                    code = f"0x{cell['f']:02X}"
                    if code not in effects:
                        effects[code] = []
                    effects[code].append({"pattern": pi, "channel": ci, "row": ri, "param": cell["p"]})
        report.append({"pattern": pi, "used": pi in used, "length_rows": pattern["length"],
                       "channels": channels})
    rows = [song["patterns"][pi]["length"] for pi in song["order"]]
    return {"format": "ngpcraft-ngps-inspection/1", "source": str(path), "sha256": digest,
            "ngps_version": 1, "validation": "structural-only", "runtime_ready": False,
            "patterns": report, "order": song["order"], "total_rows": sum(rows),
            "loop_order_index": song["loop_point"], "loop_start_row": sum(rows[:song["loop_point"]]),
            "used_patterns": sorted(used), "unused_patterns": sorted(set(range(len(report))) - used),
            "required_instruments": sorted(instruments), "effects": dict(sorted(effects.items())),
            "required_external_data": ["instrument_bank", "tick_clock"],
            "limitations": ["Instruments and a separate initial tempo field are absent; Bxx can encode tempo. Preview defaults to 8 ticks per row.",
                            "Editor key spans are not synthesized audio durations.",
                            "Effects are inventoried without interpretation; no effect is silently discarded.",
                            "No music event, PSG stream or ROM is generated by this command."]}
