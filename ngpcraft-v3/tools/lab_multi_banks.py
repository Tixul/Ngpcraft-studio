"""Several script banks: one project, one bank or many, the same game.

docs/PLAN_MULTI_BANQUES.md É6. The project is exported twice: scripts in one
bank of 64 KB (the reference), then in banks of --bank-bytes bytes (a dozen
or more banks on an ordinary project, nearly every script outside bank 0,
far calls between banks). Both ROMs run on the native core (silicon timing,
noisy power-on RAM) with the same held buttons, and every game tick compares
the screen and every GBVM variable: the first difference is reported, with
both screens. A build whose script bytes moved is the same game or a bug.

Witnesses (--witness, repeatable) break the banked build on purpose and must
be detected (a difference, or a tick never reached):
  call-far-bank0  VM_CALL_FAR jumps into bank 0 instead of the callee's bank;
  ret-far-bank0   VM_RET_FAR returns into bank 0 instead of the caller's;
  entry-bank0     the engine's entry points (init, update, hit...) start bank 0;
  start-bank0     the instructions that start a script (call, thread, button,
                  timer, music routine) look for it in bank 0;
  table-shift     every script bank table of scene_actors.c rotated by one row.
A witness whose build fails is an error of the lab, never a detection.

  python -B tools/lab_multi_banks.py --project <dir> --out <new dir> --frames 900
         [--start <scene id> [--x X --y Y]] [--inputs run.json] [--bank-bytes 512]
         [--witness NAME ...] --toolchain C:/t900 --emulator <Ngpcraft_emulator> --bios <bios>

The user's project is copied, never modified. Writes result.json in --out.
"""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ngpcraft.compiler import CompileError  # noqa: E402
from ngpcraft.pipeline import export_project  # noqa: E402
from ngpcraft.toolchain import build_rom  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.probe_rom import power_on_ram, verified_tick, write_frame  # noqa: E402  (before the emulator's tools)

ONE_BANK = 0x10000

# (file, exact text of the banked export, broken text): each must change one place at least.
WITNESSES = {
    "call-far-bank0": [("src/gbvm.c", "vm_call_far(ctx, bank, (UBYTE *)(ngc_banks[bank] + a));",
                        "vm_call_far(ctx, 0u, (UBYTE *)(ngc_banks[0] + a));")],
    "ret-far-bank0": [("src/gbvm.c", "THIS->bank = (UBYTE)(*(THIS->stack_ptr));",
                       "THIS->bank = 0u;")],
    "entry-bank0": [("src/gbvm.c", "script_execute(bank, (UBYTE *)(ngc_banks[bank] + addr)",
                     "script_execute(0u, (UBYTE *)(ngc_banks[0] + addr)")],
    "start-bank0": [("src/gbvm.c", "(UBYTE *)(ngc_banks[bank] + a)", "(UBYTE *)(ngc_banks[0] + a)"),
                    ("src/gbvm.c", "(UBYTE *)(ngc_banks[bank] + b)", "(UBYTE *)(ngc_banks[0] + b)")],
}


def export(project, out, bank_bytes):
    with patch("ngpcraft.gbvm.emit.SCRIPT_BANK_BYTES", bank_bytes):
        manifest = export_project(project, out)
    banks = manifest["gbvm"].get("script_banks") or [sum(s["bytes"] for s in manifest["scripts"].values())]
    return manifest, banks


def rehash(export_dir, path):
    manifest_path = export_dir / "export.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"][path] = hashlib.sha256((export_dir / path).read_bytes()).hexdigest()
    write_json(manifest_path, manifest)


