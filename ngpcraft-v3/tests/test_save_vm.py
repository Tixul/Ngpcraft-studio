"""NV1 context/pile codec: lossless oracle, actual C/wasm, Toshiba compile."""
import copy
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.save_vm import CONTEXT_BYTES, FIELDS, HEADS, Layout, budget, decode, encode
from tools.reference import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "ngpcraft/runtime/save_vm.c"
CLANG = os.environ.get("NGC_TEST_CLANG")


def snapshot(layout, seed):
    rng = random.Random(seed)
    def ref():
        return rng.choice([None, *range(layout.contexts)])
    state = {name: ref() for name in HEADS}
    state["vm_lock_state"] = rng.randrange(256)
    state["memory"] = [rng.randrange(65536) for _ in range(layout.words)]
    state["contexts"] = []
    for index in range(layout.contexts):
        state["contexts"].append({
            "PC": rng.randrange(0x1000000), "bank": rng.randrange(256), "next": ref(),
            "update_fn": rng.randrange(0x1000000), "update_fn_bank": rng.randrange(256),
            "stack_ptr": rng.choice([0, layout.base(index), layout.base(index) + 2 * layout.stack_words]),
            "base_addr": layout.base(index), "ID": index + 1,
            "hthread": rng.choice([0, 0x4000, 0x6BFE]),
            **{name: rng.randrange(256) for name in ("terminated", "waitable", "lock_count", "flags")}})
    return state


