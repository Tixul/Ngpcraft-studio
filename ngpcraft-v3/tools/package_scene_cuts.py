"""Build four new, natively verified multi-scene ROMs without any fade."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.check_text import ppm  # noqa: E402
from tools.demo_gbs_scene import write_json  # noqa: E402
from tools.lab_scene_cuts import build_case, observe  # noqa: E402
from tools.package_m2_console import write_bytes  # noqa: E402

CASES = (("cut_60", 60, False), ("cut_30", 30, False),
         ("stack_60", 60, True), ("stack_30", 30, True))

GUIDE = """NGPCRAFT V3 — M2 : CHANGEMENTS DE SCÈNE SANS FONDU

Quatre ROMs : coupe directe (cut) et retour par pile de scènes (stack),
chacune à 60 et 30 images/s. Le village et la grotte alternent toutes les
deux secondes de jeu ; le chargement ajoute une courte pause. Aucun fondu,
son ni sauvegarde. Le script verrouille les déplacements pendant la boucle.

Sur chaque ROM, observer au moins deux minutes :
- Village en plein écran, grotte avec une bande noire de 4 pixels en haut
  et en bas. Le personnage doit rester visible et bien orienté.
- Dans cut, le retour au village place le joueur face vers le bas.
- Dans stack, le retour restaure sa position et sa direction initiales
  (vers la droite), sans dérive au fil des boucles.
- Les durées logiques sont les mêmes à 60 et 30 ; le dessin est moins
  fluide à 30. Une pause de chargement demeure à chaque transition.
- Le chargement peut brièvement afficher le nouveau décor avec les
  couleurs précédentes (limite A8 déjà acceptée). La scène stabilisée doit
  retrouver immédiatement ses bonnes couleurs, sans ligne déchirée.
- Éteindre par Power dans chaque scène, puis redémarrer.

captures.png : une ligne par ROM dans l'ordre cut_60, cut_30, stack_60,
stack_30 ; village, grotte, retour au village, autre passage en grotte.
Les images proviennent de l'émulateur natif ; elles ne prouvent pas les
images intermédiaires du chargement. Les mesures de cadence concernent
les ticks stables, hors chargements explicites.

Les variables de chaque scène sont vérifiées automatiquement mais ne sont
pas affichées. validation-summary.json contient pixels, horloges, charges,
RAM, pile et empreintes. RESULTATS_CONSOLE.json reste à compléter sur NGPC.
La validation physique des quatre ROMs est en attente.
"""


def contact_sheet(cases, destination):
    rows, selections = [], {}
    width = height = None
    for name, root in cases:
        report = json.loads((root / "comparison.json").read_text(encoding="utf-8"))
        # Sampling from the separate physical-frame probe, not debug counters
        # inserted into the ROM; choose stable frames in successive scenes.
        selections[name] = report["capture_frames"]
        pictures = [ppm(root / "variables" / f"shot-{frame:04d}.ppm")
                    for frame in report["capture_frames"]]
        if len(pictures) != 4 or not report["accepted"]:
            raise ValueError("four captures from an accepted native case required")
        for w, h, pixels in pictures:
            if width is not None and (width, height) != (w, h):
                raise ValueError("capture sizes differ")
            width, height = w, h
            if len(pixels) != w * h * 3:
                raise ValueError("incomplete native picture")
        rows.extend(b"\0" + b"".join(p[y * width * 3:(y + 1) * width * 3]
                                     for _, _, p in pictures) for y in range(height))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width * 4, height * len(cases), 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")
    write_bytes(destination, png)
    return selections


def package(out, toolchain, emulator, bios):
    out.mkdir(parents=True, exist_ok=False)
    console = out / "console"
    console.mkdir()
    summaries, cases = [], []
    for name, rate, stack in CASES:
        root = out / name
        root.mkdir()
        manifest, build = build_case(root, toolchain, wait=120, ticks=720, rate=rate, stack=stack)
        report = observe(root, emulator, bios)
        if not report["accepted"] or len(report["load_ticks_seen"]) < 4:
            raise RuntimeError(f"{name}: native gate failed; see {root / 'comparison.json'}")
        probe = json.loads((root / "variables/probe.json").read_text(encoding="utf-8"))
        rom = (root / "export/rom/game.ngc").read_bytes()
        if hashlib.sha256(rom).hexdigest() != build["header"]["sha256"]:
            raise RuntimeError("ROM changed after native verification")
        summary = dict(name=name, frame_rate=rate, stack=stack, rom=build["header"],
                       features=manifest["features"],
                       static_ram_bytes=build["link_audit"]["static_ram_bytes"],
                       stack_audit=build["link_audit"]["stack"],
                       native={k: report[k] for k in (
                           "accepted", "frames_with_mismatch", "variables_passed",
                           "stable_cadence_errors", "load_ticks_seen", "load_intervals")},
                       fingerprints={k: probe[k] for k in (
                           "rom_sha256", "bios_sha256", "emulator_core_sha256",
                           "emulator_binding_sha256", "silicon_timing", "boot")},
                       console_validated=False)
        write_json(root / "validation.json", summary)
        write_bytes(console / f"{name}.ngc", rom)
        summaries.append(summary)
        cases.append((name, root))
        print(json.dumps({"name": name, "native_accepted": True}), flush=True)
    captures = contact_sheet(cases, console / "captures.png")
    write_bytes(console / "GUIDE_CONSOLE.txt", GUIDE.encode("utf-8"))
    write_bytes(console / "SHA256SUMS.txt", "".join(
        f"{s['rom']['sha256']}  {s['name']}.ngc\n" for s in summaries).encode("ascii"))
    write_json(console / "validation-summary.json", dict(cases=summaries, captures=captures,
                                                          console_validated=False))
    write_json(console / "RESULTATS_CONSOLE.json", dict(cases=[dict(
        name=s["name"], rom_sha256=s["rom"]["sha256"], status="en_attente",
        console_model=None, linker=None, duration_minutes=None, boot=None,
        transitions=None, player_position_direction=None, perceived_cadence=None,
        power_off=None, anomalies=[], verdict=None) for s in summaries]))
    return console


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be new")
    console = package(args.out.resolve(), args.toolchain, args.emulator, args.bios)
    print(console)


if __name__ == "__main__":
    main()
