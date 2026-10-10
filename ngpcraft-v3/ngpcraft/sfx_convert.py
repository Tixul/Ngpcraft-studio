"""Sound effect files -> native NGPC effect timelines (docs/M7_SFX_CONVERT.md).

A GB Studio sound asset (`.sav` FX Hammer bank, `.vgm`/`.vgz` DMG log) is
read the way GB Studio 4.3.2 compiles it (lib/compiler/sounds/
compileFXHammer.ts, compileVGM.ts): the same steps, the same frame counts,
the same register writes reaching the sound driver. Those writes are then
played on a model of the Game Boy APU (square 1/2, noise; envelopes, length
counters, NR51 routing, NR52), one state per 60 Hz frame, and each frame
becomes one native step (project_sfx.validate_bindings): the louder square
on the PSG tone voice, the noise channel on the PSG noise voice.

Everything the NGPC timeline cannot carry is listed in `losses`, never
dropped silently: duty cycle, stereo, frequency sweep, a second square
playing at once, the 7-bit LFSR, noise rates rounded to the K2GE's three.
`.wav` effects are sample playback: refused (the native timeline is PSG
steps only).
"""
import gzip
import math
from pathlib import Path

from .compiler import CompileError

FORMAT = "ngpcraft-native-sfx/1"
PSG_CLOCK_TONE = 96000                   # K2GE PSG tone: f = 96 000 / N (project_sfx.py)
NOISE_RATES = (6000.0, 3000.0, 1500.0)   # K2GE noise shift rates N/512, N/1024, N/2048 (N = 3.072 MHz)
FRAME_HZ = 60
FX_HAMMER_DELAY = 4                      # compileFXHammer options.delay
FX_NOTE_FREQS = (
    44, 156, 262, 363, 457, 547, 631, 710, 786, 854, 923, 986, 1046, 1102, 1155,
    1205, 1253, 1297, 1339, 1379, 1417, 1452, 1486, 1517, 1546, 1575, 1602, 1627,
    1650, 1673, 1694, 1714, 1732, 1750, 1767, 1783, 1798, 1812, 1825, 1837, 1849,
    1860, 1871, 1881, 1890, 1899, 1907, 1915, 1923, 1930, 1936, 1943, 1949, 1954,
    1959, 1964, 1969, 1974, 1978, 1982, 1985, 1988, 1992, 1995, 1998, 2001, 2004,
    2006, 2009, 2011, 2013, 2015)


# -- reading, as GB Studio compiles -------------------------------------------

def fxhammer_effects(data):
    """compileFXHammer / readFXHammerNumEffects: indices of the effects present."""
    if data[0x9:0x12] != b"FX HAMMER":
        raise CompileError("not an FX Hammer file (GB Studio: Invalid file format)")
    return [n for n in range(0x3C) if data[0x300 + n]]