class OracleTests(unittest.TestCase):
    def test_random_snapshots_preserve_every_field_and_memory_word(self):
        for seed in range(200):
            count = 1 + seed % 31
            layout = Layout(count, seed % 34, 1 + seed % 17, 0x4000)
            original = snapshot(layout, seed)
            data = encode(layout, original)
            self.assertEqual(len(data), layout.size)
            self.assertEqual(decode(layout, data), original)

    def test_gbs2_worst_case_fits_even_with_all_stacks_present(self):
        from ngpcraft.save_state import check_capacity
        layout = Layout(31, 33, 17, 0x4000)
        self.assertEqual(layout.size, 1659)
        # Raw measured state minus raw VM save points, plus packed NV1.
        reduced = 2669 - (1054 + 16 + 1 + 1120) + layout.size
        self.assertEqual(reduced, 2137)
        self.assertEqual(check_capacity(reduced), 9)
        self.assertEqual(3 * check_capacity(reduced) + 1, 28)

    def test_budget_uses_measured_points_and_rejects_overflow(self):
        sizes = {"CTXS": 1054, "script_memory": 1120, "vm_lock_state": 1,
                 **{name: 4 for name in HEADS}}
        report = {"raw_state_max_bytes": 2669,
                  "variables": [{"name": name, "decision": "save", "target_bytes": size}
                                for name, size in sizes.items()]}
        manifest = {"gbvm": {"heap_words": 33, "stack_budget": {"contexts": 31, "words_per_context": 17}}}
        result = budget(report, manifest)
        self.assertEqual(result["payload_max_bytes"], 2137)
        self.assertEqual(result["payload_margin_bytes"], 400)
        from ngpcraft.compiler import CompileError
        oversized = copy.deepcopy(report)
        oversized["raw_state_max_bytes"] += 401
        with self.assertRaises(CompileError):
            budget(oversized, manifest)
        missing = copy.deepcopy(report)
        missing["variables"].pop()
        with self.assertRaises(ValueError):
            budget(missing, manifest)
        wrong = copy.deepcopy(manifest)
        wrong["gbvm"]["heap_words"] += 1
        with self.assertRaises(ValueError):
            budget(report, wrong)

    def test_variables_are_at_a_fixed_peek_offset(self):
        layout = Layout(3, 5, 7, 0x4000)
        state = snapshot(layout, 33)
        data = encode(layout, state)
        for index in range(layout.heap_words):
            self.assertEqual(int.from_bytes(data[12 + 2 * index:14 + 2 * index], "little"), state["memory"][index])

    def test_truncation_and_invalid_references_are_rejected(self):
        layout = Layout(2, 3, 4, 0x4000)
        data = encode(layout, snapshot(layout, 1))
        for length in range(len(data)):
            with self.assertRaises(ValueError):
                decode(layout, data[:length])
        start = 12 + 2 * layout.words
        for offset, value in ((0, 0), (3, 3), (7, 3), (start + 4, 3),
                              (start + 9, 254), (start + 11, 1)):
            changed = bytearray(data)
            changed[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                decode(layout, changed)

    def test_unsupported_addresses_id_and_layout_fail_before_encoding(self):
        layout = Layout(2, 3, 4, 0x4000)
        state = snapshot(layout, 1)
        for name, value in (("PC", 0x1000000), ("update_fn", -1), ("ID", 99),
                            ("base_addr", 0), ("stack_ptr", 0x4001), ("hthread", 0x7000), ("flags", 256)):
            changed = copy.deepcopy(state)
            changed["contexts"][0][name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                encode(layout, changed)
        with self.assertRaises(ValueError):
            Layout(255, 100, 64, 0x4000)

    def test_all_pinned_script_context_fields_have_a_representation(self):
        source = (REFERENCE / "appData/engine/gbvm/include/vm.h").read_text(encoding="utf-8")
        context = source.split("typedef struct SCRIPT_CTX", 1)[1].split("} SCRIPT_CTX;", 1)[0]
        context = re.sub(r"//[^\n]*", "", context)
        names = set(re.findall(r"(\w+)\s*;", context))
        self.assertEqual(names, set(FIELDS))
        self.assertEqual(CONTEXT_BYTES, 17)


@unittest.skipUnless(CLANG, "requires Clang with wasm32 and Node")
class CTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wasm = Path(cls.tmp.name) / "codec.wasm"
        cls.artifacts = None
        cls.record_index = 0
        if os.environ.get("NGC_TEST_ARTIFACTS"):
            cls.artifacts = Path(os.environ["NGC_TEST_ARTIFACTS"]) / "save-vm"
            cls.artifacts.mkdir(parents=True, exist_ok=False)
        harness = Path(cls.tmp.name) / "harness.c"
        harness.write_text(Path(__file__).with_name("save_vm_harness.c").read_text(encoding="utf-8")
                           .replace("#include CODEC_SOURCE", '#include "' + UNIT.as_posix() + '"'), encoding="utf-8")
        subprocess.run([CLANG, "--target=wasm32", "-nostdlib", "-O2", "-fno-builtin", "-std=c89",
                        "-Wall", "-Werror", "-Wl,--no-entry", "-Wl,--export-all", "-Wl,--global-base=16384",
                        "-Wl,-z,stack-size=8192",
                        str(harness), "-o", str(cls.wasm)], check=True)
        if cls.artifacts:
            shutil.copy2(cls.wasm, cls.artifacts / "codec.wasm")
            shutil.copy2(harness, cls.artifacts / "harness.c")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def replay(self, cases, wasm=None):
        path = Path(self.tmp.name) / "cases.json"
        path.write_text(json.dumps(cases), encoding="utf-8")
        proc = subprocess.run(["node", str(Path(__file__).with_name("save_vm_replay.js")), str(wasm or self.wasm), str(path)],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        if self.artifacts:
            type(self).record_index += 1
            stem = f"replay-{type(self).record_index:02d}"
            (self.artifacts / f"{stem}-cases.json").write_text(json.dumps(cases), encoding="utf-8")
            (self.artifacts / f"{stem}-observed.json").write_text(proc.stdout, encoding="utf-8")
            shutil.copy2(wasm or self.wasm, self.artifacts / f"{stem}.wasm")
        return json.loads(proc.stdout)

    def layout(self):
        result = self.replay([{"seed": 0}])[0]
        return Layout(31, 33, 17, result["address"])

    def test_c_restore_and_emit_equal_the_oracle(self):
        layout = self.layout()
        states = [snapshot(layout, seed) for seed in range(200)]
        blobs = [encode(layout, state) for state in states]
        results = self.replay([{"seed": seed, "data": data.hex()} for seed, data in enumerate(blobs)])
        for state, data, result in zip(states, blobs, results):
            self.assertEqual(result["ret"], 1)
            self.assertEqual(result["emitted"], 1)
            self.assertEqual(bytes.fromhex(result["after"]), data)
            self.assertEqual(decode(layout, bytes.fromhex(result["after"])), state)

    def test_invalid_restore_is_atomic_at_every_truncated_byte(self):
        layout = self.layout()
        data = encode(layout, snapshot(layout, 19))
        cases = [{"seed": 3, "data": data[:length].hex()} for length in range(len(data))]
        start = 12 + 2 * layout.words
        for offset, value in ((0, 0), (3, 32), (7, 32), (start + 4, 32), (start + 9, 254), (start + 11, 1)):
            changed = bytearray(data)
            changed[offset] = value
            cases.append({"seed": 3, "data": changed.hex()})
        for result in self.replay(cases):
            self.assertEqual(result["ret"], 0)
            self.assertEqual(result["after"], result["before"])

    def test_invalid_state_writes_no_bytes(self):
        for result in self.replay([{"seed": 1, "badIdentity": True}, {"seed": 1, "badPointer": True}]):
            self.assertEqual(result["ret"], 0)
            self.assertEqual(result["length"], 0)

    def test_witness_losing_flags_is_detected(self):
        folder = Path(self.tmp.name)
        unit = folder / "witness.c"
        source = UNIT.read_text(encoding="utf-8")
        anchor = "ngc_save_put(c->flags);"
        self.assertEqual(source.count(anchor), 1)
        unit.write_text(source.replace(anchor, "ngc_save_put(0u);"), encoding="utf-8")
        harness = folder / "witness_harness.c"
        harness.write_text(Path(__file__).with_name("save_vm_harness.c").read_text(encoding="utf-8")
                           .replace("#include CODEC_SOURCE", '#include "' + unit.as_posix() + '"'), encoding="utf-8")
        wasm = folder / "witness.wasm"
        subprocess.run([CLANG, "--target=wasm32", "-nostdlib", "-O2", "-fno-builtin", "-std=c89",
                        "-Wl,--no-entry", "-Wl,--export-all", "-Wl,--global-base=16384", "-Wl,-z,stack-size=8192",
                        str(harness), "-o", str(wasm)], check=True)
        address = self.replay([{"seed": 0}], wasm)[0]["address"]
        data = encode(Layout(31, 33, 17, address), snapshot(Layout(31, 33, 17, address), 91))
        result = self.replay([{"seed": 1, "data": data.hex()}], wasm)[0]
        self.assertEqual(result["ret"], 1)
        self.assertNotEqual(result["after"], data.hex())


@unittest.skipUnless(os.environ.get("NGC_TEST_TOSHIBA"), "requires user Toshiba toolchain")
class ToshibaTests(unittest.TestCase):
    def test_codec_compiles_in_c89_on_target(self):
        from ngpcraft.toolchain import _compiler_lock, locate
        info = locate(os.environ["NGC_TEST_TOSHIBA"])
        env = os.environ.copy()
        env["THOME"] = info["root"]
        env["PATH"] = info["bin"] + os.pathsep + env.get("PATH", "")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            harness = path / "harness.c"
            harness.write_text(Path(__file__).with_name("save_vm_harness.c").read_text(encoding="utf-8")
                               .replace("#include CODEC_SOURCE", '#include "' + UNIT.as_posix() + '"'), encoding="utf-8")
            with _compiler_lock():
                proc = subprocess.run([str(Path(info["bin"]) / "cc900.exe"), "-c", "-O3", "-Nb2",
                                       str(harness), "-o", str(path / "codec.rel")],
                                      cwd=path, env=env, capture_output=True, timeout=120)
            self.assertEqual(proc.returncode, 0, (proc.stdout + proc.stderr).decode(errors="replace"))
            self.assertTrue((path / "codec.rel").exists())


if __name__ == "__main__":
    unittest.main()
