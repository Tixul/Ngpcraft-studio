/*
 * NgpCraft: first file of the main process. `--ngpcraft-plugin-bridge` runs
 * the V3 plugin bridge bundled with the application and nothing of the
 * editor (lib/ngpcraft/pluginBridgeHost.ts); otherwise the editor starts.
 */
import {
  pluginBridgeRequested,
  runPluginBridge,
} from "lib/ngpcraft/pluginBridgeHost";

if (pluginBridgeRequested(process.argv)) {
  runPluginBridge();
} else {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("./main");
}