def fxhammer_frames(data, effect):
    """compileFXHammerSingle: [(writes, frames)] of one effect. The effect
    index is clamped as GB Studio clamps it, with a loss note."""
    present = fxhammer_effects(data)
    if not present:
        raise CompileError("FX Hammer file holds no effect")
    notes = []
    chosen = max(0, min(effect, len(present) - 1))
    if chosen != effect:
        notes.append(f"effect {effect} clamped to {chosen} as GB Studio does ({len(present)} effects)")
    channels = data[0x300 + chosen]
    if not channels:
        raise CompileError(f"FX Hammer effect {chosen}: no channels (GB Studio refuses it)")
    body = data[0x400 + chosen * 256:0x400 + (chosen + 1) * 256]
    if len(body) != 256:
        raise CompileError("FX Hammer file: unexpected end of file")
    frames, pan = [], 0xFF
    for i in range(32):
        duration, ch2pan, ch2vol, ch2duty, ch2note, ch4pan, ch4vol, ch4freq = body[i * 8:i * 8 + 8]
        if duration == 0:
            break
        writes = {}
        current = 0b01010101 | ch2pan | ch4pan          # usePan, with GB Studio's failsafes
        if ch2pan == 0:
            current |= 0b00100010
        if ch4pan == 0:
            current |= 0b10001000
        if current != pan:
            writes[0x25] = current
            pan = current
        if ch2pan:
            freq = FX_NOTE_FREQS[(ch2note - 0x40) >> 1]
            writes.update({0x16: ch2duty, 0x17: ch2vol, 0x18: freq & 0xFF, 0x19: ((freq >> 8) | 0x80) & 0xFF})
        if ch4pan:
            writes.update({0x20: 0x2A, 0x21: ch4vol, 0x22: ch4freq, 0x23: 0x80})
        # One frame with the writes, then delay * duration - 1 frames of wait.
        frames.append((writes, max(1, FX_HAMMER_DELAY * duration)))
    # cutSound: NRx2 = 0, NRx4 = 0xC0 on the effect's channels; usePan: NR51 = 0xFF.
    end = {0x25: 0xFF}
    if channels & 0xF0:
        end.update({0x17: 0, 0x19: 0xC0})
    if channels & 0x0F:
        end.update({0x21: 0, 0x23: 0xC0})
    frames.append((end, 1))
    return frames, notes


