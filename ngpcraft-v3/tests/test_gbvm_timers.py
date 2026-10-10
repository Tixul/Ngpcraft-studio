"""EVENT_SET_TIMER_SCRIPT, EVENT_TIMER_RESTART, EVENT_TIMER_DISABLE.

eventTimerScriptSet.js / scriptBuilder.ts timerScriptSet, GBVM events.c
timers_init / timers_update and vm_gameboy.c vm_timer_*, called as core.c
process_VM does: VM not locked, after the state update, every 16th frame.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gbs_fixture import GBS2, event, make_project, number
from ngpcraft.gbvm.builder import ScriptBuilder
from ngpcraft.gbvm.events import HANDLERS
from ngpcraft.pipeline import export_project

ROOT = Path(__file__).resolve().parents[1]
TOSHIBA = os.environ.get("NGC_TEST_TOSHIBA")
EMULATOR = os.environ.get("NGC_TEST_EMULATOR")
BIOS = os.environ.get("NGC_TEST_BIOS")


def branch(eid, command, children, **args):
    return {**event(eid, command, **args), "children": children}


class TimerCompileTests(unittest.TestCase):
    def interval(self, **args):
        with tempfile.TemporaryDirectory() as tmp:
            script = [branch("t", "EVENT_SET_TIMER_SCRIPT", {"script": []}, **args)]
            manifest = export_project(make_project(Path(tmp) / "project", script), Path(tmp) / "out")
        source = next(v["source"] for k, v in manifest["scripts"].items() if k.endswith("_init"))
        line = next(l for l in source.splitlines() if "VM_TIMER_SET" in l)
        return line.split()[-1]

    def test_interval_in_16_frame_ticks(self):
        # (frames / 16 + 0.5) | 0, clamped to 1..255; seconds: Math.ceil(duration * 60).
        self.assertEqual(self.interval(units="frames", frames=32), "2")
        self.assertEqual(self.interval(units="frames", frames=24), "2")       # 2.0 -> 2
        self.assertEqual(self.interval(units="frames", frames=23), "1")       # 1.94 -> 1
        self.assertEqual(self.interval(units="frames", frames=0), "1")
        self.assertEqual(self.interval(units="frames", frames=5000), "255")
        self.assertEqual(self.interval(units="frames"), "2")                  # missing frames: 30
        self.assertEqual(self.interval(units="time", duration=0.5), "2")      # 30 frames
        self.assertEqual(self.interval(), "38")                               # 10 s: 600 / 16 + 0.5

    def test_restart_and_disable_text(self):
        output = []
        sb = ScriptBuilder(output, {})
        HANDLERS["EVENT_TIMER_RESTART"]({"timer": 2}, sb)
        HANDLERS["EVENT_TIMER_DISABLE"]({}, sb)
        self.assertEqual([l.strip() for l in output if l.strip()],
                         ["; Timer Restart", "VM_TIMER_RESET          2", "; Timer Disable", "VM_TIMER_STOP           1"])


@unittest.skipUnless(TOSHIBA and EMULATOR and BIOS and GBS2.is_dir(), "set Toshiba/emulator/BIOS paths")
class TimerRomTests(unittest.TestCase):
    def run_rom(self, tmp, script, frames):
        from ngpcraft.toolchain import build_rom
        out = Path(tmp) / "out"
        manifest = export_project(make_project(Path(tmp) / "project", script), out)
        build = build_rom(out, TOSHIBA)
        probe = Path(tmp) / "probe"
        subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out), "--emulator", EMULATOR,
                        "--bios", BIOS, "--out", str(probe), "--frames", str(frames)],
                       capture_output=True, check=True, timeout=120)
        return manifest, build, json.loads((probe / "probe.json").read_text())["changes"]

    def test_period_and_disable(self):
        inc = lambda eid, v: event(eid, "EVENT_INC_VALUE", variable=v)
        # The timer script disables its timer at its second run. (A wait in the
        # scene init would not do: the init runs locked, VM_LOCK as in GB
        # Studio, and core.c updates timers only while the VM is not locked.)
        stop = branch("if", "EVENT_IF", {"true": [event("d", "EVENT_TIMER_DISABLE", timer=1)], "false": []},
                      condition={"type": "eq", "valueA": {"type": "variable", "value": "3"}, "valueB": number(2)})
        script = [branch("t", "EVENT_SET_TIMER_SCRIPT", {"script": [inc("i3", "3"), stop]}, timer=1,
                         units="frames", frames=32)]
        with tempfile.TemporaryDirectory() as tmp:
            manifest, build, changes = self.run_rom(tmp, script, 140)
        self.assertIn("timer_events", manifest["features"])
        self.assertIn("_timers_update", build["link_audit"]["public_symbols"])
        # Interval 2: remains counts down at game_time 0 and 16 (timers_update
        # every 16th frame), the started thread runs at the next tick, seen at
        # frame 18; then every 32 frames; no third run after the disable.
        self.assertEqual(changes["VAR_QUEST4"], [{"frame": 0, "value": 0}, {"frame": 18, "value": 1},
                                                 {"frame": 50, "value": 2}])

    def test_locked_init_freezes_timers(self):
        # A wait in the locked scene init: no timer run until it ends (frame 80).
        inc = lambda eid, v: event(eid, "EVENT_INC_VALUE", variable=v)
        script = [branch("t", "EVENT_SET_TIMER_SCRIPT", {"script": [inc("i3", "3")]}, timer=1, units="frames",
                         frames=32),
                  event("w", "EVENT_WAIT", units="frames", frames=number(80))]
        with tempfile.TemporaryDirectory() as tmp:
            _, _, changes = self.run_rom(tmp, script, 140)
        self.assertGreater(changes["VAR_QUEST4"][1]["frame"], 80)

    def test_scene_change_resets_timers(self):
        # Two scenes: in Sample Town a 16-frame timer counts v2 and switches to
        # Cave at its third run; core.c timers_init(FALSE) on the change: v2
        # stays 3 in Cave, which only counts its own arrival in v1.
        from ngpcraft.toolchain import build_rom
        from tools.demo_scene_switch import CAVE, CAVE_ARRIVAL, GBS2 as SOURCE, TOWN, TOWN_START
        from tools.demo_scene_switch import derive_scenes, ev, num, set_script, switch
        with tempfile.TemporaryDirectory() as tmp:
            keep, ids = derive_scenes(SOURCE, Path(tmp) / "project", [TOWN, CAVE], TOWN, TOWN_START)
            go = {**ev("go-if", "EVENT_IF", condition={"type": "eq", "valueA": {"type": "variable", "value": "2"},
                                                         "valueB": num(3)}, __disableElse=True),
                  "children": {"true": [switch("go", ids[CAVE], CAVE_ARRIVAL, "0")], "false": []}}
            set_script(keep[TOWN], [{**ev("t", "EVENT_SET_TIMER_SCRIPT", timer=1, units="frames", frames=16),
                                     "children": {"script": [ev("i2", "EVENT_INC_VALUE", variable="2"), go]}}])
            set_script(keep[CAVE], [ev("i1", "EVENT_INC_VALUE", variable="1")])
            out = Path(tmp) / "out"
            manifest = export_project(Path(tmp) / "project", out)
            build_rom(out, TOSHIBA)
            probe = Path(tmp) / "probe"
            subprocess.run([sys.executable, "-B", str(ROOT / "tools/probe_gbvm.py"), str(out), "--emulator",
                            EMULATOR, "--bios", BIOS, "--out", str(probe), "--frames", "200"],
                           capture_output=True, check=True, timeout=120)
            changes = json.loads((probe / "probe.json").read_text())["changes"]
        self.assertEqual(len(manifest["scene_engine"]["scenes"]), 2)
        self.assertIn("timer_events", manifest["features"])
        v1, v2 = "VAR_QUEST2", "VAR_QUEST3"        # gbs2 variables "1" and "2"
        self.assertEqual(sorted(manifest["variables"]), [v1, v2])
        self.assertEqual([c["value"] for c in changes[v2]], [0, 1, 2, 3])
        self.assertEqual([c["value"] for c in changes[v1]], [0, 1])
        # The Cave init runs in the tick of the change (same observed frame).
        self.assertGreaterEqual(changes[v1][1]["frame"], changes[v2][-1]["frame"])

    def test_no_timer_no_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, build, _ = self.run_rom(tmp, [event("s", "EVENT_SET_VALUE", variable="3", value=number(1))], 2)
        self.assertNotIn("timer_events", manifest["features"])
        self.assertNotIn("_timers_update", build["link_audit"]["public_symbols"])
        self.assertNotIn("vm_timer_set", manifest["gbvm"]["functions"])


if __name__ == "__main__":
    unittest.main()
