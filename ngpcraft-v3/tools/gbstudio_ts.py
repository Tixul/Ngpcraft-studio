"""Run GB Studio's own TypeScript functions under Node, as a test oracle.

The V3 ports GB Studio's compiler code line for line (lexText, encodeString,
readFileToFontData...). Besides the expectations of GB Studio's tests, the
ports are compared with the REAL functions on large corpora: Node (>= 23,
type stripping and `--experimental-transform-types` for the enums) imports the
.ts files of the pinned reference (tools/reference.py) through a resolve hook
for GB Studio's path aliases (`shared/...`, `lib/...`, tsconfig baseUrl src).

  calls = [["shared/lib/compiler/lexText", "lexText", ["Hello $V0$"]], ...]
  results = run(calls)          # one JSON result per call, in order

A call whose function returns a Promise is awaited. Arguments and results
travel as JSON (Uint8Array results become lists).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from tools.reference import REFERENCE

HOOKS = r"""
import { existsSync, statSync } from "node:fs";
import { pathToFileURL, fileURLToPath } from "node:url";
import path from "node:path";
const SRC = process.env.NGC_GBS_SRC;
const ALIASES = /^(shared|lib|consts|store|components|renderer)(\/|$)/;
function pick(base) {
  for (const c of [base + ".ts", base + ".tsx", base + ".js", base, path.join(base, "index.ts"), path.join(base, "index.js")]) {
    if (existsSync(c) && statSync(c).isFile()) return c;
  }
  return null;
}
const STUBS = { "pngjs": "stub_pngjs.mjs", "fs-extra": "stub_fs_extra.mjs", "@sinclair/typebox": "stub_inert.mjs" };
export async function resolve(spec, ctx, next) {
  if (STUBS[spec]) return { url: new URL("./" + STUBS[spec], import.meta.url).href, shortCircuit: true };
  let base = null;
  if (ALIASES.test(spec)) base = path.join(SRC, spec);
  else if ((spec.startsWith("./") || spec.startsWith("../")) && ctx.parentURL && ctx.parentURL.startsWith("file:"))
    base = path.resolve(path.dirname(fileURLToPath(ctx.parentURL)), spec);
  if (base) {
    const f = pick(base);
    if (f) return { url: pathToFileURL(f).href, shortCircuit: true,
                    format: f.endsWith(".js") ? undefined : "module-typescript" };
  }
  return next(spec, ctx);
}
// GB Studio imports types and values in the same braces without `type`
// (its bundler elides them); under Node a missing named export is an error.
// Named imports become a namespace import plus a destructuring, so a
// type-only name is simply undefined (never used at run time).
let counter = 0;
function rewrite(source) {
  return source
    .replace(/import\s+type\s+[^;]*?from\s*["'][^"']+["'];?/g, "")
    .replace(/export\s+type\s*\{[^}]*\}\s*(from\s*["'][^"']+["'])?;?/g, "")
    .replace(/export\s*\{([^}]*)\}\s*from\s*(["'][^"']+["']);?/g, (m, names, from) => {
      const ns = "__ngc_re" + counter++;
      const kept = names.split(",").map((s) => s.trim()).filter((s) => s && !s.startsWith("type "));
      const lines = kept.map((s) => { const [a, b] = s.split(/\s+as\s+/); return `export const ${b || a} = ${ns}.${a};`; });
      return `import * as ${ns} from ${from};\n${lines.join("\n")}`;
    })
    .replace(/import\s+(\w+\s*,\s*)?\{([^}]*)\}\s*from\s*(["'][^"']+["']);?/g, (m, def, names, from) => {
      const ns = "__ngc_ns" + counter++;
      const kept = names.split(",").map((s) => s.trim()).filter((s) => s && !s.startsWith("type "))
        .map((s) => s.replace(/\s+as\s+/, ": "));
      return `import ${def || ""}* as ${ns} from ${from};\nconst { ${kept.join(", ")} } = ${ns};`;
    });
}
export async function load(url, ctx, next) {
  if (url.startsWith("file:") && /\.tsx?$/.test(url)) {
    const r = await next(url, { ...ctx, format: "module-typescript" });
    // CommonJS globals some modules use (consts.ts: __dirname).
    const file = fileURLToPath(url);
    const head = `const __filename = ${JSON.stringify(file)}; const __dirname = ${JSON.stringify(path.dirname(file))};\n`;
    return { format: "module-typescript", source: head + rewrite(r.source.toString()), shortCircuit: true };
  }
  return next(url, ctx);
}
"""

RUNNER = r"""
import { register } from "node:module";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";
register(new URL("./hooks.mjs", import.meta.url));
const calls = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const modules = {};
const out = [];
const plain = (v) => (v instanceof Uint8Array ? Array.from(v) : v);
for (const [mod, fn, args] of calls) {
  if (!modules[mod]) modules[mod] = await import(pathToFileURL(path.join(process.env.NGC_GBS_SRC, mod + ".ts")).href);
  const f = modules[mod][fn];
  if (typeof f !== "function") throw new Error(mod + " has no function " + fn);
  const r = await f(...args);
  out.push(JSON.parse(JSON.stringify(r, (k, v) => plain(v))));
}
process.stdout.write(JSON.stringify(out));
"""


# The two npm packages GB Studio's asset readers import, absent from the
# reference (no node_modules): fs-extra (readFile, readJson) and pngjs (PNG
# decoding). The PNG pixels come from ngpcraft/png.py (tests/test_png.py),
# passed in pngs.json; everything after the decoding is GB Studio's code.
STUB_FS_EXTRA = r"""
import { readFileSync } from "node:fs";
import path from "node:path";
export const readFile = async (p) => ({ __ngc_path: path.resolve(String(p)) });
export const readJson = async (p) => JSON.parse(readFileSync(String(p), "utf-8"));
export default { readFile, readJson };
"""
STUB_PNGJS = r"""
import { readFileSync } from "node:fs";
import path from "node:path";
const PNGS = JSON.parse(readFileSync(new URL("./pngs.json", import.meta.url), "utf-8"));
const key = (p) => path.resolve(p).toLowerCase();
export class PNG {
  parse(file, cb) {
    const img = PNGS[key(file.__ngc_path)];
    if (!img) { cb(new Error("ngc stub: PNG not provided " + file.__ngc_path)); return; }
    cb(null, { width: img.width, height: img.height, data: Uint8Array.from(img.data) });
  }
}
export default { PNG };
"""


# Schema builders evaluated at import time by modules on the import path
# (shared/lib/resources/types.ts: @sinclair/typebox); never part of the
# computation under test: an inert object that accepts any use.
STUB_INERT = r"""
const inert = new Proxy(function () {}, {
  get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === "then" ? undefined : inert),
  apply: () => inert, construct: () => inert });
