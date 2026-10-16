"""Correctif chien de garde des pilotes flash ngpc_flash_asm.asm (base_template et dérivés).

Règle 3 de STORAGE.md (addendum 28/09/2026) ; validé console sur la V3 le 2026-10-17 (MegaX).
- plus aucune écriture de WDMOD (0x6E) : les lignes `(FLASH_BUS_CTRL),0x14 / 0xF0` et
  `(FLASH_WD),0xB1` sont retirées ;
- chien de garde vidé juste avant chaque `call FLASH_RAM` (et juste après) ;
- talon d'effacement StarGunner (98 o.) remplacé par la version 101 o. qui vide le chien de
  garde à chaque tour d'attente ; copie `ld bc,0x62` -> `0x65`.
Usage : python fix_flash_wd.py liste.txt [--apply]
"""
import os
import re
import sys
from pathlib import Path

OLD_STUB = bytes([
    0xF3, 0xF1, 0x55, 0x55, 0x00, 0xAA, 0xF3, 0xF1, 0xAA, 0x2A, 0x00, 0x55,
    0xF3, 0xF1, 0x55, 0x55, 0x00, 0xF0, 0x00, 0x00, 0xF3, 0xF1, 0x55, 0x55,
    0x00, 0xAA, 0xF3, 0xF1, 0xAA, 0x2A, 0x00, 0x55, 0xF3, 0xF1, 0x55, 0x55,
    0x00, 0x80, 0xF3, 0xF1, 0x55, 0x55, 0x00, 0xAA, 0xF3, 0xF1, 0xAA, 0x2A,
    0x00, 0x55, 0xB2, 0x00, 0x30, 0x82, 0x3F, 0xFF, 0x66, 0x15, 0xED, 0x61,
    0xED, 0xCF, 0xFF, 0xFF, 0x1F, 0x00, 0x66, 0x09, 0xB2, 0xCD, 0x66, 0xED,
    0x82, 0x3F, 0xFF, 0x66, 0x02, 0x21, 0xFF, 0xF3, 0xF1, 0x55, 0x55, 0x00,
    0xAA, 0xF3, 0xF1, 0xAA, 0x2A, 0x00, 0x55, 0xF3, 0xF1, 0x55, 0x55, 0x00,
    0xF0, 0x0E])
# old stub: loop at 0x35, `66 ED` (jr z,loop) at 0x46..0x47
NEW_STUB = OLD_STUB[:0x35] + bytes([0x08, 0x6F, 0x4E]) + OLD_STUB[0x35:0x46] + bytes([0x66, 0xEA]) + OLD_STUB[0x48:]
assert len(NEW_STUB) == 101


def v3_stub():
    """The 101 bytes of the V3 driver, validated on a console on 2026-10-17: the reference."""
    src = Path(r"C:\Users\wilfr\Documents\GitHub\Ngpcraft-studio\ngpcraft-v3\ngpcraft\runtime\save_flash.c").read_text(encoding="utf-8")
    table = src[src.index("ngc_erase_stub[101]"):]
    table = table[table.index("{") + 1:table.index("};")]
    return bytes(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})u", re.sub(r"/\*.*?\*/", "", table, flags=re.S)))


assert NEW_STUB == v3_stub(), "stub differs from the console-validated V3 one"

STUB_NOTE = """\
; WATCHDOG (2026-10-17). The stub used to run with the watchdog switched OFF
; (WDMOD 0x6E = 0x14, WDCR 0x6F = 0xB1) and turned back on with a FIXED
; WDMOD = 0xF0, without clearing it first. On a console that switched the
; machine off one erase in two (NgpCraft V3 / MegaX, Retro HQ GameDrive,
; validated fix 2026-10-17; STORAGE.md, addendum 28/09/2026, rule 3).
; Now WDMOD is never written: `ld (0x6F),0x4E` (08 6F 4E) is added at the top
; of the stub's wait loop, the jump back re-aimed (66 ED -> 66 EA), so the
; watchdog is cleared however long the erase takes. 101 bytes.
"""

