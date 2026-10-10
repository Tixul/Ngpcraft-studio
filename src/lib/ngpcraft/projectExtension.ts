/*
 * NgpCraft: read and write ngpcraft.json next to the project (main process).
 * Writes are atomic (temporary file then rename) and skipped when nothing
 * changed. The file is only created when it carries an extension.
 */
import Path from "path";
import { pathExists, readFile, rename, writeFile } from "fs-extra";
import {
  hasNgpcContent,
  NgpcProjectExtension,
} from "shared/lib/ngpcraft/projectExtension";

export const NGPC_EXTENSION_FILENAME = "ngpcraft.json";

const extensionPath = (projectRoot: string) =>
  Path.join(projectRoot, NGPC_EXTENSION_FILENAME);

export const encodeNgpcExtension = (extension: NgpcProjectExtension) =>
  `${JSON.stringify(extension, null, 2)}\n`;

// null when the project has no ngpcraft.json. An unreadable file is an
// error: overwriting it on the next save would lose the user's data.
export const readNgpcExtension = async (
  projectRoot: string,
): Promise<NgpcProjectExtension | null> => {
  const path = extensionPath(projectRoot);
  if (!(await pathExists(path))) {
    return null;
  }
  const text = await readFile(path, "utf8");
  const parsed = JSON.parse(text.replace(/^﻿/, ""));
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${path}: not a JSON object`);
  }
  return parsed as NgpcProjectExtension;
};

export const writeNgpcExtension = async (
  projectRoot: string,
  extension: NgpcProjectExtension | null,
): Promise<void> => {
  if (!extension) {
    return;
  }
  const path = extensionPath(projectRoot);
  const exists = await pathExists(path);
  if (!exists && !hasNgpcContent(extension)) {
    return;
  }
  const data = encodeNgpcExtension(extension);
  if (exists && (await readFile(path, "utf8")) === data) {
    return;
  }
  const tmp = `${path}.tmp`;
  await writeFile(tmp, data, "utf8");
  await rename(tmp, path);
};
