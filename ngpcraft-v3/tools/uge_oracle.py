"""Execute the published GB Studio UGE loader/saver, without ported stubs.

Only inert schema builders are stubbed; loadUGESong, compactUGESong, song
factories, split-pattern helper and binary saveUGESong are the real sources.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from tools.gbstudio_ts import HOOKS, STUB_INERT, OracleError
from tools.reference import REFERENCE

RUNNER = r'''
import { register } from "node:module";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";
register(new URL("./hooks.mjs", import.meta.url));
const {loadUGESong, saveUGESong, exportToC} = await import(pathToFileURL(
    path.join(process.env.NGC_GBS_SRC, "shared/lib/uge/ugeHelper.ts")).href);
const {convertMODDataToUGESong} = await import(pathToFileURL(
    path.join(process.env.NGC_GBS_SRC, "shared/lib/uge/mod2uge/import.ts")).href);
const cases = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const plain = (k, v) => v instanceof Uint8Array ? Array.from(v) : v;
const out = [];
for (const item of cases) {
    const binary = item.modpath ? saveUGESong(convertMODDataToUGESong(readFileSync(item.modpath), true)) :
                   item.path ? readFileSync(item.path) : saveUGESong(item.song);
    const song = loadUGESong(binary);
    out.push({song, binary_base64: binary.toString("base64"),
              ...(item.export_c ? {export_c: exportToC(song, "oracle")} : {})});
}
process.stdout.write(JSON.stringify(out, plain));
'''


def run(cases):
    node = shutil.which("node")
    if not node:
        raise OracleError("Node is required for the GB Studio UGE oracle")
    with tempfile.TemporaryDirectory(prefix="ngc_uge_oracle_") as tmp:
        root = Path(tmp)
        hooks = HOOKS.replace('"@sinclair/typebox": "stub_inert.mjs"',
                              '"@sinclair/typebox": "stub_inert.mjs", "@sinclair/typebox/value": "stub_value.mjs"')
        assert hooks != HOOKS
        files = {"hooks.mjs": hooks, "stub_inert.mjs": STUB_INERT,
                 "stub_value.mjs": STUB_INERT.replace("export const Type = inert;", "export const Value = inert;"),
                 "runner.mjs": RUNNER, "cases.json": json.dumps(cases)}
        for name, text in files.items():
            with (root / name).open("x", encoding="utf-8") as stream:
                stream.write(text)
        env = {**os.environ, "NGC_GBS_SRC": str(REFERENCE / "src")}
        proc = subprocess.run([node, "--experimental-transform-types", "--no-warnings",
                               str(root / "runner.mjs"), str(root / "cases.json")],
                              capture_output=True, env=env, cwd=root, timeout=120)
        if proc.returncode:
            raise OracleError(proc.stderr.decode("utf-8", "replace")[-3000:])
        return json.loads(proc.stdout)
