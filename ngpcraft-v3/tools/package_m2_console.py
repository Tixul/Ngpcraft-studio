"""Build and verify the M2 combined-load console pack in a NEW directory.

Reuses the combined-load fixture and its source-pixel and native timing
oracles. The 60 Hz reference reports the four accepted dialogue page holds;
the two 30 Hz cases require strict cadence, including dialogue pages.
No BIOS, emulator or proprietary compiler is copied into the console pack.
"""
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
from tools.lab_combined_load import FRAMES, build_case, run  # noqa: E402

CASES = (("m2_60_7pnj", 60, 7, False),
         ("m2_30_7pnj", 30, 7, False),
         ("m2_30_14pnj", 30, 14, True))


def write_bytes(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


def native_gate(report, rate):
    """Do not let the accepted 60 Hz page holds hide another failed gate."""
    return (report["check_passed"] and report["dialogues"] >= 2
            and report["camera_positions"] > 40
            and report["front_positions"] > 20
            and not any(report[key] for key in (
                "missed_normal", "early_ticks", "commits_outside_vblank",
                "commits_overrunning_vblank"))
            and (len(report["missed_modal"]) == 4 if rate == 60
                 else not report["missed_modal"]))


def contact_sheet(cases, destination):
    """Four native captures per case; no third-party image dependency."""
    rows, selected = [], {}
    width = height = None
    for name, root in cases:
        probe = json.loads((root / "probe/probe.json").read_text(encoding="utf-8"))
        modal = [r["frame"] for r in probe["frame_timings"] if r["mode"] == "modal"]
        # The comparison oracle checked every screenshot, including these.
        first_dialogue = [modal[0]]
        for frame in modal[1:]:
            if frame != first_dialogue[-1] + 1:
                break
            first_dialogue.append(frame)
        normal = [r["frame"] for r in probe["frame_timings"] if r["mode"] == "normal"]
        frames = (40, first_dialogue[len(first_dialogue) // 3],
                  first_dialogue[2 * len(first_dialogue) // 3], normal[-1])
        selected[name] = list(frames)
        pictures = []
        for frame in frames:
            w, h, pixels = ppm(root / "probe" / f"shot-{frame:04d}.ppm")
            if width is not None and (w, h) != (width, height):
                raise ValueError("native screenshot dimensions differ")
            if len(pixels) != w * h * 3:
                raise ValueError("incomplete native screenshot")
            width, height = w, h
            pictures.append(pixels)
        rows.extend(b"\0" + b"".join(p[y * width * 3:(y + 1) * width * 3]
                                     for p in pictures) for y in range(height))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width * 4, height * len(cases), 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")
    write_bytes(destination, png)
    return selected


GUIDE = """NGPCRAFT V3 — M2 : ESSAIS CONSOLE 60 / 30 IMAGES PAR SECONDE

Les trois ROMs sont des projets de laboratoire : décor principal 512x512,
second décor 320x320, défilement avant à 1/2 sur X et 1/4 sur Y, joueur,
PNJ 16x16 mobiles et dialogue de deux pages. Aucun son ni sauvegarde.

Ordre recommandé :
1. m2_60_7pnj.ngc : référence, sept PNJ synchronisés, 60 images/s.
2. m2_30_7pnj.ngc : même charge, 30 images/s ; la vitesse des déplacements
   et des attentes reste identique en secondes. L'affichage est moins fluide.
3. m2_30_14pnj.ngc : quatorze PNJ, départs décalés, 30 images/s ; charge
   plus élevée (60 sprites matériels au maximum sur les 64 disponibles).

Sur chaque ROM, pendant au moins deux minutes :
- Déplacer le joueur avec le stick dans les quatre directions. Le motif
  du second décor doit défiler plus lentement que le principal.
- Une boîte s'ouvre automatiquement après environ 2,5 secondes de jeu.
  Le joueur s'immobilise pendant le dialogue, les PNJ continuent à marcher.
- A avance le texte puis passe à la page suivante et ferme la boîte.
  Relâcher A entre deux appuis. Tester aussi A pendant l'écriture du texte.
- Maintenir une direction à l'ouverture et à la fermeture de la boîte ;
  après fermeture, le déplacement et le décor doivent reprendre proprement.
- Vérifier que les sprites sont coupés sous la boîte, que le texte reste
  fixe à l'écran et que le second décor est restauré à la fermeture.
- À 60, une brève image retenue au changement de page est acceptée.
  À 30, ce parcours automatisé tient sa cadence, pages comprises ; signaler
  toute saccade supplémentaire visible sur console.
- Éteindre par Power pendant le jeu puis pendant un dialogue et redémarrer.

Les PNJ se déplacent autour du point de départ ; certains sortent de la vue
lorsque le joueur s'éloigne. Revenir au point de départ pour comparer la charge.
Captures : une ligne par ROM dans l'ordre ci-dessus, quatre vues par ligne.
La cadence chiffrée vient de l'émulateur natif, pas d'une observation visuelle.

RESULTATS_CONSOLE.json est une fiche à compléter : modèle de NGPC, linker,
durée, démarrage, défilement, dialogue/masquage, cadence perçue, arrêt Power,
anomalies et résultat. Les empreintes exactes des ROMs sont dans SHA256SUMS.txt
et validation-summary.json. Toutes les validations physiques restent en attente.
"""


def package(out, toolchain, emulator, bios):
    out.mkdir(parents=True, exist_ok=False)
    console = out / "console"
    console.mkdir()
    summaries, observed = [], []
    # Serialize compiler invocations; every export is new and remains intact.
    for name, rate, workers, stagger in CASES:
        root = out / name
        root.mkdir()
        manifest, build = build_case(root, toolchain, workers,
                                     stagger=stagger, frame_rate=rate)
        report = run(root, emulator, bios, tick_frames=60 // rate)
        probe = json.loads((root / "probe/probe.json").read_text(encoding="utf-8"))
        accepted = native_gate(report, rate)
        summary = dict(name=name, frame_rate=rate, workers=workers, stagger=stagger,
                       rom=build["header"], static_ram_bytes=build["link_audit"]["static_ram_bytes"],
                       stack=build["link_audit"]["stack"],
                       stack_words=manifest["gbvm"]["stack_budget"]["words_per_context"],
                       native=report, native_accepted=accepted, console_validated=False,
                       fingerprints={k: probe[k] for k in ("rom_sha256", "bios_sha256",
                           "emulator_core_sha256", "emulator_binding_sha256", "silicon_timing", "boot")})
        write_json(root / "validation.json", summary)
        print(json.dumps(dict(name=name, native_accepted=accepted,
                             missed_normal=len(report["missed_normal"]),
                             missed_modal=len(report["missed_modal"])), ensure_ascii=False), flush=True)
        if not accepted:
            raise RuntimeError(f"{name}: native verification failed; see {root / 'validation.json'}")
        rom = (root / "export/rom/game.ngc").read_bytes()
        if hashlib.sha256(rom).hexdigest() != build["header"]["sha256"]:
            raise RuntimeError("ROM fingerprint changed after native verification")
        write_bytes(console / f"{name}.ngc", rom)
        summaries.append(summary)
        observed.append((name, root))
    captures = contact_sheet(observed, console / "captures.png")
    write_bytes(console / "GUIDE_CONSOLE.txt", GUIDE.encode("utf-8"))
    write_bytes(console / "SHA256SUMS.txt", "".join(
        f"{s['rom']['sha256']}  {s['name']}.ngc\n" for s in summaries).encode("ascii"))
    write_json(console / "validation-summary.json", dict(
        cases=summaries, captures=captures, console_validated=False,
        scope="native source-pixel and timing proof; physical console validation pending"))
    write_json(console / "RESULTATS_CONSOLE.json", dict(cases=[dict(
        name=s["name"], rom_sha256=s["rom"]["sha256"], status="en_attente",
        console_model=None, linker=None, duration_minutes=None, boot=None,
        scrolling=None, dialogue_and_masking=None, perceived_cadence=None,
        power_off=None, anomalies=[], verdict=None) for s in summaries]))
    return console


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("out", "toolchain", "emulator", "bios"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output must be a new directory")
    console = package(args.out.resolve(), args.toolchain, args.emulator, args.bios)
    print(str(console), flush=True)


if __name__ == "__main__":
    main()