DROP = re.compile(r"^[ \t]*ld[ \t]+\((?:FLASH_BUS_CTRL|0x6[eE])\),[ \t]*0x(?:14|F0|f0)\b.*\n"
                  r"|^[ \t]*ld[ \t]+\((?:FLASH_WD|0x6[fF])\),[ \t]*0x[bB]1\b.*\n", re.M)
KICK = "        ld      (FLASH_WD),0x4E         ; watchdog cleared (WDMOD never written)\n"


def db_bytes(block):
    return bytes(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", re.sub(r";[^\n]*", "", block)))


def db_lines(data):
    out = []
    for i in range(0, len(data), 12):
        out.append("        db      " + ",".join(f"0x{b:02X}" for b in data[i:i + 12]) + "\n")
    return "".join(out)


def patch(text):
    if "_erase_stub:" not in text or "FLASH_RAM" not in text:
        return None, "pas de talon d'effacement (system.lib ?)"
    if "0x08,0x6F,0x4E" in text.replace(" ", ""):
        return None, "déjà corrigé"
    m = re.search(r"(_erase_stub:[^\n]*\n)((?:[ \t]*(?:db[^\n]*|;[^\n]*)?\n)+)", text)
    if not m or db_bytes(m.group(2)) != OLD_STUB:
        return None, "talon d'effacement différent de celui attendu : non touché"
    new = text[:m.start()] + STUB_NOTE + m.group(1) + db_lines(NEW_STUB) + "\n" + text[m.end():]
    new, n_drop = DROP.subn("", new)
    # erase copy length
    e = new.index("_ngpc_flash_erase_asm:")
    seg_end = new.find("ret", e)
    seg = new[e:seg_end]
    seg2, n_len = re.subn(r"ld([ \t]+)bc,0x62([^\n]*)", lambda mm: f"ld{mm.group(1)}bc,0x65{' ' * max(1, len(mm.group(2)) - len(mm.group(2).lstrip()))}; BC = 101 bytes (watchdog-clearing stub)", seg, count=1)
    if n_len != 1:
        return None, "longueur de copie du talon introuvable"
    new = new[:e] + seg2 + new[seg_end:]
    # a watchdog clear right before and right after every call FLASH_RAM
    lines = new.split("\n")
    out = []
    for i, line in enumerate(lines):
        if re.match(r"^[ \t]*call[ \t]+FLASH_RAM\b", line):
            prev = out[-1] if out else ""
            if not re.search(r"\((?:FLASH_WD|0x6[fF])\),[ \t]*0x4E", prev):
                out.append(KICK.rstrip("\n"))
            out.append(line)
            nxt = next((l for l in lines[i + 1:] if l.strip() and not l.strip().startswith(";")), "")
            if not re.search(r"\((?:FLASH_WD|0x6[fF])\),[ \t]*0x4E", nxt):
                out.append(KICK.rstrip("\n"))
            continue
        out.append(line)
    new = "\n".join(out)
    new = re.sub(r"(FLASH_BUS_CTRL[ \t]+equ[ \t]+0x6E[ \t]*)(;[^\n]*)?",
                 r"\1; WDMOD (watchdog mode), NOT a /WE control: never written (see the stub note)", new, count=1)
    code = "\n".join(l.split(";", 1)[0] for l in new.split("\n"))
    if re.search(r"\((?:FLASH_BUS_CTRL|0x6[eE])\),", code) or re.search(r"0x[bB]1\b", code.replace("0xB1,", "")):
        return None, "écriture WDMOD restante"
    return new, f"{n_drop} lignes WDMOD/0xB1 retirées"


def main():
    files = [l.strip() for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
    apply = "--apply" in sys.argv
    for f in files:
        p = Path(f)
        raw = p.read_bytes()
        crlf = b"\r\n" in raw
        text = raw.decode("utf-8").replace("\r\n", "\n")
        new, why = patch(text)
        print(("OK   " if new else "--   ") + why + " : " + f)
        if new and apply:
            data = (new.replace("\n", "\r\n") if crlf else new).encode("utf-8")
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)


if __name__ == "__main__":
    main()
