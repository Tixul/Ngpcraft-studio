/*
 * NgpCraft: the Emscripten rgbds modules (appData/wasm/rgbds) start with
 * `var _scriptDir = import.meta.url;`, which webpack replaces with the
 * absolute file URL of the module on the build machine (user name included)
 * in the packaged app. The value is never used here: every module is created
 * with a `locateFile` (components/music/helpers/compiler.ts). Only that line
 * changes; `new URL("x.wasm", import.meta.url)` is left to webpack.
 */
const SCRIPT_DIR = /var _scriptDir = import\.meta\.url;/g;

module.exports = function scriptDirLoader(source) {
  return source.replace(SCRIPT_DIR, "var _scriptDir = undefined;");
};
