/*
 * NgpCraft: ask once where the Toshiba toolchain is, at the first start, when
 * none is set and none is found at the default place. The answer (or "Later")
 * is remembered; Preferences > NgpCraft changes it at any time.
 */
import Path from "path";
import { BrowserWindow, dialog } from "electron";
import { pathExists } from "fs-extra";
import settings from "electron-settings";
import l10n from "shared/lib/lang/l10n";
import { NGPCRAFT_SETTINGS } from "shared/lib/ngpcraft/settingsKeys";
import { getToolchainPath } from "lib/ngpcraft/v3Paths";

const hasCompiler = async (folder: string) =>
  (await pathExists(Path.join(folder, "BIN", "cc900.exe"))) ||
  (await pathExists(Path.join(folder, "cc900.exe")));

export const askToolchainOnFirstRun = async (
  parent: BrowserWindow | null,
): Promise<void> => {
  if (settings.get(NGPCRAFT_SETTINGS.toolchainAsked)) {
    return;
  }
  const current = getToolchainPath();
  if (current && (await hasCompiler(current))) {
    settings.set(NGPCRAFT_SETTINGS.toolchainAsked, true);
    return;
  }
  const options = {
    type: "question" as const,
    buttons: [
      l10n("DIALOG_NGPC_TOOLCHAIN_CHOOSE"),
      l10n("DIALOG_NGPC_TOOLCHAIN_LATER"),
    ],
    defaultId: 0,
    cancelId: 1,
    title: l10n("DIALOG_NGPC_TOOLCHAIN_TITLE"),
    message: l10n("DIALOG_NGPC_TOOLCHAIN_MESSAGE"),
    detail: l10n("DIALOG_NGPC_TOOLCHAIN_DETAIL"),
  };
  const { response } = parent
    ? await dialog.showMessageBox(parent, options)
    : await dialog.showMessageBox(options);
  settings.set(NGPCRAFT_SETTINGS.toolchainAsked, true);
  if (response !== 0) {
    return;
  }
  const pickOptions = { properties: ["openDirectory" as const] };
  const picked = parent
    ? await dialog.showOpenDialog(parent, pickOptions)
    : await dialog.showOpenDialog(pickOptions);
  if (!picked.canceled && picked.filePaths[0]) {
    settings.set(NGPCRAFT_SETTINGS.toolchainPath, picked.filePaths[0]);
  }
};
