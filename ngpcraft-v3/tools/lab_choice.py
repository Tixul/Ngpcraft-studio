"""M4 EVENT_CHOICE: author events, native ROM, ui.c oracle.

Disposable project derived from gbs2 Sample Town: the scene script runs five
choices in a row (results in variables 21..25), driven by buttons on the
hardware port. The oracle is tools/gbvm_ui.Ui (ui.c with Game Boy semantics)
stepped as GBVM's textChoice does: overlay clear and move, instant text with
goto codes, the modal wait, then ui_run_menu (cursor tile, input read at the
start of each iteration, decision after its frame), the move out and its wait.
Compared: every pixel of the box on every frame, and every result value.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from ngpcraft.gbvm import text as t
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import get_palette, rgb444
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import default_font
from tools.check_text import page_bytes, ppm
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.gbvm_ui import Palette, Ui, box_pixels, load_font, rgb

UP, DOWN, LEFT, RIGHT, A, B = 0x04, 0x08, 0x02, 0x01, 0x10, 0x20      # GB bits (input.h)
PORT = {UP: 0x01, DOWN: 0x02, LEFT: 0x04, RIGHT: 0x08, A: 0x10, B: 0x20}  # NGPC port 0xB0
# (true text, false text, presses (frame, GB button)); results in variables 21..25.
CHOICES = [("Save Game", "Cancel", [(40, A)]),
           ("Yes", "No", [(100, DOWN), (110, A)]),
           ("New Game", "Continue", [(170, DOWN), (180, UP), (186, RIGHT), (195, A)]),
           ("Left", "Right", [(260, B)]),
           ("", "", [(330, A)])]
FRAMES = 400
MARK = 20


def project_case(root):
    root.mkdir(parents=True, exist_ok=True)
    project = root / "project"
    derive(GBS2, project, "sample_town", (12, 12), fade=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf-8"))
    script = []
    for k, (yes, no, _) in enumerate(CHOICES, 1):
        script += [ev(f"mark-{k}", "EVENT_SET_VALUE", variable=str(MARK), value=num(k)),
                   ev(f"choice-{k}", "EVENT_CHOICE", variable=str(20 + k), trueText=yes, falseText=no)]
    script.append(ev("mark-end", "EVENT_SET_VALUE", variable=str(MARK), value=num(len(CHOICES) + 1)))
    scene.update(name="M4 choice", script=script, autoFadeSpeed=None)
    write_json(path, scene)
    return project


def schedule():
    """[[start, end, port mask]]: each press held two frames."""
    return [[f, f + 2, PORT[b]] for _, _, presses in CHOICES for f, b in presses]


def choice_text(yes, no):
    """scriptBuilder.ts textChoice."""
    return (f"{t.text_code_set_speed(0)}{t.text_code_goto(3, 2)}{yes or 'Choice A'}\n"
            f"{t.text_code_goto(3, 3)}{no or 'Choice B'}")


def parse_init(asm):
    """The init thread's instructions from its assembly (GB Studio's text):
    one entry per VM instruction, with the .asciz / .MENUITEM data it owns."""
    from ngpcraft.gbvm.assembler import _Expr, _split_args, _asciz, load_model
    constants = load_model()["constants"]
    value = lambda text: _Expr(text, constants, "lab_choice").value()
    ops, started = [], False
    for line in asm.splitlines():
        code = line.split(";", 1)[0].rstrip()
        if code.endswith("::"):
            started = True
            continue
        if not started or not code.strip():
            continue
        m = re.fullmatch(r'        \.asciz "(.*)"', code)
        if m:
            ops[-1]["text"] = bytes(_asciz(m[1], "lab_choice")[:-1])
            continue
        m = re.fullmatch(r"            \.MENUITEM\s+(.*)", code)
        if m:
            ops[-1]["items"].append(tuple(int(v) for v in _split_args(m[1])))
            continue
        m = re.fullmatch(r"        ([A-Z][A-Z0-9_]*)(?:\s+(.*))?", code)
        if not m:
            raise ValueError(f"lab_choice: unexpected line {code!r}")
        args = _split_args(m[2] or "")
        op = {"name": m[1], "args": args}
        if m[1] == "VM_CHOICE":
            op.update(var=args[0], options=value(args[1]), count=int(args[2]), items=[])
        elif m[1] in ("VM_OVERLAY_CLEAR", "VM_OVERLAY_MOVE_TO", "VM_OVERLAY_WAIT", "VM_OVERLAY_SET_SCROLL",
                      "VM_OVERLAY_SHOW"):
            op["values"] = [value(a) for a in args]
        ops.append(op)
    return ops


QUANTUM = 17       # vm.c script_runner_update: counter INSTRUCTIONS_PER_QUANT (16), post-decremented


def simulate(asm, font, held, frames, make_ui=None, game_time=lambda k: 0):
    """States after the logic preceding each frame entry k, and each choice's
    (result, logic index where the script resumes), in check_text's frame model.
    The chronology is predicted, not measured: the init thread holds the VM
    lock, so script_runner_update ends the tick after QUANTUM instructions
    (RUNNER_IDLE), and that tick's core.c ui_update steps the window once;
    modal instructions (ui_run_modal, ui_run_menu) run whole frames inside.
    make_ui: the oracle's Ui (default: the default font only). game_time(k):
    the value ui_update reads in logic L(k) (GBVM increments it after
    ui_update, in core.c and ui_run_modal: the value at entry k - 1)."""
    ui = make_ui() if make_ui else Ui(font)
    states, results, normal = {}, [], []
    joy = 0

    def snap(k):
        states[k] = (ui.win_y, dict(ui.cells), dict(ui.tiles), ui.win_x)

    def read(k):
        nonlocal joy
        last = joy
        joy = held(k)
        return joy & ~last & 0xFF

    def modal(k, flags):
        pressed = read(k - 1)
        while k <= frames and not ui.wait_ok(flags, pressed):
            ui.update(game_time(k), pressed)
            snap(k)
            pressed = read(k)
            k += 1
        return k

    def menu(k, items, options, count):
        """ui_run_menu: the cursor is drawn, then each iteration reads the
        buttons (logic L(k) reads entry k - 1), runs one frame, then decides."""
        index = 1
        ui.cells[items[0][:2]] = (0xCB, 0)
        while k <= frames:
            pressed = read(k - 1)
            ui.update(game_time(k), pressed)
            snap(k)
            k += 1
            item = items[index - 1]
            if pressed & UP:
                nxt = item[4]
            elif pressed & DOWN:
                nxt = item[5]
            elif pressed & LEFT:
                nxt = item[2]
            elif pressed & RIGHT:
                nxt = item[3]
            elif pressed & A:
                return k, 0 if (index == count and options & 1) else index
            elif pressed & B and options & 2:
                return k, 0
            else:
                continue
            if not nxt:
                continue
            ui.cells[item[:2]] = (0xC4, 0)
            index = nxt
            ui.cells[items[index - 1][:2]] = (0xCB, 0)
        return k, None

    def normal_tick(k):
        """End of a tick: scene_input input_update, core.c ui_update, picture."""
        pressed = read(k - 1)
        ui.update(game_time(k), pressed)
        snap(k)
        normal.append(k)
        return k + 1

    snap(0)                              # before any logic: the window is closed
    k, counter, text = 1, QUANTUM, b""
    for op in parse_init(asm):
        if k > frames:
            break
        name = op["name"]
        if name == "VM_OVERLAY_CLEAR":
            x, y, w, h, color, options = op["values"]
            ui.overlay_clear(x, y, w, h, color, options)
        elif name == "VM_OVERLAY_MOVE_TO":
            x, y, speed = op["values"]
            ui.move_to(x, y, speed)
        elif name == "VM_OVERLAY_SET_SCROLL":
            ui.set_scroll(*op["values"][:4])
        elif name == "VM_LOAD_TEXT":
            text = op["text"]
        elif name == "VM_DISPLAY_TEXT":
            ui.display_text(text)
        elif name == "VM_OVERLAY_SHOW":
            ui.overlay_show(*op["values"])
        elif name == "VM_OVERLAY_WAIT":
            is_modal, flags = op["values"]
            if is_modal:
                k = modal(k, flags)
            else:
                # vm_overlay_wait non-modal: a failed check makes the context
                # waitable, which ends the tick (vm.c script_runner_update);
                # the next tick runs it again with a fresh quantum.
                if flags & ~1:
                    raise ValueError("lab_choice: non-modal wait on text or buttons not modelled")
                while k <= frames and not ui.wait_ok(flags, 0):
                    k = normal_tick(k)
                    counter = QUANTUM
        elif name == "VM_CHOICE":
            k, value = menu(k, op["items"], op["options"], op["count"])
            results.append((value, k))
        elif name not in ("VM_LOCK", "VM_SET_SPRITE_MODE", "VM_SET_CONST", "VM_SWITCH_TEXT_LAYER", "VM_STOP",
                          "VM_RESERVE", "VM_ACTOR_DEACTIVATE"):
            raise ValueError(f"lab_choice: {name} not modelled")
        counter -= 1
        if counter == 0 and k <= frames:
            k = normal_tick(k)
            counter = QUANTUM
    while k <= frames:
        k = normal_tick(k)
    return states, results, normal


def run(out, toolchain, emulator, bios, witness=None, *, project_factory=project_case,
        input_schedule=None, frames=FRAMES, expected_results=(1, 0, 1, 0, 1), report_name="choice.json"):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    project_path = project_factory(out)
    import ngpcraft.gbvm.emit as emit
    captured, assemble = [], emit.assemble_banks
    emit.assemble_banks = lambda texts, *a, **kw: (captured.append(list(texts)), assemble(texts, *a, **kw))[1]
    try:
        manifest = export_project(project_path, out / "export")
    finally:
        emit.assemble_banks = assemble
    asm = captured[0][0]                  # the scene's init thread (C10), GB Studio's text
    (out / "init.asm").write_text(asm, encoding="utf-8")
    if witness == "cancel-last":
        # Witness: the last item no longer returns 0 (MENU_CANCEL_LAST ignored).
        path = out / "export/src/gbvm.c"
        text = path.read_text(encoding="utf-8")
        old = "return ((current_index == count) && (options & MENU_CANCEL_LAST)) ? 0u : current_index;"
        if text.count(old) != 1:
            raise ValueError("witness anchor changed")
        import hashlib
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text.replace(old, "return current_index;"), encoding="utf-8", newline="")
        tmp.replace(path)
        manifest["sha256"]["src/gbvm.c"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(out / "export/export.json", manifest)
    elif witness is not None:
        raise ValueError("unknown witness")
    build_rom(out / "export", toolchain)
    write_json(out / "inputs.json", schedule() if input_schedule is None else input_schedule)
    probe = out / "probe"
    proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_gbvm", str(out / "export"), "--emulator", str(emulator),
                           "--bios", str(bios), "--out", str(probe), "--frames", str(frames),
                           "--inputs", str(out / "inputs.json"), "--duty-cycle", "0.25",
                           "--shots", ",".join(str(i) for i in range(frames + 1))],
                          capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=3600)
    if proc.returncode:
        raise ValueError(proc.stderr[-2000:])
    data = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    project = load_project(project_path)
    scene = project.scenes[0]
    rel, _ = default_font(project)
    font = load_font(project.root / PurePosixPath(rel).with_suffix(""))
    mapping = json.loads((project.root / PurePosixPath(rel).with_suffix("").with_suffix(".json"))
                         .read_text(encoding="utf-8")).get("mapping") or {}
    ids, defaults = scene.get("paletteIds") or [], project.settings["defaultBackgroundPaletteIds"]
    palette = Palette(project, tuple(rgb444(c) for c in get_palette(project, ids[7] if len(ids) > 7 else "",
                                                                     defaults[7], "oracle")))
    report = manifest["scene_engine"]
    top = report["view"]["top_band"]
    offset, bottom = (top if top else 8), top + report["view"]["height"]
    # Case B, or case A through the logical map (tall or moved window): the
    # screen shows the map of the previous logic, copied at the commit.
    committed_map = report["planes"]["case"] == "B" or report["planes"].get("text_logical_map", False)
    samples = data["input_samples"]

    def held(k):
        raw = samples[max(0, k)]
        return (raw & 0x30) | ((raw & 3) << 2) | ((raw & 4) >> 1) | ((raw & 8) >> 3)
    modal = data["modal_frames"]
    starts = [f for f in modal if f - 1 not in modal]
    states, results, normal = simulate(asm, font, held, frames)
    measured_normal = sorted(set(range(1, frames + 1)) - set(modal))
    wrong, compared = [], 0
    for k in range(1, frames + 1):
        win_y = states[k - 1][0]
        if offset + win_y >= bottom:
            continue
        ui = Ui(font)
        ui.cells, ui.tiles = states[k - 1 if committed_map else k][1], states[k][2]
        expected = box_pixels(ui, palette, win_y, offset, bottom, states[k - 1][3])
        w, _, pix = ppm(probe / f"shot-{k:04d}.ppm")
        bad = sum(1 for (x, y), c in expected.items()
                  if c is None or tuple(pix[(y * w + x) * 3:(y * w + x) * 3 + 3]) != rgb(c))
        compared += 1
        if bad:
            wrong.append({"frame": k, "pixels": bad})
    values = {}
    for i in range(len(expected_results)):
        name = f"VAR_VARIABLE_{21 + i}"
        found = [c for c in data["changes"].get(name, []) if c["frame"] > 0]
        values[21 + i] = found[-1] if found else None
    actual = [values[21 + i]["value"] if values[21 + i] else 0 for i in range(len(expected_results))]
    expected_values = [v for v, _ in results]
    result = {"witness": witness, "starts": starts, "frames": frames, "compared_frames": compared,
              "wrong_frames": wrong[:10], "wrong_count": len(wrong),
              "expected_results": expected_values, "actual_results": actual,
              "resume_logic": [k for _, k in results],
              "result_change_frames": [values[21 + i]["frame"] if values[21 + i] else None for i in range(len(expected_results))],
              "marker_end": data["changes"][f"VAR_VARIABLE_{MARK}"][-1],
              "features": manifest["features"], "case": report["planes"]["case"],
              "rom_sha256": data["rom_sha256"], "silicon_timing": data["silicon_timing"]}
    # Chronology: predicted normal ticks are the measured non-modal frame
    # entries; a nonzero result appears at the entry where the script resumes.
    result["normal_ticks_match"] = normal == measured_normal
    result["normal_ticks_predicted"] = [k for k in normal if k <= 360]
    result["result_frames_match"] = all(f == k for (v, k), f in zip(results, result["result_change_frames"]) if v)
    result["passed"] = bool(compared and not wrong and expected_values == actual and None not in expected_values
                            and expected_values == list(expected_results) and result["normal_ticks_match"]
                            and result["result_frames_match"])
    write_json(out / report_name, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + n, required=True)
    p.add_argument("--witness", choices=("cancel-last",))
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios, a.witness)
    print(json.dumps(r, indent=2))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
