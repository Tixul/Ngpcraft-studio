"""MegaX : sauvegardes en boucle PENDANT le combat contre Elec Man (extinction après le 2e boss).

Copie du projet MegaX ; la partie démarre dans la salle du boss Elec Man ; en tête du script de
mise à jour du boss : emplacement 1, puis « sauver_partie » (le même événement personnalisé que
la victoire) toutes les 90 images, compteur en variable 201. Le combat continue autour (musique,
bruitages, projectiles, joueur). Chaque sauvegarde MegaX fait 18 pages : elles ne tiennent jamais
deux dans un bloc de 32, donc à partir de la 2e chaque sauvegarde efface un bloc.

  python -B -m tools.lab_megax_boss_save --source <megax_v24> --out <nouveau dossier>
         --toolchain C:/t900 [--emulator <Ngpcraft_emulator> --bios <bios> --frames 1800]

Sur l'émulateur : chien de garde et pile système en arrêt, flash à temps réels, RAM
aléatoire, cartouche 16 Mbit (ROM complétée à 2 Mo, 0xFF) ; une ligne par sauvegarde vue.
"""
import argparse
import json
from pathlib import Path
import random
import shutil

from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom
from tools.demo_scene_switch import ev, num
from tools.lab_save import setv

BOSS_SCENE = "1f32a5a9-4a99-5d54-9e18-b832c5bac22a"   # Elec Man - Boss
BOSS_ACTOR = "scenes/elec_boss/actors/20.gbsres"
SAVE_EVENT = "scripts/sauver_partie.gbsres"
COUNT = 201
SIZE_16MBIT = 0x200000
SAVE_BLOCKS = 0x200000 + 0x1F8000   # blocks 32-33 (FlashMem.txt)


def write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def victory_prefix(save_id):
    """--victory: two saves once (both blocks used: the victory's save erases one), then the
    boss at 1 HP every update loop: the first hit kills it, by MegaX's own script."""
    once = {"id": "lab-once", "command": "EVENT_IF", "args": {"condition": {
        "type": "eq", "valueA": {"type": "variable", "value": "202"}, "valueB": {"type": "number", "value": 0}}},
        "children": {"true": [setv("lab-done", 202, 1), setv("lab-slot", 50, 1),
                              ev("lab-save1", "EVENT_CALL_CUSTOM_EVENT", customEventId=save_id),
                              ev("lab-save2", "EVENT_CALL_CUSTOM_EVENT", customEventId=save_id),
                              ev("lab-inc", "EVENT_INC_VALUE", variable=str(COUNT)),
                              ev("lab-beep", "EVENT_SOUND_PLAY_EFFECT", type="beep", pitch=6,
                                 frequency=200, duration=0.3, wait=False)],
                     "false": []}}
    return [once, setv("lab-hp", 1, 1)]


POWER_REQUEST = "        if (*(volatile unsigned char *)0x6F85) {\n"
# Diagnostic only: a system power request (button, or the BIOS seeing the batteries drop)
# turns the whole screen red for 3 s before the shutdown; a crash switches off at once.
POWER_WITNESS = POWER_REQUEST + """\
            unsigned char k;
            *(volatile unsigned char *)0x8004 = 0u;
            *(volatile unsigned char *)0x8005 = 0u;
            for (k = 0u; k < 8u; ++k) ((volatile unsigned short *)0x83F0)[k] = 0x000Fu;
            for (k = 0u; k < 180u; ++k) {
                previous = ngc_frame;
                while (previous == ngc_frame) { }
            }
"""


