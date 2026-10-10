"""M1: freeze the GB Studio 4.x project data model, extracted from ITS code.

The V3 project format (`ngpcraft-project/1`, docs/FORMAT_PROJET.md) is the GB
Studio project format itself. Its closed validation needs the exact resource
schemas and defaults of the reference. They are not copied by hand: this tool
parses the TypeBox declarations and the default settings of the reference and
writes them to ngpcraft/gbstudio_model.json, which the loader uses at run time
(the reference is not shipped). A test re-extracts and compares, so a new
reference version or a hand edit turns red.

Sources (reference: tools/reference.py):
  src/shared/lib/resources/types.ts     resource schemas (TypeBox)
  src/consts.ts                         defaultProjectSettings
  src/lib/project/loadProjectResources.ts   resource types the loader reads
  src/lib/events/*.js                   per-event flags read by the compiler
                                        (auto fade placement, contract C10)
  appData/engine/engine.json            scene types (removeDisabledScenes)

  python -B -m tools.extract_gbstudio_model write | check
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
from tools.reference import LABEL, REFERENCE
MODEL = ROOT / "ngpcraft" / "gbstudio_model.json"
SOURCES = {
    "types": "src/shared/lib/resources/types.ts",
    "consts": "src/consts.ts",
    "loader": "src/lib/project/loadProjectResources.ts",
    "engine": "appData/engine/engine.json",
}
EVENTS_GLOB = "src/lib/events/*.js"
# Event definition flags the compiler reads when it builds a scene's init
# thread (shared/lib/scripts/eventHelpers.ts:109-157, calculateAutoFadeEventId).
EVENT_FLAGS = ("waitUntilAfterInitFade", "allowChildrenBeforeInitFade")


class ParseError(ValueError):
    pass


TOKEN = re.compile(r'\s+|//[^\n]*|/\*.*?\*/|(?P<t>"(?:[^"\\]|\\.)*"|=>|\.\.\.|-?\d+(?:\.\d+)?|[A-Za-z_$][\w$]*|[()\[\]{},:;.=<>|&?!+*/%`-])', re.S)


def tokenize(text):
    out, pos = [], 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise ParseError(f"unexpected character {text[pos]!r} at offset {pos}")
        if m["t"] is not None:
            out.append(m["t"])
        pos = m.end()
    return out


class Parser:
    """Recursive descent over the TypeBox subset used by types.ts."""

    def __init__(self, tokens):
        self.toks, self.i, self.defs, self.recursive = tokens, 0, {}, None

    def peek(self, k=0):
        return self.toks[self.i + k] if self.i + k < len(self.toks) else None

    def take(self, expected=None):
        tok = self.peek()
        if expected is not None and tok != expected:
            raise ParseError(f"expected {expected!r}, got {tok!r} (token {self.i})")
        self.i += 1
        return tok

    def skip_statement(self):
        depth = 0
        while self.peek() is not None:
            tok = self.take()
            if tok in "([{":
                depth += 1
            elif tok in ")]}":
                depth -= 1
            elif tok == ";" and depth == 0:
                return

    def module(self):
        while self.peek() is not None:
            start = self.i
            if self.peek() == "export":
                self.take()
            if self.peek() == "const" and self.peek(2) == "=" and self.peek(3) in ("Type", "{"):
                self.take()
                name = self.take()
                self.take("=")
                self.defs[name] = self.expr() if self.peek() == "Type" else self.props_literal()
                self.take(";")
            else:
                self.i = start
                self.skip_statement()
        return self.defs

    def literal_value(self):
        tok = self.take()
        if tok.startswith('"'):
            return json.loads(tok)
        if tok in ("true", "false"):
            return tok == "true"
        if re.fullmatch(r"-?\d+(\.\d+)?", tok):
            return float(tok) if "." in tok else int(tok)
        raise ParseError(f"unsupported literal {tok!r}")

    def options(self):
        self.take("{")
        opts = {}
        while self.peek() != "}":
            key = self.take()
            self.take(":")
            opts[key] = self.literal_value()
            if self.peek() == ",":
                self.take()
        self.take("}")
        return opts

    def props_literal(self):
        """{ key: expr, ...Spread } -> ordered property map."""
        self.take("{")
        props = {}
        while self.peek() != "}":
            if self.peek() == "...":
                self.take()
                name = self.take()
                props.update(self.defs[name]["props"] if self.defs[name].get("t") == "object" else self.defs[name])
            else:
                key = self.take()
                self.take(":")
                schema = self.expr()
                optional = schema.pop("optional", False)
                props[key] = {"schema": schema, "optional": optional}
            if self.peek() == ",":
                self.take()
        self.take("}")
        return props

    def list_of_exprs(self):
        self.take("[")
        items = []
        while self.peek() != "]":
            items.append(self.expr())
            if self.peek() == ",":
                self.take()
        self.take("]")
        return items

    def trailing_options(self, node):
        if self.peek() == ",":
            self.take()
            if self.peek() == "{":
                node.update(self.options())
                if self.peek() == ",":
                    self.take()
        return node

    def expr(self):
        tok = self.take()
        if tok != "Type":
            if tok == self.recursive:
                return {"t": "ref", "name": self.recursive_name}
            if tok not in self.defs:
                raise ParseError(f"unknown reference {tok!r}")
            return json.loads(json.dumps(self.defs[tok]))
        self.take(".")
        fn = self.take()
        self.take("(")
        simple = {"String": "string", "Number": "number", "Integer": "integer", "Boolean": "boolean",
                  "Null": "null", "Undefined": "undefined", "Unknown": "unknown"}
        if fn in simple:
            node = {"t": simple[fn]}
            if self.peek() == "{":
                node.update(self.options())
        elif fn == "Literal":
            node = {"t": "literal", "v": self.literal_value()}
        elif fn in ("Union", "Tuple"):
            node = {"t": fn.lower(), "of": self.list_of_exprs()}
            self.trailing_options(node)
        elif fn == "Array":
            node = {"t": "array", "of": self.expr()}
            self.trailing_options(node)
        elif fn == "Record":
            key = self.expr()
            self.take(",")
            node = {"t": "record", "key": key, "value": self.expr()}
        elif fn == "Optional":
            node = self.expr()
            node["optional"] = True
        elif fn == "Object":
            node = {"t": "object", "props": self.props_literal()}
        elif fn == "Composite":
            parts = self.list_of_exprs()
            props = {}
            for part in parts:
                if part.get("t") != "object":
                    raise ParseError("Composite of a non-object")
                props.update(part["props"])
            node = {"t": "object", "props": props}
        elif fn == "Omit":
            base = self.expr()
            self.take(",")
            self.take("[")
            names = []
            while self.peek() != "]":
                names.append(self.literal_value())
                if self.peek() == ",":
                    self.take()
            self.take("]")
            node = {"t": "object", "props": {k: v for k, v in base["props"].items() if k not in names}}
        elif fn == "Recursive":
            self.take("(")
            self.recursive = self.take()
            self.take(")")
            self.take("=>")
            node = self.expr()
            node["name"] = self.recursive_name
            self.recursive = None
        else:
            raise ParseError(f"unsupported Type.{fn}")
        if self.peek() == ",":
            self.take()
        self.take(")")
        return node

def parse_types(text):
    parser = Parser(tokenize(text))
    # The only recursive schema is ScriptEvent: its self reference is named after it.
    parser.recursive_name = "ScriptEvent"
    return parser.module()


def parse_default_settings(text):
    m = re.search(r"export const defaultProjectSettings: Settings = (\{.*?\n\});", text, re.S)
    if not m:
        raise ParseError("defaultProjectSettings not found")
    body = re.sub(r"^(\s*)([A-Za-z_]\w*):", r'\1"\2":', m[1], flags=re.M)
    body = re.sub(r",(\s*[\]}])", r"\1", body)
    return json.loads(body)


def parse_loader(text):
    """(schema name, lookup key, mode) in the order loadProjectResources tries them."""
    return [{"schema": s, "lookup": k, "mode": mode}
            for mode, s, k in re.findall(r"^\s*(cast|partial)\((\w+), resourcesLookup\.(\w+)\),", text, re.M)]


def parse_events(root):
    """command -> {file, flags}: every published event and its compiler flags.

    A flag is either absent (undefined, falsy in JS) or one literal
    `key: true|false,` line of module.exports; any other form is refused so it
    cannot be misread."""
    events, digest = {}, hashlib.sha256()
    for path in sorted(root.glob(EVENTS_GLOB)):
        text = path.read_text(encoding="utf-8")
        digest.update(path.name.encode() + b"\x00" + path.read_bytes())
        m = re.search(r'^const id = "([A-Z0-9_]+)";', text, re.M)
        if not m:
            continue                      # helpers.js, scriptCommands.js
        entry = {"file": path.name}
        for flag in EVENT_FLAGS:
            found = re.findall(rf"^\s*{flag}:\s*(\S+?),?$", text, re.M)
            literal = all(v in ("true", "false") for v in found)
            if len(found) > 1 or not literal or text.count(flag) != len(found):
                raise ParseError(f"{path.name}: {flag} not a single literal boolean")
            entry[flag] = found == ["true"]
        if m[1] in events:
            raise ParseError(f"{path.name}: duplicate event id {m[1]}")
        events[m[1]] = entry
    return events, digest.hexdigest()


def extract(root=REFERENCE):
    root = Path(root)
    texts = {k: (root / p).read_text(encoding="utf-8") for k, p in SOURCES.items()}
    events, events_sha = parse_events(root)
    engine = json.loads(texts["engine"])
    defs = parse_types(texts["types"])
    loader = parse_loader(texts["loader"])
    resources = {}
    for entry in loader:
        schema = defs[entry["schema"]]
        kind = schema["props"]["_resourceType"]["schema"]["v"]
        resources[kind] = {**entry, "schema": schema}
    sources = {k: {"path": p, "sha256": hashlib.sha256((root / p).read_bytes()).hexdigest()}
               for k, p in SOURCES.items()}
    sources["events"] = {"path": EVENTS_GLOB, "sha256": events_sha}
    return {
        "reference": LABEL,
        "sources": sources,
        "resources": resources,
        "project": defs["ProjectMetadataResource"],
        # Target of {"t": "ref"} (ScriptEvent children are script events).
        "defs": {"ScriptEvent": defs["ScriptEvent"]},
        "defaultProjectSettings": parse_default_settings(texts["consts"]),
        "events": events,
        # compileData.ts:1312-1327 keeps only scenes of an enabled type.
        "sceneTypes": [t["key"] for t in engine["sceneTypes"]],
        # compileBootstrap.ts compileScriptEngineInit: a field without a value
        # in the project starts at its defaultValue.
        "engineFields": {f["key"]: {"cType": f["cType"], "defaultValue": f.get("defaultValue"),
                                    "sceneType": f.get("sceneType", "")}
                         for f in engine["fields"] if "key" in f},
    }


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("write", "check"))
    a = p.parse_args()
    fresh = extract()
    if a.command == "write":
        write_json(MODEL, fresh)
        print(f"written {MODEL}: {len(fresh['resources'])} resource types")
        return 0
    frozen = json.loads(MODEL.read_text(encoding="utf-8"))
    same = frozen == fresh
    print("GB Studio model up to date" if same else "GB Studio model differs from the reference: rerun write and review")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
