"""Audit of a wide-world export's C (docs/PLAN_GRANDES_CARTES.md, world.py).

The linker does not compare types: a 16-bit `extern` of a 32-bit position,
or a 32-bit position stored in a 16-bit variable, builds and truncates in
silence. Two checks on the generated sources:
1. every `extern` declaration has the type of the file-scope definition;
2. no variable declared 16-bit receives a position (act_x/act_y, the
   camera, a move target, a projectile position) as a whole value.

  python -B tools/wide_audit.py <export dir>
"""
import json
from pathlib import Path
import re
import sys

POSITIONS = r"(?:act_[xy]|act_m[xy]|proj_[xy]|ngc_camera_[xy]|ngc_camera_clamp_[xy]|cam_clamp_[xy]|camera_[xy])"
SHORT_TYPES = {"unsigned short", "short", "UWORD", "WORD", "INT16", "UINT16", "signed short"}
TYPE = r"(?:unsigned |signed )?(?:long|short|char|int)|UWORD|WORD|UBYTE|BYTE|INT16|UINT16|INT8|UINT8"
DEFINITION = re.compile(rf"^(?:static )?(?:volatile )?({TYPE}) (\w+)(?:\[[^\]]*\])*(?: = [^;]*)?;", re.M)
EXTERN = re.compile(rf"^extern ({TYPE}) ([\w, ]+);", re.M)
DECLARATION = re.compile(rf"^\s*(?:static )?({TYPE}) (\w+)(?:\[[^\]]*\])?(?: = [^;]*)?;", re.M)
STORE = re.compile(rf"\b(\w+)(?:\[[^\]]*\])? = ({POSITIONS})(?:\[[^\]]*\])?;")


def _norm(ctype):
    return {"UWORD": "unsigned short", "WORD": "short", "UINT16": "unsigned short", "INT16": "short",
            "UBYTE": "unsigned char", "BYTE": "signed char"}.get(ctype, ctype)


def audit(export):
    sources = {p.name: p.read_text(encoding="utf-8") for p in sorted((Path(export) / "src").glob("*.[ch]"))}
    defined = {}
    for name, text in sources.items():
        for ctype, var in DEFINITION.findall(text):
            defined.setdefault(var, set()).add((_norm(ctype), name))
    problems = []
    for name, text in sources.items():
        for ctype, names in EXTERN.findall(text):
            for var in (v.strip() for v in names.split(",")):
                types = {t for t, _ in defined.get(var, ())}
                if types and _norm(ctype) not in types:
                    problems.append(f"{name}: extern {ctype} {var}, defined as {sorted(types)}")
        # A position stored whole in a 16-bit variable of the same file (the
        # last declaration of that name before the store: locals shadow globals).
        for match in STORE.finditer(text):
            target = match.group(1)
            if re.fullmatch(POSITIONS, target):
                continue
            # a declaration with an initializer: its own type, on the same line
            line_start = text.rfind("\n", 0, match.start()) + 1
            own = re.fullmatch(rf"\s*(?:static )?({TYPE})\s+", text[line_start:match.start()])
            if own:
                if own.group(1) in SHORT_TYPES:
                    line = text.count("\n", 0, match.start()) + 1
                    problems.append(f"{name}:{line}: {own.group(1)} {target} = {match.group(2)}")
                continue
            before = [d for d in DECLARATION.finditer(text[:match.start()]) if d.group(2) == target]
            if before and before[-1].group(1) in SHORT_TYPES:
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{name}:{line}: {before[-1].group(1)} {target} = {match.group(2)}")
    return problems


if __name__ == "__main__":
    found = audit(sys.argv[1])
    print(json.dumps({"problems": found}, indent=2))
    sys.exit(1 if found else 0)