def patch(export, name, pairs):
    """Replace each `old` (present exactly once) in the exported src/<name>; manifest hash too."""
    import hashlib
    path = export / "src" / name
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if text.count(old) != 1:
            raise SystemExit(f"{name}: {old[:50]!r} not found once")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8", newline="")
    manifest_path = export / "export.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"]["src/" + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8", newline="")


def power_witness(export):
    patch(export, "startup.c", [(POWER_REQUEST, POWER_WITNESS)])


# --trace-save: a black box. The console's RAM outlives a crash and the power-off that follows
# (it is what a console shows as "random RAM" at power-on), so the save driver writes its
# step to 0x6000 (above the static RAM, below the 3 KB of stack) and the next power-on shows
# the last step as a full-screen colour for 3 s, then boots as usual.
TRACE_RAM = 0x6000
# Signature "NGTR" on 4 bytes (a flash cart's menu runs before the ROM and may leave anything in
# RAM: one byte would match by chance 1 time in 256), then step, page, block.
TRACE = ("#define NGC_TRACE(s) (*(volatile unsigned char *)0x6000 = 0x4Eu, "
         "*(volatile unsigned char *)0x6001 = 0x47u, *(volatile unsigned char *)0x6002 = 0x54u, "
         "*(volatile unsigned char *)0x6003 = 0x52u, *(volatile unsigned char *)0x6004 = (unsigned char)(s))\n")
TRACE_SIGNED = ("*(volatile unsigned char *)0x6000 == 0x4Eu && *(volatile unsigned char *)0x6001 == 0x47u"
                " && *(volatile unsigned char *)0x6002 == 0x54u && *(volatile unsigned char *)0x6003 == 0x52u")
TRACE_COLOURS = {   # step: (0x0BGR, name for the guide)
    1: (0x0F00, "bleu : sauvegarde commencée (avant toute écriture flash)"),
    2: (0x000F, "rouge : juste avant l'effacement d'un bloc"),
    3: (0x00FF, "jaune : effacement terminé, avant sa vérification"),
    4: (0x00F0, "vert : bloc effacé et vérifié vierge"),
    5: (0x0F0F, "magenta : pendant l'écriture d'une page (appel du BIOS)"),
    6: (0x0FF0, "cyan : une page écrite, entre deux pages"),
    7: (0x0FFF, "blanc : sauvegarde terminée, avant de rendre la main au jeu"),
    8: (0x0888, "gris : le jeu a repris après la sauvegarde"),
    9: (0x008F, "orange : sauvegarde refusée par le journal"),
}
BOOT_AT = "    *(volatile unsigned char *)0x8118 = 0x80u;\n"


def trace_save(export):
    colours = ", ".join(f"0x{TRACE_COLOURS.get(k, (0, ''))[0]:04X}u" for k in range(10))
    patch(export, "save_journal.c", [
        ("extern void ngc_flash_erase(unsigned char block);\n",
         "extern void ngc_flash_erase(unsigned char block);\n" + TRACE),
        ("    ok = sj_write(slot, SJ_STATE);\n",
         "    NGC_TRACE(1u);\n    ok = sj_write(slot, SJ_STATE);\n    NGC_TRACE(ok ? 7u : 9u);\n"),
        ("        ngc_flash_erase(target);\n        if (!sj_blank(target, 0u, SJ_PAGES)) return 0u;\n",
         "        *(volatile unsigned char *)0x6006 = target;\n        NGC_TRACE(2u);\n"
         "        ngc_flash_erase(target);\n        NGC_TRACE(3u);\n"
         "        if (!sj_blank(target, 0u, SJ_PAGES)) return 0u;\n        NGC_TRACE(4u);\n")])
    patch(export, "save_flash.c", [
        ("static unsigned long ngc_flash_src;", TRACE + "static unsigned long ngc_flash_src;"),
        ("    ngc_flash_dst = ngc_flash_offset(block) + ((unsigned long)page << 8);\n",
         "    ngc_flash_dst = ngc_flash_offset(block) + ((unsigned long)page << 8);\n"
         "    *(volatile unsigned char *)0x6005 = page;\n    NGC_TRACE(5u);\n"),
        ("    __asm(\"ei 0\");\n}\n\n/* A whole", "    __asm(\"ei 0\");\n    NGC_TRACE(6u);\n}\n\n/* A whole")])
    patch(export, "game.c", [
        ("void ngc_game_tick(void)\n{\n    unsigned char status;\n    unsigned short next;\n",
         "void ngc_game_tick(void)\n{\n    unsigned char status;\n    unsigned short next;\n"
         "    if (*(volatile unsigned char *)0x6004 == 7u) *(volatile unsigned char *)0x6004 = 8u;\n")])
    patch(export, "startup.c", [(BOOT_AT, BOOT_AT + f"""\
    if ({TRACE_SIGNED}) {{
        static const unsigned short colours[10] = {{ {colours} }};
        unsigned char step = *(volatile unsigned char *)0x6004;
        unsigned short a, b;
        *(volatile unsigned char *)0x8004 = 0u;
        *(volatile unsigned char *)0x8005 = 0u;
        for (a = 0u; a < 8u; ++a) ((volatile unsigned short *)0x83F0)[a] = colours[step < 10u ? step : 0u];
        for (a = 0u; a < 90u; ++a)
            for (b = 0u; b < 4000u; ++b) *(volatile unsigned char *)0x006F = 0x4Eu;  /* ~3 s, measured */
        *(volatile unsigned char *)0x8004 = 160u;
        *(volatile unsigned char *)0x8005 = 152u;
        *(volatile unsigned char *)0x6000 = 0u;
        *(volatile unsigned char *)0x6003 = 0u;
    }}
""")])


SELECT_SCENE = "0a63ef92-595f-5bd0-a3f6-8c6eedc0c24d"   # where MegaX's victory goes


def steps_prefix(save_id):
    """--steps: the victory's ingredients one by one, beeps in between (heard on a console):
    2 saves (1 beep), a save that erases a block (2 beeps), then 3 beeps and the victory's
    own scene switch, without the fight."""
    def beeps(k, n):
        out = []
        for j in range(n):
            out += [ev(f"lab-b{k}{j}", "EVENT_SOUND_PLAY_EFFECT", type="beep", pitch=6, frequency=200,
                       duration=0.15, wait=False),
                    ev(f"lab-bw{k}{j}", "EVENT_WAIT", units="frames", frames=num(20))]
        return out
    save = lambda k: ev(f"lab-save{k}", "EVENT_CALL_CUSTOM_EVENT", customEventId=save_id)
    wait = lambda k, n: ev(f"lab-w{k}", "EVENT_WAIT", units="frames", frames=num(n))
    return [setv("lab-slot", 50, 1), save(1), save(2), *beeps(1, 1), wait(1, 60),
            save(3), *beeps(2, 2), wait(2, 60), *beeps(3, 3), wait(3, 30),
            ev("lab-go", "EVENT_SWITCH_SCENE", sceneId=SELECT_SCENE, x=num(9), y=num(13),
               direction="right", fadeSpeed="2"),
            {"id": "lab-stop", "command": "EVENT_LOOP", "args": {}, "children": {"true": [wait(4, 60)]}}]


BOSS_MUSIC = "0635da5d-4c5a-46c1-a946-1634056bdd95"   # Elec Man's room


def sound_ab_prefix(save_id):
    """--sound-ab: does an erase fail only under load? 2 saves (1 beep: both blocks used, each
    save from now on erases), then 2 erasing saves in SILENCE (2 beeps each), then the
    victory's conditions in a loop: music, the 1 s crash effect, 0.8 s, an erasing save (3 beeps)."""
    def beeps(k, n):
        out = []
        for j in range(n):
            out += [ev(f"lab-b{k}{j}", "EVENT_SOUND_PLAY_EFFECT", type="beep", pitch=6, frequency=200,
                       duration=0.15, wait=False),
                    ev(f"lab-bw{k}{j}", "EVENT_WAIT", units="frames", frames=num(20))]
        return out
    save = lambda k: ev(f"lab-save{k}", "EVENT_CALL_CUSTOM_EVENT", customEventId=save_id)
    wait = lambda k, n: ev(f"lab-w{k}", "EVENT_WAIT", units="frames", frames=num(n))
    loud = [ev("lab-crash", "EVENT_SOUND_PLAY_EFFECT", type="crash", pitch=4, frequency=200, duration=1.0,
               wait=False), wait("v", 48), save("v"), *beeps("v", 3), wait("v2", 90)]
    return [setv("lab-slot", 50, 1), save(1), save(2), *beeps(1, 1),
            ev("lab-stop", "EVENT_MUSIC_STOP"), wait(1, 90),
            save(3), *beeps(3, 2), wait(3, 60), save(4), *beeps(4, 2), wait(4, 60),
            ev("lab-music", "EVENT_MUSIC_PLAY", musicId=BOSS_MUSIC, loop=True), wait(5, 120),
            {"id": "lab-loud", "command": "EVENT_LOOP", "args": {}, "children": {"true": loud}}]


def project_case(source, out, victory=False, steps=False, sound_ab=False):
    project = out / "project"
    shutil.copytree(source, project, ignore=shutil.ignore_patterns("build", "build_export"))
    for path in (project / "project").glob("*.gbsres"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "startSceneId" in data:
            data.update(startSceneId=BOSS_SCENE, startX=4, startY=13)
            write(path, data)
    save_id = json.loads((project / "project" / SAVE_EVENT).read_text(encoding="utf-8"))["id"]
    boss_path = project / "project" / BOSS_ACTOR
    boss = json.loads(boss_path.read_text(encoding="utf-8"))
    loop = {"id": "lab-loop", "command": "EVENT_LOOP", "args": {}, "children": {"true": [
        ev("lab-save", "EVENT_CALL_CUSTOM_EVENT", customEventId=save_id),
        ev("lab-inc", "EVENT_INC_VALUE", variable=str(COUNT)),
        # Heard on the console: one beep = one save done.
        ev("lab-beep", "EVENT_SOUND_PLAY_EFFECT", type="beep", pitch=6, frequency=200, duration=0.3, wait=False),
        ev("lab-wait", "EVENT_WAIT", units="frames", frames=num(90))]}}
    prefix = (sound_ab_prefix(save_id) if sound_ab else steps_prefix(save_id) if steps
              else victory_prefix(save_id) if victory else [setv("lab-slot", 50, 1), loop])
    boss["updateScript"] = prefix + boss["updateScript"]
    write(boss_path, boss)
    return project


def run(export, emulator, bios, frames, seed=1, fire=False):
    import sys
    from tools.probe_rom import USER_RAM
    rom = (export / "rom/game.ngc").read_bytes()
    rom += b"\xFF" * (SIZE_16MBIT - len(rom))
    build = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))
    manifest = json.loads((export / "export.json").read_text(encoding="utf-8"))
    symbols = build["link_audit"]["public_symbols"]
    sys.path.append(str(Path(emulator).resolve()))
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.set_hw_guard(native.HW_WATCHDOG | native.HW_SYSTEM_STACK | native.HW_FLASH_BUSY_FETCH)
    m.reset(bios_handoff=True)
    rng = random.Random(seed)
    a, b = USER_RAM
    m.write(a, bytes(rng.randrange(256) for _ in range(b - a)))
    index = manifest["variables"].get(f"VAR_VARIABLE_{COUNT}")   # absent with --steps
    memory = symbols["_script_memory"]["address"]
    seen = -1
    dead = manifest["variables"].get("VAR_BOSSMORT", manifest["variables"].get("VAR_VARIABLE_2"))
    scene_at = symbols["_ngc_scene"]["address"]
    last = None
    for frame in range(frames):
        if fire:
            # X shoots (B) on and off from the left of the room; port 0xB0 and the BIOS copy.
            held = bytes([0x20 if frame % 16 < 4 else 0])
            m.write(0x00B0, held)
            m.write(0x6F82, held)
        summary = m.run_frames(1)
        state = (m.read(scene_at, 1)[0], int.from_bytes(m.read(memory + 2 * dead, 2), "little") if dead is not None else None)
        if state != last:
            print(f"image {frame}: scène {state[0]}, boss vaincu = {state[1]}")
            last = state
        if summary.stop_status not in (native.STATUS_OK, native.STATUS_COUNT_REACHED):
            print(f"image {frame}: ARRÊT {native.STATUS.get(summary.stop_status, summary.stop_status)}"
                  f" pc={summary.stop_pc:#x}")
            break
        if index is not None:
            n = int.from_bytes(m.read(memory + 2 * index, 2), "little")
            if n != seen:
                print(f"image {frame}: sauvegardes faites = {n}")
                seen = n
    print("violations :", m.hw_violations(), [(v.kind, hex(v.pc), v.detail) for v in m.hw_violation_samples(8)])
    blocks = m.read(SAVE_BLOCKS, 0x4000)
    print("pages non vierges : bloc 32 =", sum(any(x != 0xFF for x in blocks[p * 256:p * 256 + 256]) for p in range(32)),
          ", bloc 33 =", sum(any(x != 0xFF for x in blocks[0x2000 + p * 256:0x2000 + p * 256 + 256]) for p in range(32)))
    m.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", type=Path, required=True)
    p.add_argument("--emulator", type=Path)
    p.add_argument("--bios", type=Path)
    p.add_argument("--frames", type=int, default=1800)
    p.add_argument("--victory", action="store_true",
                   help="two saves, then Elec Man at 1 HP: X shoots, MegaX's victory and its save (an erase)")
    p.add_argument("--steps", action="store_true",
                   help="2 saves, 1 beep; an erasing save, 2 beeps; 3 beeps, the victory's scene switch")
    p.add_argument("--sound-ab", action="store_true",
                   help="erasing saves in silence (2 beeps), then after the crash effect with music (3 beeps)")
    p.add_argument("--trace-save", action="store_true",
                   help="black box: the save driver's last step shown as a colour at the next power-on")
    p.add_argument("--power-witness", action="store_true",
                   help="a system power request shows a red screen for 3 s before switching off")
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out}: output must be new")
    project = project_case(a.source, a.out, a.victory, a.steps, a.sound_ab)
    export_project(project, a.out / "export")
    if a.power_witness:
        power_witness(a.out / "export")
    if a.trace_save:
        trace_save(a.out / "export")
    build_rom(a.out / "export", a.toolchain)
    print("ROM:", a.out / "export/rom/game.ngc")
    if a.emulator:
        run(a.out / "export", a.emulator, a.bios, a.frames, fire=a.victory)


if __name__ == "__main__":
    main()