def break_build(export_dir, name):
    """Apply witness `name` to an export; returns what it changed."""
    changed = []
    if name == "table-shift":
        path = "src/scene_actors.c"
        text = (export_dir / path).read_text(encoding="utf-8")
        # scene_link.py prefixes each scene's statics (s5_act_update_bank).
        pattern = re.compile(r"(static const unsigned char ((?:s\d+_)?act_\w+_bank)\[(\d+)\] = \{\n)(.*?)(\n\};)",
                             re.S)

        def rotate(m):
            values = [v.strip() for v in m[4].replace("\n", " ").split(",") if v.strip()]
            if len(set(values)) < 2:
                return m[0]
            changed.append(m[2])
            return m[1] + "    " + ", ".join(values[1:] + values[:1]) + "," + m[5]
        text = pattern.sub(rotate, text)
        (export_dir / path).write_text(text, encoding="utf-8")
        if changed:
            rehash(export_dir, path)
        return changed
    for path, old, new in WITNESSES[name]:
        text = (export_dir / path).read_text(encoding="utf-8")
        count = text.count(old)
        if count:
            (export_dir / path).write_text(text.replace(old, new), encoding="utf-8")
            rehash(export_dir, path)
            changed.append(f"{path}: {count}x")
    return changed


class Run:
    """One ROM on the native core, observed at each frame: a game tick entry,
    or with text the entry of a modal frame (a dialogue runs whole frames
    inside one tick, GBVM ui_run_modal), as tools/probe_gbvm.py counts them."""

    def __init__(self, export_dir, emulator, bios):
        rom = (export_dir / "rom/game.ngc").read_bytes()
        build = json.loads((export_dir / "rom/build-report.json").read_text(encoding="utf-8"))
        manifest = json.loads((export_dir / "export.json").read_text(encoding="utf-8"))
        self.sym = build["link_audit"]["public_symbols"]
        self.variables = len(manifest["variables"])
        tick = verified_tick(rom, build)
        entries = [tick]
        if "text" in manifest["features"]:
            entries.append(self.sym["_ngc_ui_modal_commit"]["address"])
        self.modal = entries[1] if len(entries) > 1 else None
        # Work of a tick: cycles from its entry to ngc_vm_frame_end (the wait
        # for the next vertical blank), ticks without a modal frame only.
        self.frame_end = self.sym["_ngc_vm_frame_end"]["address"] if "_ngc_vm_frame_end" in self.sym else None
        self.work, self.after_tick = [], False
        if str(emulator) not in sys.path:
            sys.path.insert(0, str(emulator))
        from core import native
        self.m = native.NativeMachine(rom, bios=bios.read_bytes())
        self.m.set_timing_silicon(word_wait=10, bios_wait=8)
        self.m.reset(bios_handoff=True)
        power_on_ram(self.m, "noise")
        self.m.set_breakpoints(entries + ([self.frame_end] if self.frame_end is not None else []))

    def tick(self, held):
        while True:
            stop, _ = self.m.run(3_000_000, record=False)
            if stop.stop_status != 40:
                return None
            if stop.stop_pc != self.frame_end:
                break
            if self.after_tick:
                self.work.append(stop.total_cycles)
            self.after_tick = False
        modal = self.modal is not None and stop.stop_pc == self.modal
        self.after_tick = not modal
        self.m.write(0x00B0, bytes([held]))
        pixels = self.m.framebuffer()
        memory = bytes(self.m.read(self.sym["_script_memory"]["address"], 2 * self.variables))
        scene = self.m.read(self.sym["_ngc_scene"]["address"], 1)[0] if "_ngc_scene" in self.sym else 0
        return hashlib.sha256(array("H", pixels).tobytes()).hexdigest(), memory, (scene, modal), pixels


def held_at(inputs, frame):
    mask = 0
    for start, end, bits in inputs:
        if start <= frame < end:
            mask |= bits
    return mask


