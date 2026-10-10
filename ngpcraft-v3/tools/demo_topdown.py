"""GB Studio topdown scene controlled by real NGPC buttons, and its ROM oracle."""
import argparse
import hashlib
import json
from pathlib import Path

from ngpcraft.background_stream import view_of
from ngpcraft.pipeline import export_project
from ngpcraft.project import load_project
from ngpcraft.scene_assets import background_pixels, sprite_palettes
from ngpcraft.scene_build import build_engine_scene
from ngpcraft.toolchain import build_rom
from tools.demo_backgrounds import write_new
from tools.demo_gbs_scene import derive, write_json
from tools.demo_actor_moves import ev, num, GBS2, ANGRY
from tools.gbvm_actors import ScriptScheduler, plain, op_wait, op_marker, engine_frames
from tools.gbvm_topdown import Topdown, held_at

SCHEDULE = [(20, 22, 8), (45, 75, 8), (90, 150, 16), (100, 110, 8),
            (170, 190, 32), (220, 230, 16), (280, 320, 1), (310, 335, 4),
            (345, 365, 2), (380, 410, 8), (395, 425, 2), (440, 470, 4),
            (490, 510, 3), (530, 550, 12)]


def project_case(root, grid=8, pause=20, button="INPUT_A", speed=1, long_press=None):
    derive(GBS2, root / "project", "sample_town", (12, 20), fade=False)
    sf = next((root / "project/project/scenes").rglob("scene.gbsres"))
    scene = json.loads(sf.read_text(encoding="utf-8"))
    scene["script"] = [ev("init", "EVENT_WAIT", units="frames", frames=num(12))]
    write_json(sf, scene)
    path = root / "project/project/settings.gbsres"
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["startDirection"], settings["startMoveSpeed"] = "right", speed
    write_json(path, settings)
    path = root / "project/project/engine_field_values.gbsres"
    fields = json.loads(path.read_text(encoding="utf-8"))
    fields["engineFieldValues"] += [{"id": "topdown_grid", "value": grid},
                                    {"id": "INPUT_TOPDOWN_INTERACT", "value": button}]
    write_json(path, fields)
    for path in (root / "project/project/scenes").rglob("actors/*.gbsres"):
        actor = json.loads(path.read_text(encoding="utf-8"))
        if actor["id"] != ANGRY:
            continue
        actor["x"], actor["y"] = 15, 21 if grid == 16 else 20
        actor["script"] = [ev("count", "EVENT_INC_VALUE", variable="0"),
                           ev("hide", "EVENT_ACTOR_HIDE", actorId="$self$"),
                           ev("wait", "EVENT_WAIT", units="frames", frames=num(pause)),
                           ev("show", "EVENT_ACTOR_SHOW", actorId="$self$")]
        write_json(path, actor)
    if long_press is not None:      # ngpcraft.json ngpc.option_long_press (ECARTS 2, A5)
        write_json(root / "project/ngpcraft.json",
                   {"schema": "ngpcraft-project/1", "ngpc": {"option_long_press": long_press}})
    return root / "project"


def expected(engine, scene, ticks, schedule, grid, pause, mask, long_press="select"):
    actor_index = next(i + 1 for i, actor in enumerate(scene["actors"]) if actor["id"] == ANGRY)
    scheduler = ScriptScheduler(plain(4) + [op_wait(12)], {})
    def flags(hidden):
        def run(m, s, actors, tick, thread):
            if hidden:
                actors[actor_index].flags |= 2
            else:
                actors[actor_index].flags &= ~2
            return False, False
        return run
    def interact():
        # LOCK, RESERVE, INC_VALUE (RPN), actor const, hide flags, wait const,
        # INVOKE, actor const, show flags, STOP.
        return (plain(2) + [op_marker(1)] + plain(1) + [flags(True)] + plain(1) +
                [op_wait(pause)] + plain(1) + [flags(False)])
    player = Topdown(grid, mask, {actor_index: interact}, scheduler, long_press=long_press)
    # At a tick entry, drive port B0. BIOS samples it during the next VBlank;
    # Sys Lever is the preceding BIOS sample. The first effect is tick n + 3.
    frames = engine_frames(engine, ticks, thread=scheduler, player=player,
                           inputs=lambda tick: held_at(schedule, tick - 3))
    return frames, player


def build_case(root, toolchain, grid=8, pause=20, button="INPUT_A", speed=1, schedule=None, mutate=None,
               long_press=None):
    if schedule is None:
        schedule = SCHEDULE
    project_path = project_case(root, grid, pause, button, speed, long_press)
    manifest = export_project(project_path, root / "export")
    if mutate:
        rel, old, new = mutate
        path = root / "export" / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (rel, old, text.count(old))
        text = text.replace(old, new)
        write_new(path.with_suffix(".mutated"), text.encode())
        path.with_suffix(".mutated").replace(path)
        manifest["sha256"][rel] = hashlib.sha256(text.encode()).hexdigest()
        write_json(root / "export/export.json", manifest)
    build = build_rom(root / "export", toolchain)
    project = load_project(project_path)
    scene = project.scenes[0]
    engine = build_engine_scene(project, scene)
    total = max([b for _, b, _ in schedule] + [560]) + 40
    mask = {"INPUT_A": 16, "INPUT_B": 32, "INPUT_A_OR_B": 48}[button]
    frames, player = expected(engine, scene, total + 10, schedule, grid, pause, mask,
                              long_press or "select")
    width, height, pixels = background_pixels(project, scene)
    write_new(root / "scene.rgb444", b"".join(v.to_bytes(2, "little") for v in pixels))
    write_json(root / "source.json", {
        "size_px": [width, height], "probe_frames": total, "step_from_game_time": True,
        "view": list(view_of(engine.background))[::-1], "input_schedule": schedule,
        "expected_scroll": [list(p) for p, _ in frames], "actors": [d for _, d in frames],
        "project": str(project_path), "default_sprite_mode": project.settings["spriteMode"],
        "sprite_palettes": [list(p) for p in sprite_palettes(project, scene)],
        "oracle": "GBVM topdown.c / input.c via tools.gbvm_topdown",
        "player_trace": player.log, "interaction_ticks": player.interaction_ticks})
    return manifest, build


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--console", action="store_true")
    args = p.parse_args()
    if args.out.exists():
        p.error("output must be new")
    args.out.mkdir(parents=True)
    schedule = ([(120, 124, 8), (300, 330, 8), (480, 660, 16), (720, 750, 32),
                 (900, 915, 16), (1100, 1132, 1), (1280, 1312, 2)] if args.console else SCHEDULE)
    manifest, build = build_case(args.out, args.toolchain, pause=120 if args.console else 20, schedule=schedule)
    name = "ngpcraft_m2_topdown_controls.ngc" if args.console else "ngpcraft_m2_topdown_probe.ngc"
    write_new(args.out / name, (args.out / "export/rom/game.ngc").read_bytes())
    print(json.dumps({"rom": str(args.out / name), "bytes": build["header"]["size_bytes"]}))


if __name__ == "__main__":
    main()