export const Type = inert;
export default inert;
"""


class OracleError(RuntimeError):
    pass


def node_available():
    return shutil.which("node") is not None


def run(calls, root=REFERENCE, pngs=()):
    """pngs: PNG paths the called functions will read (decoded here)."""
    from ngpcraft.png import read_png
    node = shutil.which("node")
    if not node:
        raise OracleError("node is required for the GB Studio oracle")
    decoded = {}
    for p in pngs:
        width, height, pixels = read_png(Path(p))
        decoded[os.path.abspath(str(p)).lower()] = {"width": width, "height": height,
                                                    "data": [v for px in pixels for v in px]}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "pngs.json").write_text(json.dumps(decoded), encoding="utf-8")
        (tmp / "stub_fs_extra.mjs").write_text(STUB_FS_EXTRA, encoding="utf-8")
        (tmp / "stub_pngjs.mjs").write_text(STUB_PNGJS, encoding="utf-8")
        (tmp / "stub_inert.mjs").write_text(STUB_INERT, encoding="utf-8")
        (tmp / "hooks.mjs").write_text(HOOKS, encoding="utf-8")
        (tmp / "runner.mjs").write_text(RUNNER, encoding="utf-8")
        (tmp / "calls.json").write_text(json.dumps(calls), encoding="utf-8")
        env = {**os.environ, "NGC_GBS_SRC": str(Path(root) / "src")}
        proc = subprocess.run([node, "--experimental-transform-types", "--no-warnings", str(tmp / "runner.mjs"),
                               str(tmp / "calls.json")], capture_output=True, env=env, cwd=tmp)
        if proc.returncode != 0:
            raise OracleError(proc.stderr.decode("utf-8", "replace")[-2000:])
        return json.loads(proc.stdout.decode("utf-8"))
