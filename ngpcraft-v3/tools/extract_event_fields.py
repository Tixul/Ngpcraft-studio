"""Freeze the field schema of every GB Studio event, read from ITS sources.

Custom scripts (EVENT_CALL_CUSTOM_EVENT) rewrite the arguments of the events
they contain according to the TYPE of each field (scriptBuilder.ts
compileCustomEventScript: isVariableField, isActorField, isPropertyField,
isScriptValueField, isDataTableField of scriptDefHelpers.ts), and a field
only counts when visible (isFieldVisible: its `conditions`). Those types live
in the `fields` arrays of src/lib/events/*.js, which are computed JavaScript
(spreads, l10n calls): Node loads the real files, with `../helpers/l10n` and
`shared/lib/scriptValue/types` stubbed (labels and one compile-time helper,
neither part of the schema), and builds the lookup exactly like
handlerCommon.ts buildFieldsLookup (groups flattened, a later key wins).
Kept per field: type, conditions. Written to ngpcraft/gbstudio_event_fields.json.

  python -B -m tools.extract_event_fields write | check   (needs node)
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
from tools.reference import LABEL, REFERENCE  # noqa: E402
OUT = ROOT / "ngpcraft" / "gbstudio_event_fields.json"
EVENTS = "src/lib/events"

NODE_SCRIPT = r"""
const fs = require("fs"), path = require("path"), Module = require("module");
const dir = process.argv[2];
const stubs = {
  "../helpers/l10n": { default: (key) => key },
  "shared/lib/scriptValue/types": { isConstScriptValue: () => { throw new Error("compile-time only"); } },
};
const original = Module._load;
Module._load = function (request, parent, isMain) {
  if (request in stubs) return stubs[request];
  return original.apply(this, arguments);
};
// handlerCommon.ts buildFieldsLookup
const build = (lookup, fields) => {
  for (const field of fields) {
    if (field.type === "group" && field.fields) build(lookup, field.fields);
    else if (field.key) {
      const kept = { type: field.type === undefined ? null : field.type };
      if (field.conditions !== undefined) kept.conditions = field.conditions;
      lookup[field.key] = kept;
    }
  }
};
const out = {};
for (const name of fs.readdirSync(dir).sort()) {
  if (!name.endsWith(".js")) continue;
  // helpers.js, scriptCommands.js: not events (same filter as extract_gbstudio_model.py)
  if (!/^const id = "[A-Z0-9_]+";/m.test(fs.readFileSync(path.join(dir, name), "utf-8"))) continue;
  const mod = require(path.join(dir, name));
  if (!mod || typeof mod.id !== "string") continue;
  if (out[mod.id]) throw new Error("duplicate event id " + mod.id);
  const lookup = {};
  build(lookup, mod.fields || []);
  // handlerCommon.ts: isConditional = a top-level field of type "events"
  // (compileEntityEvents.ts emits GBVM_END$ debug symbols after those).
  const conditional = !!(mod.fields || []).find((f) => f.type === "events");
  out[mod.id] = { file: name, fields: lookup, conditional };
}
process.stdout.write(JSON.stringify(out));
"""


class ExtractError(RuntimeError):
    pass


def extract(root=REFERENCE):
    node = shutil.which("node")
    if not node:
        raise ExtractError("node is required to read the event definitions")
    events_dir = Path(root) / EVENTS
    digest = hashlib.sha256()
    for path in sorted(events_dir.glob("*.js")):
        digest.update(path.name.encode() + b"\x00" + path.read_bytes())
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "fields.js"
        script.write_text(NODE_SCRIPT, encoding="utf-8")
        proc = subprocess.run([node, str(script), str(events_dir)], capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
    if proc.returncode != 0:
        raise ExtractError(proc.stderr.strip()[-2000:])
    events = json.loads(proc.stdout)
    return {"reference": LABEL, "source": {"path": EVENTS + "/*.js", "sha256": digest.hexdigest()},
            "rule": "handlerCommon.ts buildFieldsLookup", "events": events}


def write_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("write", "check"))
    a = p.parse_args()
    fresh = extract()
    if a.command == "write":
        write_json(OUT, fresh)
        print(f"written {OUT}: {len(fresh['events'])} events")
        return 0
    same = json.loads(OUT.read_text(encoding="utf-8")) == fresh
    print("event fields up to date" if same else "event fields differ from the reference: rerun write and review")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
