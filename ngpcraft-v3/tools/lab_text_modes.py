"""M4 EVENT_TEXT modes: non-modal box (closeWhen notModal), native ROM, ui.c oracle.

Disposable project derived from gbs2 Sample Town. The scene script opens a
non-modal dialogue, then loops: variable 20 += 1, wait one frame. The box
slides in and prints during ordinary ticks (core.c ui_update) while the
script keeps running: GB Studio's non-modal text.

Oracle: tools/gbvm_ui.Ui (ui.c with Game Boy semantics) driven by an
interpreter of the scene's init thread as assembled (GB Studio's text):
GBVM vm.c quantum (the init thread holds the VM lock: the tick ends after 17
instructions, or at VM_IDLE / VM_STOP), each ordinary tick ending with the
core's ui_update at the tick's game_time. Compared: every pixel of the box
on every frame, and variable 20 at every frame entry.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import get_palette, rgb444
from ngpcraft.toolchain import build_rom
from ngpcraft.ui_text import default_font
from tools.check_text import ppm
from tools.demo_actor_moves import GBS2, ev, num
from tools.demo_gbs_scene import derive, write_json
from tools.gbvm_ui import Palette, Ui, box_pixels, load_font, rgb

FRAMES = 160
COUNTER = 20
QUANTUM = 17       # vm.c script_runner_update: INSTRUCTIONS_PER_QUANT (16), post-decremented
PAGES = ["Non modal box\nstill printing", "Second page\nof the box"]


def project_case(root):
    root.mkdir(parents=True, exist_ok=True)
    project = root / "project"
    derive(GBS2, project, "sample_town", (12, 12), fade=False)
    path = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["script"] = [ev("text", "EVENT_TEXT", text=PAGES, closeWhen="notModal"),
                       {"command": "EVENT_LOOP", "args": {}, "id": "loop", "children": {"true": [
                           ev("inc", "EVENT_INC_VALUE", variable=str(COUNTER)),
                           ev("wait", "EVENT_WAIT", units="frames", frames=num(1))]}}]
    scene.update(name="M4 text modes", autoFadeSpeed=None)
    write_json(path, scene)
    return project


def parse(asm):
    """The init thread: instructions with their data lines, and labels."""
    from ngpcraft.gbvm.assembler import _Expr, _asciz, _split_args, load_model
    constants = load_model()["constants"]
    value = lambda text: _Expr(text, constants, "lab_text_modes").value()
    ops, labels, started = [], {}, False
    for line in asm.splitlines():
        code = line.split(";", 1)[0].rstrip()
        if code.endswith("::"):
            started = True
            continue
        if not started or not code.strip():
            continue
        m = re.fullmatch(r"(\d+)\$:", code)
        if m:
            labels[m[1]] = len(ops)
            continue
        m = re.fullmatch(r'        \.asciz "(.*)"', code)
        if m:
            ops[-1]["text"] = bytes(_asciz(m[1], "lab_text_modes")[:-1])
            continue
        m = re.fullmatch(r"            (\.R_\w+)(?:\s+(.*))?", code)
        if m:
            ops[-1]["rpn"].append((m[1], m[2]))
            continue
        m = re.fullmatch(r"        ([A-Z][A-Z0-9_]*)(?:\s+(.*))?", code)
        if not m:
            raise ValueError(f"lab_text_modes: unexpected line {code!r}")
        args = _split_args(m[2] or "")
        op = {"name": m[1], "args": args, "rpn": []}
        if m[1] in ("VM_OVERLAY_CLEAR", "VM_OVERLAY_MOVE_TO", "VM_OVERLAY_WAIT", "VM_OVERLAY_SET_SCROLL"):
            op["values"] = [value(a) for a in args]
        ops.append(op)
    return ops, labels


def simulate(asm, font, frames, variables):
    """States after the logic preceding each frame entry k (check_text's frame
    model) and the expected variables at each entry."""
    ui = Ui(font)
    states, memory, seen = {}, {}, {}
    ops, labels = parse(asm)

    def snap(k):
        states[k] = (ui.win_y, dict(ui.cells), dict(ui.tiles))
        seen[k] = dict(memory)

    def rpn(steps):
        stack = []
        for op, arg in steps:
            if op == ".R_REF":
                stack.append(memory.get(arg, 0))
            elif op == ".R_INT8":
                stack.append(int(arg))
            elif op == ".R_OPERATOR" and arg == ".ADD":
                b, a = stack.pop(), stack.pop()
                stack.append((a + b) & 0xFFFF)
            elif op == ".R_REF_SET":
                memory[arg] = stack.pop()
            elif op != ".R_STOP":
                raise ValueError(f"lab_text_modes: RPN {op} {arg} not modelled")

    snap(0)
    k, pc, counter, text, running = 1, 0, QUANTUM, b"", True
    while k <= frames:
        if running:
            op = ops[pc]
            name, values = op["name"], op.get("values")
            pc += 1
            if name == "VM_OVERLAY_CLEAR":
                ui.overlay_clear(*values)
            elif name == "VM_OVERLAY_MOVE_TO":
                ui.move_to(*values)
            elif name == "VM_OVERLAY_SET_SCROLL":
                ui.set_scroll(*values[:4])
            elif name == "VM_LOAD_TEXT":
                text = op["text"]
            elif name == "VM_DISPLAY_TEXT":
                ui.display_text(text)
            elif name == "VM_RPN":
                rpn(op["rpn"])
            elif name == "VM_JUMP":
                pc = labels[op["args"][0].rstrip("$")]
            elif name == "VM_STOP":
                running = False
            elif name not in ("VM_LOCK", "VM_RESERVE", "VM_SET_SPRITE_MODE", "VM_SWITCH_TEXT_LAYER", "VM_IDLE"):
                raise ValueError(f"lab_text_modes: {name} not modelled")
            counter -= 1
            if name not in ("VM_IDLE", "VM_STOP") and counter:
                continue
        # End of the tick: scene engine, then core.c ui_update at this tick's game_time.
        ui.update(k - 1, 0)
        snap(k)
        k += 1
        counter = QUANTUM
    return states, {k: {v: seen[k].get(f"VAR_VARIABLE_{v}", 0) for v in variables} for k in seen}


def run(out, toolchain, emulator, bios):
    out = Path(out)
    if out.exists():
        raise ValueError("output must be a new directory")
    project_path = project_case(out)
    import ngpcraft.gbvm.emit as emit
    captured, assemble = [], emit.assemble_bank
    emit.assemble_bank = lambda texts, *a, **kw: (captured.append(list(texts)), assemble(texts, *a, **kw))[1]
    try:
        manifest = export_project(project_path, out / "export")
    finally:
        emit.assemble_bank = assemble
    asm = captured[0][0]
    (out / "init.asm").write_text(asm, encoding="utf-8")
    build_rom(out / "export", toolchain)
    probe = out / "probe"
    proc = subprocess.run([sys.executable, "-B", "-m", "tools.probe_gbvm", str(out / "export"), "--emulator", str(emulator),
                           "--bios", str(bios), "--out", str(probe), "--frames", str(FRAMES), "--duty-cycle", "0.25",
                           "--shots", ",".join(str(i) for i in range(FRAMES + 1))],
                          capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=3600)
    if proc.returncode:
        raise ValueError(proc.stderr[-2000:])
    data = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    project = load_project(project_path)
    scene = project.scenes[0]
    rel, _ = default_font(project)
    font = load_font(project.root / PurePosixPath(rel).with_suffix(""))
    ids, defaults = scene.get("paletteIds") or [], project.settings["defaultBackgroundPaletteIds"]
    palette = Palette(project, tuple(rgb444(c) for c in get_palette(project, ids[7] if len(ids) > 7 else "",
                                                                     defaults[7], "oracle")))
    report = manifest["scene_engine"]
    top = report["view"]["top_band"]
    offset, bottom = (top if top else 8), top + report["view"]["height"]
    committed_map = report["planes"]["case"] == "B"
    states, expected_vars = simulate(asm, font, FRAMES, (COUNTER,))
    wrong, compared = [], 0
    for k in range(1, FRAMES + 1):
        win_y = states[k - 1][0]
        if offset + win_y >= bottom:
            continue
        ui = Ui(font)
        ui.cells, ui.tiles = states[k - 1 if committed_map else k][1], states[k][2]
        expected = box_pixels(ui, palette, win_y, offset, bottom)
        w, _, pix = ppm(probe / f"shot-{k:04d}.ppm")
        bad = sum(1 for (x, y), c in expected.items()
                  if c is None or tuple(pix[(y * w + x) * 3:(y * w + x) * 3 + 3]) != rgb(c))
        compared += 1
        if bad:
            wrong.append({"frame": k, "pixels": bad})
    # Variable 20 as the probe saw it at each frame entry ("changes" lists each new value).
    history = data["changes"][f"VAR_VARIABLE_{COUNTER}"]
    actual = {}
    for k in range(FRAMES + 1):
        actual[k] = [c["value"] for c in history if c["frame"] <= k][-1]
    var_errors = [{"frame": k, "rom": actual[k], "oracle": expected_vars[k][COUNTER]}
                  for k in range(1, FRAMES + 1) if actual[k] != expected_vars[k][COUNTER]]
    result = {"frames": FRAMES, "compared_frames": compared, "wrong_frames": wrong[:10], "wrong_count": len(wrong),
              "variable_errors": var_errors[:5], "variable_error_count": len(var_errors),
              "final_counter": actual[FRAMES], "modal_frames": len(data.get("modal_frames", [])),
              "features": manifest["features"], "case": report["planes"]["case"],
              "rom_sha256": data["rom_sha256"], "silicon_timing": data["silicon_timing"]}
    result["passed"] = bool(compared > 100 and not wrong and not var_errors and result["modal_frames"] == 0
                            and actual[FRAMES] > 100)
    write_json(out / "text_modes.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ("out", "toolchain", "emulator", "bios"):
        p.add_argument("--" + n, required=True)
    a = p.parse_args()
    r = run(a.out, a.toolchain, a.emulator, a.bios)
    print(json.dumps(r, indent=2))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
