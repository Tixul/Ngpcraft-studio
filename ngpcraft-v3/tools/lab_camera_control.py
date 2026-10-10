"""Native camera positions/locks and captures, without ROM instrumentation.

The independent move step follows released vm_camera.c, including its early
return when X and Y reach their destinations together. Game-tick observations
use the link map, the native silicon clock and noisy power-on RAM.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.music_rom import png
from tools.probe_rom import power_on_ram, verified_tick
from tools.reference import REFERENCE

MOVES = {2: (2816, 2432, 64), 4: (3328, 2944, 32), 5: (3328, 3328, 64),
         10: (2560, 2304, 128)}
# State at the last tick carrying each marker; short waits let every stable
# state be observed. Camera coordinates are centres in 1/32 pixel units.
ENDS = {1: (2560, 2304, 0), 2: (2816, 2432, 0), 3: (2816, 2432, 0),
        4: (3328, 2944, 0), 5: (3328, 3328, 0), 6: (3328, 3328, 5),
        7: (4096, 3328, 5), 8: (4096, 2816, 34), 9: (0, 0, 32),
        10: (2560, 2304, 0), 11: (2816, 2560, 0), 12: (2816, 2560, 0)}


def move_step(x, y, dest_x, dest_y, speed):
    """vm_camera.c axes (GBDK UWORD arithmetic), not a native runtime helper."""
    if (x, y) == (dest_x, dest_y):
        return x, y
    if x > dest_x:
        x = (x - speed) & 65535
        if x <= dest_x:
            x = dest_x
    elif x < dest_x:
        x = (x + speed) & 65535
        if x >= dest_x:
            x = dest_x
    if y > dest_y:
        y = (y - speed) & 65535
        if y <= dest_y:
            y = dest_y
    elif y < dest_y:
        y = (y + speed) & 65535
        if y >= dest_y:
            y = dest_y
    return x, y


def project_case(root):
    project = Path(root) / "project"
    derive(GBS2, project, "sample_town", (12, 12), False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf-8"))
    script = []
    def add(command, **args):
        script.append(ev(f"camera-{len(script)}", command, **args))
    def marker(k):
        add("EVENT_SET_VALUE", variable="20", value=num(k))
    def wait():
        add("EVENT_WAIT", units="frames", frames=num(10))
    def move(x, y, speed):
        add("EVENT_CAMERA_MOVE_TO", x=num(x), y=num(y), speed=speed, units="pixels")
    def position(x, y):
        add("EVENT_ACTOR_SET_POSITION", actorId="player", x=num(x), y=num(y), units="pixels")
    marker(1); move(0, 0, 0); wait()
    marker(2); move(8, 4, 2); wait()
    marker(3); move(8, 4, 1); wait()
    marker(4); move(24, 20, 1); wait()
    marker(5); move(16, 24, 0)
    add("EVENT_CAMERA_LOCK", speed=2, axis=["x", "y"], preventScroll=[]); wait()
    marker(6); add("EVENT_CAMERA_LOCK", speed=0, axis=["x"], preventScroll=["left"])
    position(88, 80); wait()
    marker(7); position(120, 80); wait()
    marker(8); add("EVENT_CAMERA_LOCK", speed=0, axis=["y"], preventScroll=["down"])
    position(128, 120); wait()
    marker(9); move(-80, -72, 0); wait()
    marker(10); move(0, 0, 4); wait()
    marker(11); move(8, 8, 0); wait()
    marker(12)
    scene.update(name="Camera move and lock", script=script, autoFadeSpeed=None)
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    project = project_case(out)
    manifest = export_project(project, out / "export")
    build = build_rom(out / "export", toolchain)
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    machine = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    machine.set_timing_silicon(word_wait=10, bios_wait=8)
    machine.reset(bios_handoff=True)
    power_on_ram(machine, "noise")
    symbols = build["link_audit"]["public_symbols"]
    base = symbols["_script_memory"]["address"]
    marker_addr = base + 2 * manifest["variables"]["VAR_VARIABLE_20"]
    addresses = [symbols[name]["address"] for name in ("_ngc_camera_x", "_ngc_camera_y", "_ngc_camera_settings")]
    machine.set_breakpoints([verified_tick(rom, build)])
    trace, last, bad, shots = [], {}, [], set()
    for frame in range(210):
        stop, _ = machine.run(15_000_000, record=False)
        if stop.stop_status != 40:
            raise ValueError("camera lab did not reach game tick")
        marker = int.from_bytes(machine.read(marker_addr, 2), "little")
        state = tuple(int.from_bytes(machine.read(a, 1 if i == 2 else 2), "little") for i, a in enumerate(addresses))
        trace.append({"frame": frame, "marker": marker, "state": state})
        if marker:
            last[marker] = state
        if len(trace) > 1 and marker in MOVES and trace[-2]["marker"] == marker:
            previous = trace[-2]["state"]
            expected = move_step(*previous[:2], *MOVES[marker])
            if state[:2] != expected:
                bad.append({"frame": frame, "marker": marker, "expected": expected, "actual": state[:2]})
        if marker in (1, 4, 7, 8, 11) and marker not in shots and trace[-2 if len(trace) > 1 else -1]["marker"] == marker:
            (out / f"camera-{marker}.png").write_bytes(png(machine.framebuffer()))
            shots.add(marker)
        if marker == 12:
            break
    mismatch = {k: {"expected": v, "actual": last.get(k)} for k, v in ENDS.items() if last.get(k) != v}
    report = {"schema": "ngpcraft-camera-control-lab/1", "passed": not bad and not mismatch,
              "step_mismatches": bad, "endpoint_mismatches": mismatch,
              "last_states": last, "trace": trace, "rom_sha256": hashlib.sha256(rom).hexdigest(),
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False,
              "core_sha256": hashlib.sha256(Path(machine._lib._name).read_bytes()).hexdigest(),
              "reference_sha256": hashlib.sha256((REFERENCE / "appData/engine/gbvm/src/core/vm_camera.c").read_bytes()).hexdigest()}
    write_json(out / "camera.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args()
    report = run(args.out, args.toolchain, args.emulator, args.bios)
    print(json.dumps({k: v for k, v in report.items() if k != "trace"}, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
