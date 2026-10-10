"""Menus (dialogue and lateral layouts) against the same released ui.c oracle as EVENT_CHOICE."""
import argparse
import json
from pathlib import Path

from tools.demo_actor_moves import ev, num
from tools.demo_gbs_scene import write_json
from tools import lab_choice as choice

# Eight items, a short second column, B disabled, B enabled, last-item
# cancellation, and blank-label defaults. All four navigation directions.
CASES = [
    (8, False, False, [(40, choice.RIGHT), (50, choice.DOWN), (60, choice.A)]),
    (5, False, False, [(150, choice.DOWN), (160, choice.RIGHT), (170, choice.A)]),
    (1, False, False, [(260, choice.B), (280, choice.A)]),
    (3, False, True, [(380, choice.B)]),
    (8, True, True, [(470, choice.RIGHT), (480, choice.DOWN), (490, choice.DOWN),
                    (500, choice.DOWN), (510, choice.A)]),
    (4, False, False, [(600, choice.RIGHT), (610, choice.UP), (620, choice.LEFT),
                     (630, choice.RIGHT), (640, choice.A)]),
]
EXPECTED = (6, 5, 1, 0, 0, 4)
# Lateral layout: one column, left = first item, right = last (textMenu menuItem).
# The 10-row window opens later than the dialogue box: the first case's RIGHT
# (frame 40) comes before the menu runs, so DOWN then A pick item 2.
EXPECTED_LATERAL = (2, 5, 1, 0, 0, 4)
FRAMES = 710


def project_case(root, layout="dialogue"):
    """Dialogue: Sample Town (case B, shared planes). Lateral: a window at
    x = 10 is case A only (docs/M4_OVERLAY.md), the gbs_fixture scene of
    tools/lab_overlay_wide.py."""
    if layout == "menu-on-b":
        # Test helper: the lateral layout on Sample Town (case B), refused.
        path, layout = choice.project_case(root), "menu"
    elif layout == "menu":
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
        from gbs_fixture import make_project
        path = make_project(Path(root) / "project", [])
    else:
        path = choice.project_case(root)
    resource = next((path / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(resource.read_text(encoding="utf-8"))
    script = []
    for k, (count, last, cancel_b, _) in enumerate(CASES, 1):
        labels = {f"option{i}": (f"Item{i}" if k != 6 else "") for i in range(1, count + 1)}
        script += [ev(f"mark-{k}", "EVENT_SET_VALUE", variable="20", value=num(k)),
                   ev(f"menu-{k}", "EVENT_MENU", variable=str(20 + k), items=count,
                      layout=layout, cancelOnLastOption=last, cancelOnB=cancel_b, **labels)]
    script.append(ev("mark-end", "EVENT_SET_VALUE", variable="20", value=num(7)))
    scene.update(name=f"{layout.capitalize()} menus 1 to 8", script=script)
    write_json(resource, scene)
    return path


def run(out, toolchain, emulator, bios, witness=None, layout="dialogue"):
    schedule = [[f, f + 2, choice.PORT[b]] for _, _, _, presses in CASES for f, b in presses]
    return choice.run(out, toolchain, emulator, bios, witness, project_factory=lambda root: project_case(root, layout),
                      input_schedule=schedule, frames=FRAMES,
                      expected_results=EXPECTED_LATERAL if layout == "menu" else EXPECTED,
                      report_name="menu.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--layout", choices=("dialogue", "menu"), default="dialogue")
    args = parser.parse_args()
    result = run(args.out, args.toolchain, args.emulator, args.bios, layout=args.layout)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
