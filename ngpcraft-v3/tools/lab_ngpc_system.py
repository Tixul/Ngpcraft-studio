"""NGPC system events and GBVM's clock in a native ROM (docs/M8_NGPC_SYSTEM.md).

The emulator's calendar chip is set to 2026-10-13 (Tuesday) 12:34:56 before
power on, its language to English or Japanese. One scene:
- EVENT_NGPC_CLOCK_GET (date and time) -> 50..56;
- EVENT_NGPC_BATTERY_GET -> 57 (the emulator's Battery_Voltage: 0x3FF);
- EVENT_NGPC_IF_LANGUAGE english -> 58 = 1, else 2;
- raw GBVM (EVENT_GBVM_SCRIPT): VM_RTC_LATCH / GET of the untouched timer
  (60..63: days since 1990-01-01 mod 512, hours, minutes, seconds); STOP,
  SET from 70..73 (300 days 23:59:10), LATCH / GET while stopped (64..67:
  exactly those); START, 120 frames, LATCH / GET (68 seconds: 12 or 13,
  69 days: 300);
- done flag 44, 10 frames, then EVENT_NGPC_SHUTDOWN: the game must stop ticking.
Witness (--witness): the same scene without the NGPC and clock events."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_gbs_scene import write_json
from tools.lab_actor_push import project_case as arena
from tools.lab_event_gaps import ev, num, set_value
from tools.probe_rom import power_on_ram, verified_tick

DONE = 44
CLOCK = {"year": 50, "month": 51, "day": 52, "weekday": 53, "hour": 54, "minute": 55, "second": 56}
BATTERY, LANGUAGE = 57, 58
SET_VALUES = {70: 300, 71: 23, 72: 59, 73: 10}
RAW_STOPPED = """VM_RTC_LATCH
VM_RTC_GET              VAR_60, .RTC_DAYS
VM_RTC_GET              VAR_61, .RTC_HOURS
VM_RTC_GET              VAR_62, .RTC_MINUTES
VM_RTC_GET              VAR_63, .RTC_SECONDS
VM_RTC_START            .RTC_STOP
VM_RTC_SET              VAR_70, .RTC_DAYS
VM_RTC_SET              VAR_71, .RTC_HOURS
VM_RTC_SET              VAR_72, .RTC_MINUTES
VM_RTC_SET              VAR_73, .RTC_SECONDS
VM_RTC_LATCH
VM_RTC_GET              VAR_64, .RTC_DAYS
VM_RTC_GET              VAR_65, .RTC_HOURS
VM_RTC_GET              VAR_66, .RTC_MINUTES
VM_RTC_GET              VAR_67, .RTC_SECONDS
VM_RTC_START            .RTC_START"""
RAW_RUNNING = """VM_RTC_LATCH
VM_RTC_GET              VAR_68, .RTC_SECONDS
VM_RTC_GET              VAR_69, .RTC_DAYS"""
WHEN = (2026, 10, 13, 2, 12, 34, 56)        # year, month, day, weekday (Tuesday), hour, minute, second


def _bcd(v):
    return ((v // 10) << 4) | (v % 10)


def expected(language):
    year, month, day, weekday, hour, minute, second = WHEN
    out = {CLOCK["year"]: year, CLOCK["month"]: month, CLOCK["day"]: day, CLOCK["weekday"]: weekday,
           CLOCK["hour"]: hour, CLOCK["minute"]: minute, CLOCK["second"]: second,
           BATTERY: 0x3FF, LANGUAGE: 1 if language == 1 else 2,
           60: (date(year, month, day) - date(1990, 1, 1)).days % 512, 61: hour, 62: minute, 63: second,
           64: 300, 65: 23, 66: 59, 67: 10, 69: 300}
    return out


def _raw(key, raw, variables):
    return {"command": "EVENT_GBVM_SCRIPT", "id": key,
            "args": {"script": raw, "references": [{"type": "variable", "id": str(n)} for n in variables]}}


def project_case(root, witness=False):
    project = arena(root)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf8"))
    for actor_path in path.parent.glob("actors/*.gbsres"):
        actor = json.loads(actor_path.read_text(encoding="utf8"))
        actor["startScript"], actor["updateScript"] = [], []
        write_json(actor_path, actor)
    script = [set_value(n, v) for n, v in SET_VALUES.items()]
    if not witness:
        script += [
            ev("clock", "EVENT_NGPC_CLOCK_GET", parts="datetime",
               **{"variable" + field.capitalize(): str(n) for field, n in CLOCK.items()}),
            ev("battery", "EVENT_NGPC_BATTERY_GET", variable=str(BATTERY)),
            {**ev("language", "EVENT_NGPC_IF_LANGUAGE", language="english"),
             "children": {"true": [set_value(LANGUAGE, 1)], "false": [set_value(LANGUAGE, 2)]}},
            _raw("stopped", RAW_STOPPED, range(60, 74)),
            ev("wait", "EVENT_WAIT", units="frames", frames=num(120)),
            _raw("running", RAW_RUNNING, (68, 69)),
        ]
    script.append(set_value(DONE, 1))
    # A few frames between the flag and the power off: the lab reads the flag at a tick.
    script.append(ev("settle", "EVENT_WAIT", units="frames", frames=num(10)))
    if not witness:
        script.append(ev("off", "EVENT_NGPC_SHUTDOWN"))
    scene["script"] = script
    write_json(path, scene)
    return project


def run(out, toolchain, emulator, bios, witness=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    manifest = export_project(project_case(out, witness), out / "export")
    build = build_rom(out / "export", toolchain)
    rom = (out / "export/rom/game.ngc").read_bytes()
    sys.path.insert(0, str(Path(emulator).resolve()))
    from core import native
    base = build["link_audit"]["public_symbols"]["_script_memory"]["address"]
    index = manifest["variables"]
    cases = []
    for language in (native.NativeMachine.LANGUAGE_ENGLISH, native.NativeMachine.LANGUAGE_JAPANESE):
        m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
        m.set_timing_silicon(word_wait=10, bios_wait=8)
        year, month, day, weekday, hour, minute, second = WHEN
        st = native.RtcState()
        st.enable, st.year, st.month, st.day = 1, _bcd(year % 100), _bcd(month), _bcd(day)
        st.hour, st.minute, st.second, st.weekday = _bcd(hour), _bcd(minute), _bcd(second), weekday
        m.set_language(language)
        m.reset(bios_handoff=True)
        # Hand-off mode: the reset seeds the chip (1998-01-01, measured); set it after.
        m.set_rtc(st)
        power_on_ram(m, "noise")
        m.set_breakpoints([verified_tick(rom, build)])

        def read(i):
            """None: a variable no script uses (the witness) is not compiled."""
            name = f"VAR_{i}" if f"VAR_{i}" in index else f"VAR_VARIABLE_{i}"
            if name not in index:
                return None
            return int.from_bytes(m.read(base + 2 * index[name], 2), "little", signed=True)
        for _ in range(600):
            stop, _ = m.run(15_000_000, record=False)
            if stop.stop_status != 40:
                raise ValueError("tick not reached")
            if read(DONE) == 1:
                break
        else:
            raise ValueError("system lab did not finish")
        want = expected(language)
        actual = {i: read(i) for i in sorted(set(want) | {68})}
        mismatches = {i: [v, actual[i]] for i, v in want.items() if v != actual[i]}
        if actual[68] not in (12, 13):
            mismatches[68] = ["12 or 13", actual[68]]
        # After the done flag the script switches the console off: no more game ticks.
        ticks = 0
        for _ in range(40):
            stop, _ = m.run(15_000_000, record=False)
            ticks += stop.stop_status == 40
        cases.append({"language": language, "actual": actual, "mismatches": mismatches,
                      "ticks_after_done": ticks})
    if witness:
        passed = all(c["mismatches"] for c in cases)
    else:
        passed = all(not c["mismatches"] and c["ticks_after_done"] <= 12 for c in cases)
    report = {"schema": "ngpcraft-ngpc-system-lab/1", "witness": witness, "when": WHEN, "cases": cases,
              "passed": passed, "warnings": manifest.get("warnings", []),
              "rom_sha256": hashlib.sha256(rom).hexdigest(), "silicon_timing": [10, 8], "ram": "noise",
              "console_validated": False}
    write_json(out / "comparison.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out")
    parser.add_argument("toolchain")
    parser.add_argument("emulator")
    parser.add_argument("bios")
    parser.add_argument("--witness", action="store_true")
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, args.witness)
    print(json.dumps({k: result[k] for k in ("passed", "cases")}, indent=1))
    sys.exit(0 if result["passed"] else 1)
