/*
 * NgpCraft: serve the NgpCraft web player and the last built ROM to the Play
 * window through an internal protocol, ngpcplay://. The player needs module
 * scripts and WebAssembly (no file:// pages); a protocol avoids any network
 * server or firewall prompt.
 *
 *   ngpcplay://player/<file>  files of the web player (appData/wasm/ngpcraft-web)
 *   ngpcplay://rom/<name>.ngc the ROM chosen with setPlayRom
 */
import Path from "path";
import { readFile } from "fs-extra";
import { protocol } from "electron";
import { ngpcraftWebPlayerRoot } from "consts";
import { guardAssetWithinProject } from "lib/helpers/assets";

export const NGPC_PLAY_SCHEME = "ngpcplay";

export const ngpcPlaySchemePrivileges = {
  scheme: NGPC_PLAY_SCHEME,
  privileges: {
    standard: true,
    secure: true,
    supportFetchAPI: true,
    corsEnabled: true,
    stream: true,
  },
};

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".wasm": "application/wasm",
  ".json": "application/json",
  ".txt": "text/plain",
  ".ngc": "application/octet-stream",
};

let playRomPath = "";

export const setPlayRom = (romPath: string) => {
  playRomPath = romPath;
};

export const ngpcPlayUrl = (romName: string, title: string, debug: boolean) =>
  `${NGPC_PLAY_SCHEME}://player/studio.html?rom=${encodeURIComponent(
    `${NGPC_PLAY_SCHEME}://rom/${encodeURIComponent(romName)}`,
  )}&title=${encodeURIComponent(title)}&debug=${debug}&t=${Date.now()}`;

export const registerNgpcPlayProtocol = () => {
  protocol.registerBufferProtocol(NGPC_PLAY_SCHEME, async (req, callback) => {
    try {
      const { host, pathname } = new URL(req.url);
      let filename = "";
      if (host === "player") {
        filename = Path.join(ngpcraftWebPlayerRoot, decodeURI(pathname));
        guardAssetWithinProject(filename, ngpcraftWebPlayerRoot);
      } else if (host === "rom" && playRomPath) {
        filename = playRomPath;
      } else {
        return callback({ statusCode: 404, data: Buffer.from("") });
      }
      const data = await readFile(filename);
      const mimeType =
        MIME_TYPES[Path.extname(filename).toLowerCase()] ??
        "application/octet-stream";
      return callback({ mimeType, data });
    } catch (e) {
      return callback({ statusCode: 404, data: Buffer.from("") });
    }
  });
};
