"""S1 hot-path benchmark: actors (move, animate, off-screen test, render, player
overlap) in variant A (specialized C) and variant D (GBVM actor code ported).

Both variants run the same scene; every measured tick is checked against an
independent Python oracle: OAM bytes, palette codes and the overlap result.
Timing is external (breakpoint on ngc_game_tick, silicon timing, free loop).
Not a full engine: no VM, no scroll, no tilemap, no physics state machine.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile

from ngpcraft.compiler import analyze, CompileError
from ngpcraft.emitter import generate
from ngpcraft.toolchain import build_rom
from tools.probe_rom import verified_tick, positive_int
from tools.reproducible_build import write_report, tool_fingerprints

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = {"A-specialized": "labs/s1_actors_spec.c", "D-gbvm": "labs/s1_actors_gbvm.c"}
FRAME_CYCLES = 515 * 199  # Ngpcraft_emulator cpp/src/core.cpp: kCyclesPerScanline * kScanlinesPerFrame


class Oracle:
    """Independent model of the laboratory scene (not derived from the C)."""

    def __init__(self, n_others):
        self.n = n_others + 1
        self.x = [(16 + (i % 6) * 20) << 5 for i in range(self.n)]
        self.y = [(16 + (i // 6) * 40) << 5 for i in range(self.n)]
        self.dx = [32 if i & 1 else -32 for i in range(self.n)]
        self.frame = [0] * self.n
        self.disabled = [False] * self.n
        self.time = 0

    def tick(self):
        for i in range(1, self.n):
            self.x[i] = (self.x[i] + self.dx[i]) & 0xFFFF
            px = self.x[i] >> 5
            if px <= 8 or px >= 136:
                self.dx[i] = -self.dx[i]
        t = self.time & 0xFF
        it = t
        for i in range(self.n):
            if (t & 7) == 0:
                self.frame[i] ^= 1
            if (it & 3) == 0:
                ax, ay = (self.x[i] >> 9) + 67, (self.y[i] >> 9) + 67
                self.disabled[i] = ax < 64 or ax > 80 or ay < 64 or ay > 79
            it = (it + 1) & 0xFF
        oam, pal = [], []
        for i in range(self.n):
            if self.disabled[i]:
                continue
            x, y = (self.x[i] >> 5) & 0xFF, (self.y[i] >> 5) & 0xFF
            tile = (128 + i * 8 + self.frame[i] * 4) & 0xFF
            for k, (ox, oy) in enumerate(((0, 0), (8, 0), (0, 8), (8, 8))):
                oam += [(tile + k) & 0xFF, 0x18, (x + ox) & 0xFF, (y + oy) & 0xFF]
                pal.append(0)
        used = len(pal)
        hit = 0xFF
        for i in range(1, self.n):
            if self.x[i] > self.x[0] + 511 or self.x[i] + 511 < self.x[0]:
                continue
            if self.y[i] > self.y[0] + 511 or self.y[i] + 511 < self.y[0]:
                continue
            hit = i
            break
        self.time += 1
        return bytes(oam), bytes(pal), used, hit


def build(dest, toolchain, variant, n_others, corrupt=False):
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("benchmark destination must be new or empty")
    files = generate(analyze({"schema": "ngpcraft-ir/0.1", "start": "lab", "scenes": [{"id": "lab"}]}), "rom")
    source = (ROOT / VARIANTS[variant]).read_text(encoding="utf-8")
    if corrupt:
        needle = "0x18u"
        if needle not in source:
            raise CompileError("corruption needle missing")
        source = source.replace(needle, "0x10u", 1)  # wrong priority on one write
    files["src/game.c"] = f"#define NGC_N_ACTORS {n_others}\n" + source
    wait = "        while (previous == ngc_frame) { }"
    if files["src/startup.c"].count(wait) != 1:
        raise CompileError("bootstrap changed: review free-loop transformation")
    files["src/startup.c"] = files["src/startup.c"].replace(wait, "        /* S1 laboratory: free loop, no vsync wait. */")
    manifest = json.loads(files.pop("export.json"))
    manifest["laboratory"] = {"name": "s1-actors", "variant": variant, "actors_besides_player": n_others,
                              "corrupt": corrupt, "free_loop": True,
                              "scratch_ram": {"address": 0x5000, "bytes": 1}}
    manifest["guarantee"] = "Disposable S1 laboratory; variant D contains MIT GBVM-derived code (see header)."
    manifest["sha256"] = {p: hashlib.sha256(s.encode()).hexdigest() for p, s in files.items()}
    files["export.json"] = json.dumps(manifest, indent=2) + "\n"
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
            f.write(text)
            tmp = f.name
        os.replace(tmp, path)
    return build_rom(dest, toolchain)


def observe(dest, emulator, bios_path, n_others, ticks):
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    dest = Path(dest)
    rom = (dest / "rom/game.ngc").read_bytes()
    report = json.loads((dest / "rom/build-report.json").read_text())
    tick = verified_tick(rom, report)
    m = native.NativeMachine(rom, bios=Path(bios_path).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    m.set_breakpoints([tick])
    first, _ = m.run(3_000_000, record=False)
    if first.stop_status != 40:
        raise CompileError("never reached tick")
    oracle = Oracle(n_others)
    costs, mismatches = [], []
    for t in range(ticks):
        step, _ = m.run(1, record=False)
        summary, _ = m.run(2_000_000, record=False)
        if summary.stop_status != 40 or m.cpu().pc != tick:
            raise CompileError("tick did not complete")
        costs.append(int(step.total_cycles) + int(summary.total_cycles))
        oam, pal, used, hit = oracle.tick()
        got_oam = m.read(0x8800, len(oam))
        got_pal = m.read(0x8C00, used)
        hidden = all(b == 0 for b in m.read(0x8800 + used * 4 + 1, (64 - used) * 4)[::4]) if used < 64 else True
        got_hit = m.read(0x5000, 1)[0]
        if got_oam != oam or got_pal != pal or got_hit != hit or not hidden:
            mismatches.append({"tick": t, "oam_ok": got_oam == oam, "pal_ok": got_pal == pal,
                               "hit": [got_hit, hit], "hidden_ok": hidden})
    return {"cycles": {"min": min(costs), "median": statistics.median(costs), "mean": statistics.mean(costs),
                       "max": max(costs)},
            "frame_fraction_median": statistics.median(costs) / FRAME_CYCLES,
            "correct": not mismatches, "mismatches": mismatches[:5],
            "rom_bytes": len(rom), "rom_sha256": hashlib.sha256(rom).hexdigest(),
            "static_ram_bytes": report["link_audit"]["static_ram_bytes"],
            "core_sha256": hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest()}


def slope(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    return sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x in xs)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--counts", default="0,2,5,8,11")
    p.add_argument("--ticks", type=positive_int, default=96)
    a = p.parse_args()
    try:
        if a.out.exists() and (not a.out.is_dir() or any(a.out.iterdir())):
            raise CompileError("benchmark output must be new or empty")
        fingerprints = tool_fingerprints(a.toolchain)
        counts = [int(c) for c in a.counts.split(",")]
        results = []
        for variant in VARIANTS:
            for n in counts:
                dest = a.out / f"{variant}-n{n}"
                build(dest, a.toolchain, variant, n)
                r = observe(dest, a.emulator, a.bios, n, a.ticks)
                r.update({"variant": variant, "actors_besides_player": n})
                results.append(r)
                print(variant, n, "correct=%s" % r["correct"], "median", r["cycles"]["median"],
                      "frame %.1f%%" % (100 * r["frame_fraction_median"]), "RAM", r["static_ram_bytes"],
                      "ROM", r["rom_bytes"])
        per_actor = {v: slope([(r["actors_besides_player"] + 1, r["cycles"]["median"])
                               for r in results if r["variant"] == v]) for v in VARIANTS}
        # Negative control: a corrupted ROM must be caught by the oracle.
        neg_dest = a.out / "negative-D-gbvm-n5"
        build(neg_dest, a.toolchain, "D-gbvm", 5, corrupt=True)
        negative = observe(neg_dest, a.emulator, a.bios, 5, 8)
        report = {"experiment": "s1-actors-hot-path", "frame_cycles": FRAME_CYCLES, "results": results,
                  "cycles_per_actor": per_actor, "negative_control_detected": not negative["correct"],
                  "tool_sha256": fingerprints,
                  "toolchain_unchanged": fingerprints == tool_fingerprints(a.toolchain),
                  "command": [sys.executable, "-B", "-m", "tools.bench_actors", *sys.argv[1:]],
                  "scope": "Actors hot path only (move stand-in, animation, off-screen test, render, "
                           "player overlap), free loop, handoff boot, silicon timing. No VM, scroll, "
                           "tilemap or scene-type physics."}
        write_report(a.out / "benchmark.json", report)
        print("cycles per actor:", {k: round(v, 1) for k, v in per_actor.items()})
        print("negative control detected:", report["negative_control_detected"])
        ok = all(r["correct"] for r in results) and report["negative_control_detected"]
        return 0 if ok and report["toolchain_unchanged"] else 1
    except (OSError, ValueError, CompileError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