def compare(reference, other, frames, inputs, out, emulator, bios):
    a, b = Run(reference, emulator, bios), Run(other, emulator, bios)
    scenes = set()
    changes = modal_frames = 0
    previous = None
    for frame in range(frames):
        held = held_at(inputs, frame)
        x, y = a.tick(held), b.tick(held)
        if x is None:
            raise SystemExit(f"reference: frame {frame} not reached")
        if y is None:
            return {"identical": False, "frame": frame, "reason": "frame not reached"}
        scenes.add(x[2][0])
        modal_frames += x[2][1]
        changes += previous is not None and previous != x[1]
        previous = x[1]
        if x[:3] != y[:3]:
            out.mkdir(parents=True, exist_ok=True)
            write_frame(out / f"diff-{frame:05d}-one.ppm", x[3])
            write_frame(out / f"diff-{frame:05d}-banks.ppm", y[3])
            what = [k for k, i in (("screen", 0), ("variables", 1), ("scene or modal frame", 2)) if x[i] != y[i]]
            return {"identical": False, "frame": frame, "reason": ", ".join(what)}
        if frame % 300 == 0 or frame == frames - 1:
            out.mkdir(parents=True, exist_ok=True)
            write_frame(out / f"tick-{frame:05d}.ppm", x[3])
    return {"identical": True, "frames": frames, "modal_frames": modal_frames, "scenes_seen": sorted(scenes),
            "variable_changes": changes, "work_cycles": {"one": work(a.work), "banks": work(b.work)}}


def work(cycles):
    """A tick's work in cycles (515 per line, 199 lines per frame)."""
    if not cycles:
        return None
    ordered = sorted(cycles)
    return {"ticks": len(cycles), "median": ordered[len(ordered) // 2], "max": ordered[-1], "total": sum(cycles)}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("project", "out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--start")
    p.add_argument("--x", type=int)
    p.add_argument("--y", type=int)
    p.add_argument("--frames", type=int, default=900)
    p.add_argument("--inputs", type=Path)
    p.add_argument("--bank-bytes", type=int, default=512)
    p.add_argument("--witness", action="append", default=[], choices=(*WITNESSES, "table-shift"))
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out}: output must be new")
    project = a.out / "project"
    shutil.copytree(a.project, project, ignore=shutil.ignore_patterns("build", "build_export"))
    if a.start:
        settings_path = project / "project/settings.gbsres"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings["startSceneId"] = a.start
        if a.x is not None:
            settings.update(startX=a.x, startY=a.y)
        write_json(settings_path, settings)
    inputs = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else []
    result = {"project": str(a.project), "start": a.start, "frames": a.frames, "inputs": inputs,
              "bank_bytes": a.bank_bytes}
    _, one = export(project, a.out / "one/export", ONE_BANK)
    manifest, many = export(project, a.out / "banks/export", a.bank_bytes)
    if len(one) != 1 or len(many) < 2:
        raise SystemExit(f"expected one bank then several, got {len(one)} and {len(many)}")
    placed = [s["bank"] for s in manifest["scripts"].values()]
    result["banks"] = {"one": one, "several": many,
                       "scripts": len(placed), "scripts_outside_bank_0": sum(b > 0 for b in placed)}
    build_rom(a.out / "one/export", a.toolchain)
    build_rom(a.out / "banks/export", a.toolchain)
    result["rom_bytes"] = {k: (a.out / k / "export/rom/game.ngc").stat().st_size for k in ("one", "banks")}
    result["several_banks"] = compare(a.out / "one/export", a.out / "banks/export", a.frames, inputs,
                                      a.out / "shots", a.emulator, a.bios)
    result["witnesses"] = {}
    for name in a.witness:
        target = a.out / f"witness-{name}/export"
        shutil.copytree(a.out / "banks/export", target, ignore=shutil.ignore_patterns("rom", "objects"))
        changed = break_build(target, name)
        if not changed:
            result["witnesses"][name] = {"applied": False}
            continue
        try:
            build_rom(target, a.toolchain)
        except CompileError as exc:
            result["witnesses"][name] = {"applied": changed, "detected": False, "error": f"build: {exc}"[:300]}
            continue
        seen = compare(a.out / "one/export", target, a.frames, inputs, a.out / f"witness-{name}/shots",
                       a.emulator, a.bios)
        result["witnesses"][name] = {"applied": changed, "detected": not seen["identical"], **seen}
    write_json(a.out / "result.json", result)
    print(json.dumps({k: result[k] for k in ("banks", "rom_bytes", "several_banks", "witnesses")}, indent=1))
    ok = result["several_banks"]["identical"] and all(
        w.get("detected") for w in result["witnesses"].values())
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
