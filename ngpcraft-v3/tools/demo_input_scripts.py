"""Input scripts (EVENT_SET_INPUT_SCRIPT, EVENT_REMOVE_INPUT_SCRIPT) on the
topdown sample_town of tools.demo_topdown: laboratory project and ROM proof.

Scene init (locked: input scripts only fire once it has ended, core.c):
  Start                      -> VAR 2 += 1
  A   (override, default)    -> VAR 3 += 1, wait 10 frames   (A is also the
                                topdown interaction button: overridden)
  Up  (override)             -> VAR 5 += 1
  B   (no override)          -> VAR 4 += 1, remove the A script
The angry neighbour's interaction (demo_topdown) adds 1 to VAR 0.

Buttons (Sys Lever bits, tools.gbvm_topdown): right tap to stand by the
neighbour; Option short (Start), Option long (Select: no script); A held;
Up held; B held (removes A); A tapped again (now the interaction); Up while
the interaction holds the VM lock (core.c: no input script while locked).
"""
import argparse
import hashlib
import json
from pathlib import Path

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_actor_moves import ev, num
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import write_json
from tools.demo_topdown import ANGRY, project_case as topdown_project

RIGHT, UP, A, B, OPTION = 8, 1, 16, 32, 64
SCHEDULE = [(20, 22, RIGHT),            # step next to the neighbour, facing it
            (40, 50, OPTION),           # short: Start
            (60, 140, OPTION),          # long: Select (no script attached)
            (160, 220, A),              # A held: overridden, repeats when its script ends
            (240, 250, UP),             # Up held: overridden, the player does not move
            (270, 330, B),              # B held: once (no override), removes the A script
            (350, 352, A),              # A tapped: the topdown interaction again
            (360, 370, UP)]             # Up while the interaction holds the VM lock: nothing
FRAMES = 420


def inc(eid, variable):
    return ev(eid, "EVENT_INC_VALUE", variable=variable)


def input_script(eid, buttons, body, override=None):
    event = ev(eid, "EVENT_SET_INPUT_SCRIPT", input=buttons, **({} if override is None else {"override": override}))
    event["children"] = {"true": body}
    return event


def scene_script():
    return [input_script("start", ["start"], [inc("s1", "2")]),
            input_script("a", ["a"], [inc("a1", "3"), ev("a2", "EVENT_WAIT", units="frames", frames=num(10))]),
            input_script("up", ["up"], [inc("u1", "5")], override=True),
            input_script("b", ["b"], [inc("b1", "4"), ev("b2", "EVENT_REMOVE_INPUT_SCRIPT", input=["a"])],
                         override=False)]


def console_script():
    """Console version: every button has a visible effect on the angry
    neighbour, the player's right-hand neighbour at start (demo_topdown)."""
    def put(eid, x):
        return ev(eid, "EVENT_ACTOR_SET_POSITION", actorId=ANGRY, x=num(x), y=num(20), units="tiles")
    return [input_script("start", ["start"], [ev("s1", "EVENT_ACTOR_HIDE", actorId=ANGRY), inc("s2", "2")]),
            input_script("select", ["select"], [ev("l1", "EVENT_ACTOR_SHOW", actorId=ANGRY), inc("l2", "3")]),
            input_script("a", ["a"], [put("a1", 17), inc("a2", "4")]),
            input_script("b", ["b"], [put("b1", 15), inc("b2", "5")])]


# Console reference: Option short, Option long, A, B; captures between them.
CONSOLE_SCHEDULE = [(30, 40, OPTION), (90, 170, OPTION), (200, 205, A), (250, 255, B)]
CONSOLE_SHOTS = (20, 60, 175, 220, 270)
CONSOLE_CAPTIONS = ("start", "Option bref : voisin cache", "Option tenu 1 s : voisin revenu",
                    "A : voisin a droite", "B : voisin a sa place")


def project_case(root, console=False):
    project = topdown_project(root)
    scene_file = next((project / "project/scenes").rglob("scene.gbsres"))
    scene = json.loads(scene_file.read_text(encoding="utf-8"))
    scene["script"] = console_script() if console else scene_script()
    write_json(scene_file, scene)
    return project


def build_case(root, toolchain, mutate=None, console=False):
    manifest = export_project(project_case(root, console), root / "export")
    if mutate:
        rel, old, new = mutate
        path = root / "export" / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (rel, old, text.count(old))
        text = text.replace(old, new)
        write_new(path.with_suffix(".mutated"), text.encode())     # bytes: no newline translation
        path.with_suffix(".mutated").replace(path)
        manifest["sha256"][rel] = hashlib.sha256(text.encode()).hexdigest()
        write_json(root / "export/export.json", manifest)
    build = build_rom(root / "export", toolchain)
    write_json(root / "inputs.json", [list(s) for s in (CONSOLE_SCHEDULE if console else SCHEDULE)])
    return manifest, build


def reference_sheet(probe_dir, out):
    """phases_reference.png: the captures side by side, 2x."""
    from PIL import Image
    shots = [Image.open(probe_dir / f"shot-{t:04d}.ppm").convert("RGB") for t in CONSOLE_SHOTS]
    w, h = shots[0].size
    sheet = Image.new("RGB", (len(shots) * (w * 2 + 4), h * 2), (40, 40, 40))
    for k, img in enumerate(shots):
        sheet.paste(img.resize((w * 2, h * 2), Image.NEAREST), (k * (w * 2 + 4), 0))
    sheet.save(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--console", action="store_true", help="visible effects, console ROM name and reference sheet")
    p.add_argument("--emulator", type=Path, help="with --console: capture the reference sheet")
    p.add_argument("--bios", type=Path)
    a = p.parse_args()
    if a.out.exists():
        p.error("output must be new")
    a.out.mkdir(parents=True)
    manifest, build = build_case(a.out, a.toolchain, console=a.console)
    rom = a.out / "export/rom/game.ngc"
    if a.console:
        rom = a.out / "ngpcraft_m2_input_option_buttons.ngc"
        write_new(rom, (a.out / "export/rom/game.ngc").read_bytes())
        if a.emulator:
            import subprocess
            import sys
            subprocess.run([sys.executable, "-B", "tools/probe_gbvm.py", str(a.out / "export"),
                            "--emulator", str(a.emulator), "--bios", str(a.bios), "--out", str(a.out / "probe"),
                            "--frames", "300", "--inputs", str(a.out / "inputs.json"),
                            "--shots", ",".join(map(str, CONSOLE_SHOTS))], check=True,
                           cwd=Path(__file__).resolve().parents[1])
            reference_sheet(a.out / "probe", a.out / "phases_reference.png")
    print(json.dumps({"rom": str(rom), "bytes": build["header"]["size_bytes"],
                      "sha256": hashlib.sha256(rom.read_bytes()).hexdigest(), "features": manifest["features"]}))


if __name__ == "__main__":
    main()
