/*
 * NgpCraft: the packaged editor is the Node that runs the V3 plugin bridge
 * (ngpcraft-v3/docs/M7_PLUGINS.md). The Electron fuse RunAsNode is off, so
 * the V3 starts `<editor> --ngpcraft-plugin-bridge` (NGPCRAFT_PLUGIN_HOST,
 * pluginBridgeEnv) and the main process runs only the bridge bundled with
 * the application, never another script: <resources>/ngpcraft-v3/v3/
 * ngpcraft/plugin_bridge.cjs with the GB Studio 4.3.2 modules gathered by
 * src/lib/forge/hooks/bundle-plugin-bridge.js. No window, no editor code:
 * the bridge reads stdin, writes stdout and exits with its parent.
 */
import Path from "path";
import { existsSync } from "fs";
import { app } from "electron";

export const PLUGIN_BRIDGE_FLAG = "--ngpcraft-plugin-bridge";

const resourcesPath = (): string | undefined =>
  (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath;

// Files laid out by src/lib/forge/hooks/bundle-v3.js.
export const bundledPluginBridge = ():
  | { bridge: string; bundle: string }
  | undefined => {
  const resources = resourcesPath();
  if (!resources) {
    return undefined;
  }
  const root = Path.join(resources, "ngpcraft-v3");
  const bridge = Path.join(root, "v3", "ngpcraft", "plugin_bridge.cjs");
  const bundle = Path.join(root, "plugin-bridge", "gbstudio_bridge.cjs");
  return existsSync(bridge) && existsSync(bundle)
    ? { bridge, bundle }
    : undefined;
};

export const pluginBridgeRequested = (argv: string[]): boolean =>
  argv.includes(PLUGIN_BRIDGE_FLAG);

// Node's own require (webpack would bundle the path instead).
// eslint-disable-next-line camelcase
declare const __non_webpack_require__: NodeRequire;

export const runPluginBridge = (): void => {
  // Once QuickJS is loaded, process.exit and app.exit both leave the Electron
  // main process alive (measured: still running 8 s after the bridge's quit);
  // Node's raw exit ends it at once. The bridge writes synchronously: nothing
  // is left to flush.
  const rawExit = (
    process as NodeJS.Process & {
      reallyExit?: (code: number) => never;
    }
  ).reallyExit;
  process.exit = ((code?: number) => {
    if (rawExit) {
      rawExit.call(process, code ?? 0);
    }
    app.exit(code ?? 0);
  }) as typeof process.exit;
  const files = bundledPluginBridge();
  if (!files) {
    process.stderr.write(
      "NgpCraft Studio: no bundled plugin bridge in this application\n",
    );
    process.exit(1);
    return;
  }
  // The bridge reads its arguments like a Node script.
  process.argv = [process.execPath, files.bridge, "--bundle", files.bundle];
  __non_webpack_require__(files.bridge);
};
