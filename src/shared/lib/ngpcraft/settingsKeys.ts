/*
 * NgpCraft: application settings (electron-settings keys) for the tools a
 * ROM build needs. Shared by Preferences (renderer) and lib/ngpcraft/v3Paths
 * (main process). An empty or missing value means "use the default".
 */
export const NGPCRAFT_SETTINGS = {
  v3Path: "ngpcraftV3Path",
  pythonPath: "ngpcraftPythonPath",
  toolchainPath: "ngpcraftToolchainPath",
  winePath: "ngpcraftWinePath",
  // Set once the first-run question about the toolchain was answered.
  toolchainAsked: "ngpcraftToolchainAsked",
} as const;
