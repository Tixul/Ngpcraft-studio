/* eslint-disable @typescript-eslint/no-var-requires */
/*
 * NgpCraft: before packaging, assemble the NgpCraft V3 compiler that the
 * application carries (forge extraResource ".bundle/ngpcraft-v3", outside
 * app.asar so Python can read it). Layout, as the V3 expects it:
 *
 *   ngpcraft-v3/v3/ngpcraft/                 V3 Python package
 *   ngpcraft-v3/v3/docs/ECARTS_GBSTUDIO.md   gap titles (event catalogue)
 *   ngpcraft-v3/plugin-bridge/gbstudio_bridge.cjs   GB Studio 4.3.2 modules
 *       of the event plugin bridge, run by the editor itself
 *       (bundle-plugin-bridge.js, lib/ngpcraft/pluginBridgeHost.ts)
 *   ngpcraft-v3/python/                      Windows only: the official
 *       embeddable Python (the V3 needs only the standard library), with
 *       ../v3 on its path, so a new Windows machine needs no Python install
 *
 * V3 source: NGPCRAFT_V3_SOURCE, or the ngpcraft-v3 folder of this repository.
 */
const fs = require("fs-extra");
const Path = require("path");
const crypto = require("crypto");
const AdmZip = require("adm-zip");
const { buildPluginBridge } = require("./bundle-plugin-bridge");

const editorRoot = Path.join(__dirname, "../../../..");
const bundleRoot = Path.join(editorRoot, ".bundle", "ngpcraft-v3");
const cacheRoot = Path.join(editorRoot, ".bundle", "cache");

// Official python.org build, pinned: any other file stops the packaging.
const PYTHON_EMBED = {
  url: "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
  sha256: "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b",
  pth: "python311._pth",
};

const sha256 = (data) => crypto.createHash("sha256").update(data).digest("hex");

const embeddedPython = async () => {
  const cached = Path.join(cacheRoot, Path.basename(PYTHON_EMBED.url));
  let data = (await fs.pathExists(cached)) ? await fs.readFile(cached) : null;
  if (!data || sha256(data) !== PYTHON_EMBED.sha256) {
    const response = await fetch(PYTHON_EMBED.url);
    if (!response.ok) {
      throw new Error(`Python download failed: ${response.status}`);
    }
    data = Buffer.from(await response.arrayBuffer());
    if (sha256(data) !== PYTHON_EMBED.sha256) {
      throw new Error(`Python download does not match the pinned SHA-256`);
    }
    await fs.outputFile(cached, data);
  }
  const target = Path.join(bundleRoot, "python");
  new AdmZip(data).extractAllTo(target, true);
  // The ._pth file replaces sys.path: add the V3 folder next to it.
  const pth = Path.join(target, PYTHON_EMBED.pth);
  const lines = (await fs.readFile(pth, "utf8")).split(/\r?\n/);
  await fs.writeFile(pth, ["../v3", ...lines].join("\r\n"));
};

module.exports = async (_forgeConfig, platform) => {
  const v3Source =
    process.env.NGPCRAFT_V3_SOURCE || Path.join(editorRoot, "ngpcraft-v3");
  const packageSource = Path.join(v3Source, "ngpcraft");
  if (!(await fs.pathExists(packageSource))) {
    throw new Error(
      `NgpCraft V3 not found in ${v3Source} (set NGPCRAFT_V3_SOURCE)`,
    );
  }
  await fs.remove(bundleRoot);
  await fs.copy(packageSource, Path.join(bundleRoot, "v3", "ngpcraft"), {
    filter: (src) => !src.includes("__pycache__") && !src.endsWith(".pyc"),
  });
  const gaps = Path.join(v3Source, "docs", "ECARTS_GBSTUDIO.md");
  if (await fs.pathExists(gaps)) {
    await fs.copy(gaps, Path.join(bundleRoot, "v3", "docs", "ECARTS_GBSTUDIO.md"));
  }
  await buildPluginBridge(
    Path.join(bundleRoot, "plugin-bridge", "gbstudio_bridge.cjs"),
  );
  if ((platform || process.platform) === "win32") {
    await embeddedPython();
  }
  console.log(`NgpCraft V3 bundled from ${v3Source}`);
};
