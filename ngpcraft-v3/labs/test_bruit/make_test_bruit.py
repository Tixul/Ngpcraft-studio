"""Morceau de test console : la voie de bruit seule (PLAN_MUSIQUE_Z80 §12).

Partie 1 (1 bip) : chaque timbre E0..E7 TENU 6 lignes, instrument plat (Clean Tone, sans ADSR).
Partie 2 (2 bips) : chaque timbre E0..E7 frappé 4 fois, instrument Noise HiHat (ADSR).
Partie 3 (3 bips) : la batterie de Redline seule.
Vitesse par défaut : 8 images par ligne (64 lignes = 8,5 s par partie).
"""
import json
import sys
from pathlib import Path

COMPO = Path(r"C:\Users\wilfr\Desktop\NGPC_RAG\04_MY_PROJECTS\Doc de dev\recherche_bgm\data\compo")
OFF = {"a": 255, "i": 0, "n": 255}


def empty():
    return [None] * 64


def beeps(count):
    ch = empty()
    for k in range(count):
        ch[2 * k] = {"n": 84, "i": 0, "a": 0}
        ch[2 * k + 1] = dict(OFF)
    return ch


def part_held():
    ch = empty()
    for t in range(8):  # note n -> timbre (n-1) & 7
        ch[8 * t] = {"n": t + 1, "i": 0, "a": 0}
        ch[8 * t + 6] = dict(OFF)
    return ch


def part_hits():
    ch = empty()
    for t in range(8):
        for h in range(4):
            ch[8 * t + 2 * h] = {"n": t + 1, "i": 2, "a": 0}
    return ch


def main(out):
    red = json.loads((COMPO / "redline.ngps").read_text(encoding="utf-8"))
    drums = red["patterns"][red["order"][0]]["channels"][3]
    assert len(drums) == 64
    patterns = [{"length": 64, "channels": [beeps(n), empty(), empty(), noise]}
                for n, noise in ((1, part_held()), (2, part_hits()), (3, list(drums)))]
    song = {"version": 1, "loop_point": 0, "order": [0, 1, 2], "patterns": patterns}
    Path(out).write_text(json.dumps(song, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