def vgm_frames(data):
    """compileVGM: [(writes, frames)], grouped at each wait command exactly as
    GB Studio groups them (its register ranges included: NR3x writes and NRx4
    -less rows never reach its driver)."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data[:4] != b"Vgm ":
        raise CompileError("not a VGM file (GB Studio: Invalid file format)")
    version = int.from_bytes(data[8:12], "little")
    if version < 0x161:
        raise CompileError(f"VGM version {version:#x} below 1.61 (GB Studio: VGM version too low)")
    ptr = 0x34 + int.from_bytes(data[0x34:0x38], "little")
    frames, row, notes = [], {}, []

    def flush(delay):
        writes = {}
        nr5 = row.pop(4, None)
        if nr5 is not None:
            for i in (3, 0, 1):
                if i in nr5:
                    writes[0x24 + i] = nr5[i]
        wave = row.pop(5, None)
        if wave is not None:
            notes.append("wave RAM writes ignored (channel 3 is not played by GB Studio's VGM path)")
        for j, base in ((0, 0x10), (1, 0x15), (3, 0x1F)):
            ch = row.pop(j, None)
            if ch is None:
                continue
            if j == 1 and any(i >= 5 for i in ch):
                # 0x1A..0x1F fall in compileVGM's 0x16..0x20 range at indices
                # 5..10, which its row loop (i < 5) never writes.
                notes.append("channel 3 (wave) writes ignored, as GB Studio's VGM path drops them")
            if 4 not in ch:
                continue                     # GB Studio's driver writes a row only with its NRx4
            for i in range(5):
                if i in ch:
                    writes[base + i] = ch[i]
        row.clear()
        frames.append((writes, max(1, delay)))

    while ptr < len(data):
        cmd = data[ptr]
        ptr += 1
        if cmd == 0x66:
            break
        if cmd == 0xB3:
            addr, value = data[ptr] + 0x10, data[ptr + 1]
            ptr += 2
            # compileVGM's ranges, checked in its order.
            if 0x10 <= addr < 0x16:
                row.setdefault(0, {})[addr - 0x10] = value
            elif 0x16 <= addr < 0x20:
                row.setdefault(1, {})[addr - 0x15] = value
            elif 0x20 <= addr < 0x24:
                row.setdefault(3, {})[addr - 0x1F] = value
            elif 0x24 <= addr < 0x27:
                row.setdefault(4, {})[addr - 0x24] = value
            elif 0x30 <= addr < 0x40:
                row.setdefault(5, {})[addr - 0x30] = value
            else:
                raise CompileError(f"VGM: invalid register address {addr:#x} (GB Studio refuses it)")
        elif cmd in (0x61, 0x62) or 0x70 <= cmd <= 0x7F:
            delay = 0
            if cmd == 0x61:
                delay = round(int.from_bytes(data[ptr:ptr + 2], "little") / 65535 * 90)
                ptr += 2
            elif cmd >= 0x70:
                delay = cmd - 0x6F
            flush(delay)
        else:
            raise CompileError(f"VGM: unsupported command {cmd:#04x} (GB Studio refuses it)")
    return frames, sorted(set(notes))


# -- the Game Boy APU, one state per frame -------------------------------------

class _Square:
    def __init__(self):
        self.on, self.volume, self.env_up, self.env_period, self.env_clock = False, 0, False, 0, 0.0
        self.freq, self.length, self.length_on, self.duty = 0, 0.0, False, 2

    def trigger(self, nrx1, nrx2, freq, length_on):
        self.on = bool(nrx2 & 0xF8)             # DAC off: no sound
        self.volume, self.env_up, self.env_period = nrx2 >> 4, bool(nrx2 & 8), nrx2 & 7
        self.env_clock, self.freq, self.length_on = 0.0, freq, length_on
        self.length = (64 - (nrx1 & 63)) / 256 * FRAME_HZ
        self.duty = nrx1 >> 6

    def advance(self):
        """One frame: envelope steps (period / 64 s) and length (n / 256 s)."""
        if self.env_period:
            self.env_clock += 1
            step = self.env_period * FRAME_HZ / 64
            while self.env_clock >= step:
                self.env_clock -= step
                self.volume = min(15, self.volume + 1) if self.env_up else max(0, self.volume - 1)
        if self.length_on:
            self.length -= 1
            if self.length <= 0:
                self.on = False


class _Apu:
    def __init__(self):
        self.reg = {a: 0 for a in range(0x10, 0x27)}
        self.reg[0x24], self.reg[0x25], self.reg[0x26] = 0x77, 0xFF, 0x80
        self.sq = [_Square(), _Square()]
        self.noise = _Square()

    def write(self, addr, value, notes):
        self.reg[addr] = value
        if addr == 0x10 and value & 0x77:
            notes.add("channel 1 frequency sweep (NR10) not carried")
        for k, base in ((0, 0x10), (1, 0x15)):
            if addr == base + 4 and value & 0x80:
                freq = self.reg[base + 3] | (value & 7) << 8
                self.sq[k].trigger(self.reg[base + 1], self.reg[base + 2], freq, bool(value & 0x40))
            elif addr == base + 2 and not value & 0xF8:
                self.sq[k].on = False
            elif addr in (base + 3, base + 4):
                self.sq[k].freq = self.reg[base + 3] | (self.reg[base + 4] & 7) << 8
        if addr == 0x23 and value & 0x80:
            self.noise.trigger(self.reg[0x20], self.reg[0x21], 0, bool(value & 0x40))
        elif addr == 0x21 and not value & 0xF8:
            self.noise.on = False
        if addr == 0x26 and not value & 0x80:
            for ch in (*self.sq, self.noise):
                ch.on = False

    def routed(self, bit):
        """NR51: a channel sent to neither side is silent; NR50 volume (0..7) kept."""
        nr51, nr50 = self.reg[0x25], self.reg[0x24]
        sides = [((nr51 >> (bit + 4)) & 1, (nr50 >> 4) & 7), ((nr51 >> bit) & 1, nr50 & 7)]
        levels = [level for on, level in sides if on]
        return (max(levels) + 1) / 8 if levels else 0.0

    def state(self, notes):
        """(tone divider, tone attenuation, noise, noise attenuation) of this frame."""
        if not self.reg[0x26] & 0x80:
            return 1, 15, 0, 15
        voices = []
        for k, ch in enumerate(self.sq):
            gain = self.routed(k)
            if ch.on and ch.volume and gain:
                voices.append((ch.volume * gain, k, ch))
        tone = (1, 15)
        if voices:
            voices.sort(key=lambda v: (-v[0], v[1]))
            if len(voices) > 1:
                notes.add("two squares at once: the louder one is kept (one PSG tone voice)")
            level, k, ch = voices[0]
            if ch.duty != 2:
                notes.add("square duty cycle other than 50% played as 50% (PSG square)")
            hz = 131072 / (2048 - ch.freq)
            divider = round(PSG_CLOCK_TONE / hz)
            if not 1 <= divider <= 1023:
                notes.add(f"square at {hz:.0f} Hz outside the PSG range: clamped")
                divider = max(1, min(1023, divider))
            tone = (divider, 15 - min(15, round(level)))
        noise = (0, 15)
        gain = self.routed(3)
        if self.noise.on and self.noise.volume and gain:
            nr43 = self.reg[0x22]
            r, s = nr43 & 7, nr43 >> 4
            if s >= 14:
                noise = (0, 15)
                notes.add("noise shift 14/15 (no clock) silent, as on the Game Boy")
            else:
                hz = 524288 / (r if r else 0.5) / 2 ** (s + 1)
                rate = min(range(3), key=lambda i: abs(math.log2(hz / NOISE_RATES[i])))
                if abs(math.log2(hz / NOISE_RATES[rate])) > 0.5:
                    notes.add("noise rates rounded to the K2GE's three (6000 / 3000 / 1500 Hz)")
                if nr43 & 8:
                    notes.add("7-bit LFSR (metallic noise) played as white noise")
                noise = (4 | rate, 15 - min(15, round(self.noise.volume * gain)))
        if self.reg[0x25] not in (0xFF, 0x00) and any(
                ((self.reg[0x25] >> b) & 1) != ((self.reg[0x25] >> (b + 4)) & 1) for b in range(4)):
            notes.add("stereo panning (NR51) played centred")
        return tone[0], tone[1], noise[0], noise[1]

    def advance(self):
        for ch in (*self.sq, self.noise):
            ch.advance()


def timeline(frames):
    """[(writes, frames)] -> (native steps, losses)."""
    apu, notes, states = _Apu(), set(), []
    for writes, count in frames:
        for addr in sorted(writes, key=lambda a: (a != 0x26, a)):
            apu.write(addr, writes[addr], notes)
        for _ in range(count):
            states.append(apu.state(notes))
            apu.advance()
    while states and states[-1][1] == 15 and states[-1][3] == 15 and len(states) > 1:
        states.pop()                            # trailing silence: the effect ends
    rows = []
    for state in states:
        if rows and rows[-1][1:] == list(state) and rows[-1][0] < 254:
            rows[-1][0] += 1
        else:
            rows.append([1, *state])
    if len(rows) > 65535:
        raise CompileError(f"{len(rows)} steps: more than 65535")
    keys = ("frames", "tone_divider", "tone_attenuation", "noise", "noise_attenuation")
    return [dict(zip(keys, r)) for r in rows], sorted(notes)


def convert(path, effect=0):
    """The `convert-sfx` command: a sound file -> native steps + losses."""
    path = Path(path)
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".wav":
        raise CompileError(f"{path.name}: WAV effects are sample playback; the native NGPC effect "
                           "timeline is PSG tone/noise steps only (not delivered)")
    if suffix == ".sav":
        frames, notes = fxhammer_frames(data, effect)
        kind = "fxhammer"
    elif suffix in (".vgm", ".vgz"):
        if effect:
            raise CompileError(f"{path.name}: a VGM holds one effect (effect {effect} asked)")
        frames, notes = vgm_frames(data)
        kind = "vgm"
    else:
        raise CompileError(f"{path.name}: unknown sound file type (GB Studio: .wav, .vgm, .vgz, .sav)")
    rows, losses = timeline(frames)
    return {"format": FORMAT, "source": path.name, "kind": kind, "effect": effect,
            "frames": sum(r["frames"] for r in rows), "rows": rows,
            "losses": sorted(set(notes) | set(losses))}
