"""Game Boy APU register log -> native NGPC held score (the bridge format of music_hold).

Input: a JSON log of every APU register write (0xFF10..0xFF3F) in order, grouped by frame, and
each channel's envelope volume at the end of the frame, as recorded from a Game Boy ROM running
in an emulator (a development oracle: nothing of the emulator enters the export). One GB frame
is one NGPC vertical blank (59.73 Hz vs 59.95 Hz: 0.4 % faster, not resampled).

Same mapping rules as the UGE conversion (ngpcraft/uge_convert.py): nearest equal-tempered key
(PSG divider), relative amplitude to 2 dB attenuation steps, fixed noise preset, square wave,
NR51 routing kept as a silent side (pan, ngpcraft/music_pan.py). Better than a static instrument: the volume is the envelope's at each frame (attenuation
updates on held notes), and the song's loop is found exactly in the log (two equal cycles).

  python -B tools/apu_log_music.py LOG.json OUT_DIR [--first F] [--min-loop N] [--noise 5]

OUT_DIR (new): held-score.json, bank.json, report.json, native/ (generate_music), as the editor's
native_music binding expects ("held-score" format)."""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.audio_assets import MusicError  # noqa: E402
from ngpcraft.audio_resolve import FIELDS  # noqa: E402
from ngpcraft.music_hold import compile_score  # noqa: E402
from ngpcraft.music_streams import generate_music  # noqa: E402
from ngpcraft.uge_convert import tone_note, volume_attenuation  # noqa: E402

TRIGGERS = (0xFF14, 0xFF19, 0xFF1E, 0xFF23)
DACS = (0xFF12, 0xFF17, 0xFF1A, 0xFF21)


def period(regs, ch):
    lo, hi = ((0xFF13, 0xFF14), (0xFF18, 0xFF19), (0xFF1D, 0xFF1E))[ch]
    return regs[lo] | (regs[hi] & 7) << 8


def frame_rows(log, noise_setting, transpose):
    """Per frame, per channel: None (silent) or (note, divider, attenuation, attack, pan)."""
    regs = {a: 0 for a in range(0xFF10, 0xFF40)}
    # registers set before the log started: its snapshot (readable bits), else the usual setup
    # (all channels routed, full master volume, wave DAC on)
    regs.update({0xFF24: 0x77, 0xFF25: 0xFF, 0xFF1A: 0x80})
    regs.update({int(k, 16): v for k, v in log.get("initial", {}).items()
                 if int(k, 16) in (0xFF24, 0xFF25, 0xFF1A, 0xFF12, 0xFF17, 0xFF1C, 0xFF21)})
    started = [False] * 4
    wave_prev = None
    rows = []
    first, last = log["from"], log["to"]
    for f in range(first, last):
        attacks = [False] * 4
        for addr, value in log["writes"].get(str(f), []):
            regs[addr] = value
            for ch, high in enumerate(TRIGGERS):
                if addr == high and value & 0x80:
                    started[ch] = attacks[ch] = True
        vols = log["volumes"].get(str(f), [0, 0, 0, 0])
        # A wave channel started before the log (no trigger inside it) and played by rewriting its frequency
        # and NR32 (Mega Man Xtreme 2's driver): playing = the emulator's channel volume; a note starts when
        # the frequency changes or NR32 leaves mute.
        if vols[2] and not started[2]:
            started[2] = attacks[2] = True
        wave_key = (period(regs, 2), regs[0xFF1C] & 0x60)
        if wave_prev is not None and wave_key != wave_prev and wave_key[1] and (
                wave_key[0] != wave_prev[0] or not wave_prev[1]):
            attacks[2] = True
        wave_prev = wave_key
        row = []
        for ch in range(4):
            routed = regs[0xFF25] & ((1 << ch) | (16 << ch))
            dac = regs[0xFF1A] & 0x80 if ch == 2 else regs[DACS[ch]] & 0xF8
            if not (started[ch] and routed and dac):
                row.append(None)
                continue
            att = volume_attenuation(2, regs[0xFF1C]) if ch == 2 else volume_attenuation(ch, vols[ch] << 4)
            if att is None:
                row.append(None)
                continue
            pan = (0 if regs[0xFF25] & (16 << ch) else 15, 0 if regs[0xFF25] & (1 << ch) else 15)
            if ch == 3:
                row.append((noise_setting, None, att, attacks[ch], pan))
                continue
            note, divider, _ = tone_note(period(regs, ch), ch, transpose[ch])
            row.append((note, divider, att, attacks[ch], pan))
        rows.append(row)
    return rows


