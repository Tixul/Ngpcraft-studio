"""ROM console : batterie de Redline d'origine contre batterie corrigée (PLAN_MUSIQUE_Z80 §12 bis).

Corrections (dans une COPIE de la banque, instruments_corpus.json n'est pas touché) :
- charleston : nouvel instrument 38 = « Noise HiHat » avec adsr_decay 1 au lieu de 0
  (0 = silence dès la 2e image dans le pilote) ;
- grosse caisse : note 3 -> 2, timbre E2 (≈ 94 Hz) -> E1 (≈ 188 Hz).

Ordre (64 lignes = 8,5 s chacune) :
  1 bip  : batterie d'origine seule      | Redline complet, batterie d'origine
  2 bips : batterie corrigée seule       | Redline complet, batterie corrigée
"""
import copy
import json
import sys
from pathlib import Path

COMPO = Path(r"C:\Users\wilfr\Desktop\NGPC_RAG\04_MY_PROJECTS\Doc de dev\recherche_bgm\data\compo")
OFF = {"a": 255, "i": 0, "n": 255}
HIHAT, KICK_NOTE = 2, 3


def beeps(count):
    ch = [None] * 64
    for k in range(count):
        ch[2 * k] = {"n": 84, "i": 0, "a": 0}
        ch[2 * k + 1] = dict(OFF)
    return ch


def main(song_out, bank_out):
    bank = json.loads((COMPO / "instruments_corpus.json").read_text(encoding="utf-8"))
    hihat = copy.deepcopy(bank["instruments"][HIHAT])
    hihat["name"] = "Noise HiHat court"
    hihat["def"]["adsr_decay"] = 1
    new_hihat = len(bank["instruments"])
    bank["instruments"].append(hihat)

    red = json.loads((COMPO / "redline.ngps").read_text(encoding="utf-8"))
    base = red["patterns"][red["order"][0]]
    assert base["length"] == 64
    drums = base["channels"][3]
    fixed = []
    for cell in drums:
        if cell and cell.get("n") != 255:
            cell = dict(cell)
            if cell.get("i") == HIHAT:
                cell["i"] = new_hihat
            if cell.get("n") == KICK_NOTE:
                cell["n"] = KICK_NOTE - 1
        fixed.append(cell)
    def cut():  # the melody's held notes stop where the drums-only part starts
        return [dict(OFF)] + [None] * 63
    patterns = [
        {"length": 64, "channels": [beeps(1), cut(), cut(), list(drums)]},
        {"length": 64, "channels": [*copy.deepcopy(base["channels"][:3]), list(drums)]},
        {"length": 64, "channels": [beeps(2), cut(), cut(), fixed]},
        {"length": 64, "channels": [*copy.deepcopy(base["channels"][:3]), fixed]},
    ]
    song = {"version": 1, "loop_point": 0, "order": [0, 1, 2, 3], "patterns": patterns}
    Path(song_out).write_text(json.dumps(song, indent=1), encoding="utf-8")
    Path(bank_out).write_text(json.dumps(bank, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
