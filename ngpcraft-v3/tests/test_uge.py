"""UGE intake: released JS loader, original saver, corrupt files, no writes."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from ngpcraft.audio_assets import MusicError
from ngpcraft.uge import inspect_uge, parse_uge, read_uge
from tools.reference import REFERENCE
from tools.uge_oracle import run

GBS2 = REFERENCE / "appData/templates/gbs2"
ARTIFACTS = os.environ.get("NGC_TEST_ARTIFACTS")


def binary_fixture():
    """Independent byte layout, with offsets for corruption tests."""
    raw, offsets = bytearray(), {}

    def u32(value, key=None):
        if key:
            offsets[key] = len(raw)
        raw.extend(struct.pack("<I", value))

    def u8(value, key=None):
        if key:
            offsets[key] = len(raw)
        raw.append(value)

    def text(value):
        encoded = value.encode("utf-8")
        raw.extend(bytes([len(encoded)]) + encoded + bytes(255 - len(encoded)))

    u32(6, "version")
    text("Source épreuve")
    text("Author")
    text("No conversion")
    for i in range(45):
        kind = i // 15
        u32(kind, f"instrument-{i}-type")
        text(f"Instrument {i}")
        u32(32, f"instrument-{i}-length")
        u8(0, f"instrument-{i}-length-enabled")
        u8(12, f"instrument-{i}-volume")
        u32(0)
        u8(0)
        u32(0)
        u32(0)
        u32(0)
        u8(2)
        u32(1)
        u32(0)
        u32(0)
        u8(0, f"instrument-{i}-sub-enabled")
        for row in range(64):
            u32(90, f"instrument-{i}-sub-{row}-note")
            u32(0)
            u32(0, f"instrument-{i}-sub-{row}-jump")
            u32(0, f"instrument-{i}-sub-{row}-effect")
            u8(0, f"instrument-{i}-sub-{row}-param")
    offsets["waves"] = len(raw)
    raw.extend(bytes(range(16)) * 32)
    u32(6, "ticks")
    u8(0, "timer")
    u32(0, "divider")
    u32(4, "pattern-count")
    for pid in range(4):
        u32(pid, f"pattern-{pid}-id")
        for row in range(64):
            u32(12 + pid if row == 0 else 90, f"pattern-{pid}-row-{row}-note")
            u32(1 if row == 0 else 0, f"pattern-{pid}-row-{row}-instrument")
            u32(0, f"pattern-{pid}-row-{row}-unused")
            u32(0, f"pattern-{pid}-row-{row}-effect")
            u8(0, f"pattern-{pid}-row-{row}-param")
    for ch in range(4):
        u32(2, f"order-{ch}-count")
        u32(ch, f"order-{ch}-id")
        u32(0, f"order-{ch}-sentinel")
    for n in range(16):
        u32(0, f"routine-{n}")
    return bytes(raw), offsets


def mutate(raw, offsets, key, value, width=4):
    out = bytearray(raw)
    start = offsets[key]
    out[start:start + width] = value.to_bytes(width, "little")
    return bytes(out)


def keep(name, data):
    if ARTIFACTS:
        root = Path(ARTIFACTS) / "uge"
        root.mkdir(parents=True, exist_ok=True)
        with (root / name).open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)


class IntakeTests(unittest.TestCase):
    def test_source_units_empty_cells_instruments_and_metadata(self):
        song = parse_uge(binary_fixture()[0])
        self.assertEqual(song["name"], "Source épreuve")
        self.assertEqual(song["ticksPerRow"], 6)
        self.assertEqual(song["patterns"][0][0], {"note": 12, "instrument": 0, "effectCode": None, "effectParam": None})
        self.assertEqual(song["patterns"][0][1], {"note": None, "instrument": None, "effectCode": None, "effectParam": None})
        self.assertEqual([len(song[g]) for g in ("dutyInstruments", "waveInstruments", "noiseInstruments")], [15]*3)
        self.assertFalse(song["sequence"][0]["splitPattern"])

    def test_only_v6_is_supported_and_requires_bytes(self):
        raw, offsets = binary_fixture()
        for version in (0, 1, 5, 7, 0xFFFFFFFF):
            with self.subTest(version=version), self.assertRaisesRegex(MusicError, "version"):
                parse_uge(mutate(raw, offsets, "version", version))
        with self.assertRaisesRegex(MusicError, "binary bytes"):
            parse_uge([6])

    def test_truncations_at_every_layout_section_are_located(self):
        raw, offsets = binary_fixture()
        cuts = set(range(5)) | {256, 771, 772, len(raw) - 1}
        for key in ("instrument-0-type", "instrument-0-sub-0-note", "instrument-44-sub-63-param",
                    "waves", "ticks", "timer", "pattern-count", "pattern-0-id",
                    "pattern-3-row-63-param", "order-0-count", "order-3-sentinel", "routine-15"):
            cuts.update((offsets[key], offsets[key] + 1))
        for end in sorted(cuts):
            with self.subTest(end=end), self.assertRaisesRegex(MusicError, r"bad.uge.*byte"):
                parse_uge(raw[:end], "bad.uge")

    def test_huge_counts_are_bounded_by_actual_bytes(self):
        raw, offsets = binary_fixture()
        for key in ("pattern-count", "order-0-count"):
            with self.subTest(key=key), self.assertRaisesRegex(MusicError, "remaining file size"):
                parse_uge(mutate(raw, offsets, key, 0xFFFFFFFF))

    def test_malformed_values_never_clamp_or_wrap(self):
        raw, offsets = binary_fixture()
        cases = [("instrument-0-type", 3, 4), ("instrument-0-volume", 16, 1),
                 ("instrument-0-length-enabled", 2, 1), ("instrument-0-length", 65, 4),
                 ("instrument-0-sub-enabled", 2, 1), ("instrument-0-sub-0-jump", 65, 4),
                 ("pattern-0-row-0-note", 72, 4), ("pattern-0-row-0-note", 0xFFFFFFFF, 4),
                 ("pattern-0-row-0-instrument", 16, 4), ("pattern-0-row-0-effect", 16, 4),
                 ("ticks", 256, 4), ("timer", 2, 1), ("divider", 256, 4)]
        for key, value, width in cases:
            with self.subTest(key=key), self.assertRaises(MusicError):
                parse_uge(mutate(raw, offsets, key, value, width))

    def test_wave_samples_and_invalid_utf8_are_not_repaired(self):
        raw, offsets = binary_fixture()
        for index, value in ((5, 255), (offsets["waves"], 16)):
            bad = bytearray(raw)
            bad[index] = value
            with self.assertRaises(MusicError):
                parse_uge(bytes(bad))

    def test_duplicate_ids_unknown_orders_routines_and_trailer_are_refused(self):
        raw, offsets = binary_fixture()
        for key, value in (("pattern-1-id", 0), ("order-0-id", 99),
                           ("order-0-count", 0), ("order-0-sentinel", 1), ("routine-0", 1)):
            with self.subTest(key=key), self.assertRaises(MusicError):
                parse_uge(mutate(raw, offsets, key, value))
        with self.assertRaisesRegex(MusicError, "trailing"):
            parse_uge(raw + b"x")

    def test_sparse_large_ids_compact_without_large_allocation(self):
        raw, offsets = binary_fixture()
        for pid in range(4):
            raw = mutate(raw, offsets, f"pattern-{pid}-id", 0xFFFFFFF8 + pid)
            raw = mutate(raw, offsets, f"order-{pid}-id", 0xFFFFFFF8 + pid)
        song = parse_uge(raw)
        self.assertEqual(len(song["patterns"]), 4)
        self.assertEqual(song["sequence"][0]["channels"], [0, 1, 2, 3])

    def test_unreferenced_blocks_removed_but_blank_neighbours_are_explicit(self):
        raw, offsets = binary_fixture()
        raw = mutate(raw, offsets, "pattern-3-id", 4)
        raw = mutate(raw, offsets, "order-3-id", 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sparse.uge"
            path.write_bytes(raw)
            report = inspect_uge(path, True)
        self.assertEqual(len(report["song"]["patterns"]), 4)
        self.assertEqual(report["storage"]["implicit_blank_neighbours"], [3])
        self.assertEqual(report["song"]["patterns"][3], [
            {"note": None, "instrument": None, "effectCode": None, "effectParam": None} for _ in range(64)])
        self.assertEqual(report["storage"]["stored_patterns"], 4)

    def test_split_orders_effect_zero_and_nonzero_reserved_field(self):
        raw, offsets = binary_fixture()
        raw = mutate(raw, offsets, "order-0-id", 3)
        raw = mutate(raw, offsets, "pattern-3-row-1-param", 12, 1)
        raw = mutate(raw, offsets, "pattern-3-row-1-unused", 123)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.uge"
            path.write_bytes(raw)
            report = inspect_uge(path)
        self.assertTrue(report["sequence"][0]["splitPattern"])
        self.assertEqual(report["effects"]["0"]["name"], "arpeggio")
        self.assertEqual(len(report["effects"]["0"]["uses"]), 2)
        self.assertEqual(report["storage"]["nonzero_reserved_fields"][0]["value"], 123)

    def test_active_subpattern_effects_and_source_adaptations_are_reported(self):
        raw, offsets = binary_fixture()
        raw = mutate(raw, offsets, "instrument-30-sub-enabled", 1, 1)
        raw = mutate(raw, offsets, "instrument-30-sub-1-effect", 14)
        raw = mutate(raw, offsets, "instrument-30-sub-1-param", 3, 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.uge"
            path.write_bytes(raw)
            report = inspect_uge(path)
        self.assertFalse(report["runtime_ready"])
        self.assertEqual(report["subpattern_effects"]["E"]["uses"],
                         [{"location": "noiseInstruments[0].subpattern[1]", "param": 3}])
        self.assertEqual(report["waves"][0]["samples"], list(range(16))*2)
        needs = [r for i in report["instrument_adaptations"] for r in i["requirements"]]
        self.assertTrue(any(r.startswith("wave_to_square:") for r in needs))
        self.assertTrue(any(r.startswith("noise:") for r in needs))

    def test_cli_is_read_only_and_errors_are_localized(self):
        raw, _ = binary_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.uge"
            path.write_bytes(raw)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            proc = subprocess.run([sys.executable, "-B", "-m", "ngpcraft", "inspect-uge", str(path), "--song-data"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["sha256"], before)
            self.assertEqual(report["song"], parse_uge(raw))
            self.assertEqual(list(root.iterdir()), [path])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            path.write_bytes(raw[:7])
            proc = subprocess.run([sys.executable, "-B", "-m", "ngpcraft", "inspect-uge", str(path)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("source.uge.name", proc.stderr)


@unittest.skipUnless(shutil.which("node") and GBS2.exists(), "Node and released GB Studio required")
class OriginalLoaderTests(unittest.TestCase):
    def test_all_gbs2_files_match_the_real_loader_field_for_field(self):
        files = sorted(GBS2.rglob("*.uge"))
        self.assertTrue(files)
        originals = run([{"path": str(p)} for p in files])
        summary = []
        for path, original in zip(files, originals):
            with self.subTest(song=path.name):
                song, digest = read_uge(path)
                self.assertEqual(song, original["song"])
                report = inspect_uge(path)
                self.assertFalse(report["runtime_ready"])
                summary.append({"file": path.name, "sha256": digest, "identical_to_original": True,
                                "rows": report["sequence_rows"], "attacks": report["note_attacks_by_channel"],
                                "effects": {k: len(v["uses"]) for k, v in report["effects"].items()}})
                keep(path.stem + "-inspection.json", report)
        keep("gbs2-original-loader.json", summary)

    def test_split_sequences_sparse_blocks_and_raw_fields_match_original(self):
        raw, offsets = binary_fixture()
        cases = [raw]
        split = mutate(raw, offsets, "order-0-id", 3)
        cases.append(mutate(split, offsets, "pattern-3-row-1-param", 12, 1))
        sparse = raw
        for pid in range(4):
            sparse = mutate(sparse, offsets, f"pattern-{pid}-id", 8 + pid)
            sparse = mutate(sparse, offsets, f"order-{pid}-id", 8 + pid)
        cases.append(sparse)
        neighbour = mutate(raw, offsets, "pattern-3-id", 4)
        cases.append(mutate(neighbour, offsets, "order-3-id", 2))
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for n, data in enumerate(cases):
                path = Path(tmp) / f"case-{n}.uge"
                path.write_bytes(data)
                paths.append(path)
            originals = run([{"path": str(p)} for p in paths])
        for data, original in zip(cases, originals):
            self.assertEqual(parse_uge(data), original["song"])

    def test_original_saver_randomized_instruments_patterns_and_order(self):
        song = parse_uge(binary_fixture()[0])
        # The original saver writes string character counts; keep ASCII here.
        song["name"] = "Oracle fixture"
        rng = random.Random(607)
        cases = []
        for case in range(40):
            candidate = copy.deepcopy(song)
            candidate["ticksPerRow"] = rng.randrange(1, 33)
            candidate["timerEnabled"] = bool(case % 2)
            candidate["timerDivider"] = rng.randrange(256)
            for group in ("dutyInstruments", "waveInstruments", "noiseInstruments"):
                for inst in candidate[group]:
                    inst["length"] = rng.choice([None, rng.randrange(1, 257 if group == "waveInstruments" else 65)])
                    inst["subpatternEnabled"] = bool(rng.randrange(2))
                    if group == "waveInstruments":
                        inst.update(volume=rng.randrange(4), waveIndex=rng.randrange(16))
                    else:
                        inst.update(initialVolume=rng.randrange(16), volumeSweepChange=rng.randrange(-7, 8))
                        if group == "dutyInstruments":
                            inst.update(dutyCycle=rng.randrange(4), frequencySweepTime=rng.randrange(8),
                                        frequencySweepShift=rng.randrange(-7, 8))
                        else:
                            inst["bitCount"] = rng.choice([7, 15])
                    inst["subpattern"][0] = {"note": rng.randrange(72), "jump": rng.randrange(65),
                                              "effectCode": rng.randrange(16), "effectParam": rng.randrange(256)}
            for pattern in candidate["patterns"]:
                for row in rng.sample(range(64), 15):
                    pattern[row] = {"note": rng.choice([None, rng.randrange(72)]),
                                    "instrument": rng.choice([None, rng.randrange(15)]),
                                    "effectCode": rng.randrange(16), "effectParam": rng.randrange(256)}
            channels = [rng.randrange(4) for _ in range(4)]
            candidate["sequence"] = [{"splitPattern": channels != [0, 1, 2, 3], "channels": channels}]
            cases.append({"song": candidate})
        originals = run(cases)
        for n, original in enumerate(originals):
            with self.subTest(case=n):
                self.assertEqual(parse_uge(base64.b64decode(original["binary_base64"])), original["song"])
        keep("random-original-saver.json", {"cases": len(cases), "identical": len(cases)})

    def test_wrong_note_or_order_mapping_is_detected_by_original(self):
        raw, offsets = binary_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "witness.uge"
            path.write_bytes(raw)
            original = run([{"path": str(path)}])[0]["song"]
        bad_note = parse_uge(raw)
        bad_note["patterns"][0][0]["note"] += 1
        bad_order = parse_uge(raw)
        bad_order["sequence"][0]["channels"][0] = 3
        self.assertNotEqual(bad_note, original)
        self.assertNotEqual(bad_order, original)


if __name__ == "__main__":
    unittest.main()
