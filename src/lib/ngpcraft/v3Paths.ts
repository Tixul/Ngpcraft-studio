/*
 * NgpCraft: where the V3 compiler and its tools are (main process only).
 *
 * Each path is resolved at use, first found wins:
 *   1. Preferences (NgpCraft section), stored with electron-settings;
 *   2. environment variable;
 *   3. for the V3, and Python on Windows: the copy bundled in the packaged
 *      application;
 *   4. default (development checkout, usual install folder).
 *
 * Off Windows, the Toshiba tools (Windows programs) run through Wine: the V3
 * calls <toolchain>/BIN/<tool>.exe, so a folder of small scripts with the
 * same names forwards each call to Wine (toolchainHomeForV3).
 */
import Path from "path";
import { existsSync } from "fs";
import os from "os";
import { chmod, ensureDir, pathExists, writeFile } from "fs-extra";
import settings from "electron-settings";
import { ngpcraftDefaultV3Root } from "consts";
import { NGPCRAFT_SETTINGS } from "shared/lib/ngpcraft/settingsKeys";

export { NGPCRAFT_SETTINGS };

// Toshiba tools the V3 calls (V3 ngpcraft/toolchain.py TOOLS).
const TOSHIBA_TOOLS = [
  "cc900",
  "thc1",
  "thc2",
  "asm900",
  "tulink",
  "tuconv",
  "s242ngp",
];

export const isWindows = process.platform === "win32";

const fromSettings = (key: string): string | undefined => {
  try {
    const value = settings.get(key);
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
  } catch (e) {
    return undefined;
  }
};

// V3 bundled by the packaged application (forge extraResource, see
// src/lib/forge/hooks/bundle-v3.js): <resources>/ngpcraft-v3/v3.
const bundledV3Root = (): string | undefined => {
  const resources = (process as NodeJS.Process & { resourcesPath?: string })
    .resourcesPath;
  return resources ? Path.join(resources, "ngpcraft-v3", "v3") : undefined;
};

export const getV3Root = async (): Promise<string> => {
  const configured =
    fromSettings(NGPCRAFT_SETTINGS.v3Path) || process.env.NGPCRAFT_V3_HOME;
  if (configured) {
    return configured;
  }
  const bundled = bundledV3Root();
  if (bundled && (await pathExists(Path.join(bundled, "ngpcraft")))) {
    return bundled;
  }
  return ngpcraftDefaultV3Root;
};

// Embeddable Python bundled with the Windows package (bundle-v3.js):
// <resources>/ngpcraft-v3/python/python.exe.
const bundledPython = (): string | undefined => {
  const resources = (process as NodeJS.Process & { resourcesPath?: string })
    .resourcesPath;
  if (!isWindows || !resources) {
    return undefined;
  }
  const python = Path.join(resources, "ngpcraft-v3", "python", "python.exe");
  return existsSync(python) ? python : undefined;
};

export const getPython = (): string =>
  fromSettings(NGPCRAFT_SETTINGS.pythonPath) ||
  process.env.NGPCRAFT_PYTHON ||
  bundledPython() ||
  (isWindows ? "python" : "python3");

export const getToolchainPath = (): string =>
  fromSettings(NGPCRAFT_SETTINGS.toolchainPath) ||
  process.env.NGPC_TOSHIBA_HOME ||
  (isWindows ? "C:\\t900" : "");

export const getWine = (): string =>
  fromSettings(NGPCRAFT_SETTINGS.winePath) ||
  process.env.NGPCRAFT_WINE ||
  "wine";

// The folder the V3 receives as NGPC_TOSHIBA_HOME. Windows: the toolchain
// itself. Elsewhere: scripts named like the tools, each one running the real
// tool with Wine.
export const toolchainHomeForV3 = async (): Promise<string> => {
  const home = getToolchainPath();
  if (isWindows || !home) {
    return home;
  }
  const realBin = (await pathExists(Path.join(home, "BIN")))
    ? Path.join(home, "BIN")
    : home;
  const shimRoot = Path.join(os.tmpdir(), "ngpcraft-toolchain-wine");
  const shimBin = Path.join(shimRoot, "BIN");
  await ensureDir(shimBin);
  const wine = getWine();
  for (const tool of TOSHIBA_TOOLS) {
    const shim = Path.join(shimBin, `${tool}.exe`);
    const real = Path.join(realBin, `${tool}.exe`);
    await writeFile(
      shim,
      `#!/bin/sh\n# NgpCraft Studio: run the Toshiba tool with Wine.\nexec "${wine}" "${real}" "$@"\n`,
    );
    await chmod(shim, 0o755);
  }
  return shimRoot;
};

// NGPCraft Sound Creator, launched to edit .ngps songs. Candidates, first
// existing wins: NGPCRAFT_SOUND_CREATOR, then the sibling GitHub checkout
// (GitHub/Ngpcraft-studio/ngpcraft-v3 -> GitHub/): its packaged copy (Qt bundled),
// then its build (needs Qt on PATH).
const soundCreatorCheckout = Path.join(
  ngpcraftDefaultV3Root,
  "..",
  "..",
  "NGPCraft-Ngpc-sound-creator",
);
export const ngpcraftSoundCreatorCandidates = [
  process.env.NGPCRAFT_SOUND_CREATOR,
  Path.join(
    soundCreatorCheckout,
    "dist",
    "stage",
    "NGPC Sound Creator",
    "ngpc_sound_creator.exe",
  ),
  Path.join(
    soundCreatorCheckout,
    "build-mingw",
    "app",
    "ngpc_sound_creator.exe",
  ),
].filter((path): path is string => Boolean(path));