def find_loop(rows, min_loop):
    """Earliest start s and shortest length L >= min_loop with rows[s:s+L] == rows[s+L:s+2L]."""
    import numpy as np
    keys = {}
    h = np.array([keys.setdefault(json.dumps(r), len(keys)) for r in rows], dtype=np.int64)
    n = len(h)
    # a cycle of silence (or one held chord) is no song: it must hold at least 8 attacks
    attacks = np.concatenate(([0], np.cumsum([sum(1 for c in r if c and c[3]) for r in rows])))
    best = None
    for length in range(min_loop, n // 2 + 1):
        eq = (h[:n - length] == h[length:]).astype(np.int8)
        # first index where a run of `length` equal frames starts
        c = np.concatenate(([0], np.cumsum(eq)))
        runs = c[length:] - c[:-length]
        hit = [s for s in np.nonzero(runs == length)[0] if attacks[s + length] - attacks[s] >= 8]
        if hit:
            best = (int(hit[0]), length)
            break
    if best is not None:
        return best + (True, 1.0)
    # A driver not locked to the vertical blank (timer interrupt) shifts a note or an envelope step
    # by one frame from one pass to the next: no exact cycle. The period is the shift that keeps
    # the most notes; the loop point, a frame where both passes join exactly, after the intro.
    keys2 = {}
    notes = np.array([keys2.setdefault(json.dumps([c[0] if c else None for c in r]), len(keys2)) for r in rows])
    # The period is judged on the second half of the recording: an introduction that never comes
    # back (a title screen's first 20 s) must not hide the cycle that follows it.
    half = n // 2
    scores = [((notes[half:n - L] == notes[half + L:]).mean() if n - L > half + 60 else 0.0, L)
              for L in range(min_loop, n // 3 + 1)]
    top = max(r for r, _ in scores)
    ratio, length = min(((r, L) for r, L in scores if r >= top - 0.005), key=lambda x: x[1])  # shortest best
    if ratio < 0.85:
        raise MusicError(f"no cycle: best period {length} frames keeps {ratio:.0%} of the notes; record longer")
    same_notes = notes[:n - length] == notes[length:]
    # the loop starts where a whole pass repeats note for note, at a frame where both join exactly
    eq = (h[:n - length] == h[length:])
    s = next((s for s in range(0, n - 2 * length) if same_notes[s:s + length].mean() >= ratio - 0.02
              and eq[s:s + 8].all() and attacks[s + length] - attacks[s] >= 8), None)
    if s is None:
        raise MusicError(f"period {length} frames: no frame where two passes join exactly")
    return s, length, False, float(ratio)


def events(rows, ch, start, stop, inst, sent):
    """sent: the pan the voice holds on entry, None when unknown (loop entry)."""
    out = []
    panned = any(r[ch] is not None and r[ch][4] != (0, 0) for r in rows)
    for t in range(start, stop):
        cur = rows[t][ch]
        old = rows[t - 1][ch] if t else None
        if cur is None:
            ev = {"kind": "off" if old is not None else "hold", "duration_frames": 1}
        elif old is None or cur[3]:
            ev = {"kind": "attack", "duration_frames": 1, "note": cur[0], "instrument": inst, "attenuation": cur[2]}
        else:
            ev = {"kind": "hold", "duration_frames": 1}
            if cur[2] != old[2]:
                ev["attenuation"] = cur[2]
            if cur[0] != old[0]:
                ev["noise_note" if ch == 3 else "divider"] = cur[0] if ch == 3 else cur[1]
        if panned and cur is not None and cur[4] != sent:
            sent = cur[4]
            ev["pan"] = {"left": sent[0], "right": sent[1]}
        if ev == {"kind": "hold", "duration_frames": 1} and out:
            out[-1]["duration_frames"] += 1
        else:
            out.append(ev)
    return out


def convert(log, noise_setting=5, transpose=(0, 0, 0), first=None, min_loop=600, loop=None):
    """loop: (start, end) frames of the log imposed as the cycle (a one-shot jingle looped whole)."""
    rows = frame_rows(log, noise_setting, list(transpose))
    if loop:
        s, length, exact, ratio = loop[0], loop[1] - loop[0], False, 0.0
    else:
        s, length, exact, ratio = find_loop(rows, min_loop)
    prefix = max(1, s)
    total = prefix + length
    active = [any(rows[t][ch] is not None for t in range(total)) for ch in range(4)]
    bank = {"version": 2, "instruments": []}
    inst = {}
    for ch in range(4):
        if active[ch]:
            d = dict.fromkeys(FIELDS, 0)
            d.update(env_step=1, env_speed=1, vib_speed=1, sweep_speed=1, sweep_end=1,
                     mode=1 if ch == 3 else 0, noise_config=noise_setting - 1 if ch == 3 else 0)
            inst[ch] = len(bank["instruments"])
            bank["instruments"].append({"name": f"GB CH{ch}: envelope per frame", "def": d})
    notes = {}
    for t in range(total):
        for ch in range(3):
            cur = rows[t][ch]
            if cur is not None:
                notes[cur[0]] = cur[1]
    score = {"schema": "ngpcraft-held-score/1", "duration_unit_frames": 1,
             "notes": [{"id": n, "divider": d} for n, d in sorted(notes.items())],
             "channels": [{"first": events(rows[:total], ch, 0, prefix, inst[ch], (0, 0)),
                           "repeat": events(rows[:total], ch, prefix, total, inst[ch], None)} if active[ch] else None
                          for ch in range(4)]}
    attacks = {rows[t][ch][0] for t in range(total) for ch in range(3)
               if rows[t][ch] is not None and (rows[t][ch][3] or t == 0 or rows[t - 1][ch] is None)}
    report = {"schema": "ngpcraft-apu-log-conversion/1", "source": log.get("rom"), "frames": len(rows),
              "loop_start_frame": prefix, "loop_frames": length, "loop_exact": exact,
              "loop_note_match": round(ratio, 4), "active_channels": active,
              "attack_notes": len(attacks), "noise_setting": noise_setting, "transpose_semitones": list(transpose),
              "losses": ["Pulse duty and wave samples replaced by a square wave.",
                         "Envelope volume sampled once a frame, 2 dB attenuation steps.",
                         "Noise pitch replaced by a fixed preset.",
                         "NR51 routing kept as a silent side (pan); master L/R volume and sweep discarded.",
                         "GB 59.73 Hz frames played at the NGPC 59.95 Hz vertical blank (0.4 % faster)."]}
    return score, bank, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--min-loop", type=int, default=600)
    ap.add_argument("--noise", type=int, default=5)
    ap.add_argument("--loop", type=int, nargs=2, metavar=("START", "END"),
                    help="impose the cycle (frames of the log): a one-shot song looped as a whole")
    a = ap.parse_args()
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out}: output must be new or empty")
    log = json.loads(a.log.read_text(encoding="utf-8"))
    transpose = [0, 0, 0]
    while True:
        try:
            score, bank, report = convert(log, a.noise, transpose, min_loop=a.min_loop, loop=a.loop)
            break
        except MusicError as e:     # a channel below the PSG's lowest divider: one octave up
            import re
            m = re.search(r"channel\[(\d)\]", str(e))
            if not m or "outside 1..1023" not in str(e) or transpose[int(m.group(1))] >= 36:
                raise
            transpose[int(m.group(1))] += 12
    a.out.mkdir(parents=True, exist_ok=True)
    for name, value in (("held-score.json", score), ("bank.json", bank), ("report.json", report)):
        (a.out / name).write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")
    plan = compile_score(a.out / "held-score.json", a.out / "bank.json")
    for name, text in generate_music(plan).items():
        (a.out / "native" / name).parent.mkdir(parents=True, exist_ok=True)
        (a.out / "native" / name).write_text(text, encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("frames", "loop_start_frame", "loop_frames", "loop_exact", "loop_note_match", "attack_notes",
                                            "active_channels", "transpose_semitones")}))


if __name__ == "__main__":
    main()
