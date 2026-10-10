"""Converted sound effect files in a native ROM (docs/M7_SFX_CONVERT.md).

gbs2 "Cave" alone, actors removed, player hidden. Its FX Hammer bank
(Tronimal_Sound_Effects.sav) and one corpus VGM (copied in with a sound
resource) are converted by `convert-sfx` and bound in ngpcraft.json; the
scene script plays bank effects 0, 5, 8 and the VGM, each followed by a long
enough Wait. Every frame the T6W28 state (tone period and volume of the effect
voice, noise rate/volume) is read from the core (no probe in the ROM) and the
sequence of states must contain each converted timeline, frame for frame.
Witness (--witness): one binding changed after conversion (one row's tone
divider): its effect must no longer match.

  python -B -m tools.lab_sfx_convert --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> --vgm <a .vgm> [--witness]
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.project import SCHEMA, SIDECAR
from ngpcraft.sfx_convert import convert
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.probe_rom import power_on_ram, verified_tick

BANK = "ca718340-6862-49ef-b052-0a086eda78f4"
VGM_ID = "7d6e1f0a-0c3b-4c69-9d6e-5f00c0ffee01"
EFFECTS = (0, 5, 8)
WAIT = 260


def project_case(root, vgm, witness=False):
    derive(GBS2, root / "project", "cave", (9, 12), fade=False)
    project = root / "project"
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    for sub in ("actors", "triggers"):
        if (path.parent / sub).is_dir():
            shutil.rmtree(path.parent / sub)
    shutil.copy(vgm, project / "assets/sounds/lab_effect.vgm")
    write_json(project / "assets/sounds/lab_effect.vgm.gbsres",
               {"_resourceType": "sound", "id": VGM_ID, "name": "lab_effect.vgm", "symbol": "sound_lab_effect",
                "type": "vgm", "filename": "lab_effect.vgm"})
    scene = json.loads(path.read_text(encoding="utf-8"))
    script = [ev("hide", "EVENT_ACTOR_HIDE", actorId="player")]
    plays = [(BANK, e) for e in EFFECTS] + [(VGM_ID, 0)]
    for k, (sound, effect) in enumerate(plays):
        script += [ev(f"play-{k}", "EVENT_SOUND_PLAY_EFFECT", type=sound, effect=effect, priority="high"),
                   ev(f"wait-{k}", "EVENT_WAIT", units="frames", frames=num(WAIT))]
    scene["script"] = script
    write_json(path, scene)
    bindings, conversions = {}, {}
    for sound, effect in plays:
        source = project / ("assets/sounds/Tronimal_Sound_Effects.sav" if sound == BANK
                            else "assets/sounds/lab_effect.vgm")
        result = convert(source, effect)
        bindings[f"{sound}:{effect}"] = [dict(r) for r in result["rows"]]
        conversions[f"{sound}:{effect}"] = result
    if witness:
        rows = bindings[f"{BANK}:{EFFECTS[1]}"]
        audible = next(r for r in rows if r["tone_attenuation"] < 15)
        audible["tone_divider"] = audible["tone_divider"] + 40 if audible["tone_divider"] < 900 else 100
    write_json(project / SIDECAR, {"schema": SCHEMA, "ngpc": {"native_sfx": bindings}})
    return project, plays, bindings, conversions


def expand(rows):
    """One (tone divider or 0, tone attenuation, noise, noise attenuation) per frame;
    a silent voice compares as silent whatever its other fields."""
    out = []
    for r in rows:
        tone = (r["tone_divider"], r["tone_attenuation"]) if r["tone_attenuation"] < 15 else (0, 15)
        noise = (r["noise"], r["noise_attenuation"]) if r["noise_attenuation"] < 15 else (0, 15)
        out += [tone + noise] * r["frames"]
    return out


def compare(seen, plays, bindings):
    """Each effect, in play order, must appear frame for frame in the PSG
    states: tone = (period / 16, volume of voice 1), noise = (rate | white << 2,
    noise volume). Volumes are the core's amplitudes: the attenuation -> volume
    table is learnt from the run and must be one to one."""
    states = []
    for period, left, right, rate, tap, nleft, nright in seen:
        tone = (period[1] // 16, left[1]) if left[1] else (0, 0)
        noise = (rate | (4 if tap != 16 else 0), nleft) if nleft else (0, 0)
        states.append(tone + noise)
    table, results, at = {}, {}, 0
    for sound, effect in plays:
        k = f"{sound}:{effect}"
        want = expand(bindings[k])
        # The effect starts at the first frame showing its first audible state.
        first = next(i for i, w in enumerate(want) if w[1] < 15 or w[3] < 15)
        start = None
        for i in range(at, len(states)):
            s, w = states[i], want[first]
            if (w[1] < 15 and s[0] == w[0]) or (w[1] == 15 and w[3] < 15 and s[2] == w[2] and s[3]):
                start = i - first
                break
        if start is None:
            results[k] = {"matched": False, "reason": "start not found"}
            continue
        wrong = []
        for j, w in enumerate(want):
            s = states[start + j] if start + j < len(states) else None
            ok = s is not None
            if ok and w[1] < 15:
                ok = s[0] == w[0] and table.setdefault(("v", w[1]), s[1]) == s[1]
            elif ok:
                ok = s[1] == 0
            if ok and w[3] < 15:
                ok = s[2] == w[2] and table.setdefault(("v", w[3]), s[3]) == s[3]
            elif ok:
                ok = s[3] == 0
            if not ok:
                wrong.append(j)
        results[k] = {"matched": not wrong, "frames": len(want), "start_frame": start,
                      "wrong_frames": len(wrong), "first_wrong": wrong[:3]}
        at = start + len(want)
    one_to_one = len(set(table.values())) == len(table)
    return {"compared": results, "attenuation_to_volume": {str(k[1]): v for k, v in sorted(table.items())},
            "table_one_to_one": one_to_one,
            "passed": one_to_one and all(r.get("matched") for r in results.values())}


def run(out, toolchain, emulator, bios, vgm, witness=False, frames=None):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be new")
    out.mkdir(parents=True)
    project, plays, bindings, conversions = project_case(out, vgm, witness)
    manifest = export_project(project, out / "export")
    build = build_rom(out / "export", Path(toolchain))
    rom = (out / "export/rom/game.ngc").read_bytes()
    emulator = Path(emulator).resolve()
    sys.path.insert(0, str(emulator))
    from core import native
    if not Path(native.__file__).resolve().is_relative_to(emulator):
        raise ValueError("different native binding")
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    tick = verified_tick(rom, build)
    m.set_breakpoints([tick])
    seen = []
    for _ in range(frames or (len(plays) * WAIT + 120)):
        stop, _ = m.run(3_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError(f"tick not reached ({stop.stop_status})")
        a = m.apu_state()
        seen.append((list(a.square_period), list(a.square_vol_left), list(a.square_vol_right),
                     a.noise_period_select, a.noise_tap, a.noise_vol_left, a.noise_vol_right))
        m.set_breakpoints([])
        m.run(1, record=False)
        m.set_breakpoints([tick])
    write_json(out / "psg-frames.json", seen)
    # Expected: what convert-sfx gives, not what the project binds (witness).
    compared = compare(seen, plays, {k: c["rows"] for k, c in conversions.items()})
    report = {"schema": "ngpcraft-sfx-convert-lab/1", "witness": witness, **compared,
              "effects": {k: {"frames": c["frames"], "rows": len(c["rows"]), "losses": c["losses"]}
                          for k, c in conversions.items()},
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "features": manifest["features"],
              "core_sha256": hashlib.sha256(Path(m._lib._name).read_bytes()).hexdigest(),
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False}
    write_json(out / "comparison.json", report)
    return report, seen, bindings


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("out", "toolchain", "emulator", "bios", "vgm"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--witness", action="store_true")
    a = p.parse_args()
    report, _, _ = run(a.out, a.toolchain, a.emulator, a.bios, a.vgm, a.witness)
    print(json.dumps(report, indent=2))
    return int(report["passed"] == a.witness)


if __name__ == "__main__":
    raise SystemExit(main())
