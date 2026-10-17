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
 * starts them with the Wine it receives as NGPCRAFT_WINE (v3Env), on the
 * real toolchain, since cc900 itself starts thc1/thc2/asm900 inside Wine.
 */
import Path from "path";
import { existsSync } from "fs";
import { pathExists } from "fs-extra";
import settings from "electron-settings";
import { ngpcraftDefaultV3Root } from "consts";
import { NGPCRAFT_SETTINGS } from "shared/lib/ngpcraft/settingsKeys";
import { bundledPluginBridge } from "lib/ngpcraft/pluginBridgeHost";

export { NGPCRAFT_SETTINGS };

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

// Environment of every V3 the editor starts. Packaged editor: event plugins
// run through this application (NGPCRAFT_PLUGIN_HOST, pluginBridgeHost.ts),
// which carries the bridge and the GB Studio modules; from the sources, the
// V3 finds Node and the GB Studio sources itself (docs/M7_PLUGINS.md).
export const v3Env = (
  extra: Record<string, string> = {},
): NodeJS.ProcessEnv => ({
  ...process.env,
  ...(bundledPluginBridge() && !process.env.NGPCRAFT_PLUGIN_HOST
    ? { NGPCRAFT_PLUGIN_HOST: process.execPath }
    : {}),
  ...(isWindows ? {} : { NGPCRAFT_WINE: getWine() }),
  PYTHONIOENCODING: "utf-8",
  ...extra,
});
