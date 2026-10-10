/*
 * NgpCraft: link a GB Studio song to a Sound Creator .ngps song, and open
 * the Sound Creator (main process). The V3 reads the link from
 * ngpcraft.json resources.<music id>.native_music; both files must be inside
 * the project (V3 project_music.validate_binding).
 */
import Path from "path";
import { spawn } from "child_process";
import { pathExists } from "fs-extra";
import { BrowserWindow, dialog } from "electron";
import { ChooseNativeSongResult } from "shared/lib/ngpcraft/projectExtension";
import { ngpcraftSoundCreatorCandidates } from "lib/ngpcraft/v3Paths";

// Bank file of a Sound Creator project, next to its songs.
const SOUND_CREATOR_BANK = "instruments.json";

const toProjectPath = (projectRoot: string, file: string): string | null => {
  const relative = Path.relative(projectRoot, file);
  if (!relative || relative.startsWith("..") || Path.isAbsolute(relative)) {
    return null;
  }
  return relative.split(Path.sep).join("/");
};

const findBank = async (songFile: string): Promise<string | null> => {
  for (const dir of [
    Path.dirname(songFile),
    Path.dirname(Path.dirname(songFile)),
  ]) {
    const candidate = Path.join(dir, SOUND_CREATOR_BANK);
    if (await pathExists(candidate)) {
      return candidate;
    }
  }
  return null;
};

export const chooseNativeSong = async (
  projectRoot: string,
  parent: BrowserWindow | null,
): Promise<ChooseNativeSongResult> => {
  const options = {
    defaultPath: projectRoot,
    properties: ["openFile" as const],
    filters: [{ name: "Sound Creator song", extensions: ["ngps"] }],
  };
  const picked = parent
    ? await dialog.showOpenDialog(parent, options)
    : await dialog.showOpenDialog(options);
  if (picked.canceled || !picked.filePaths[0]) {
    return { cancelled: true };
  }
  const songFile = picked.filePaths[0];
  const song = toProjectPath(projectRoot, songFile);
  if (!song) {
    return {
      error: `${songFile}: the song must be inside the project folder (${projectRoot})`,
    };
  }

  let bankFile = await findBank(songFile);
  if (!bankFile) {
    const bankOptions = {
      defaultPath: Path.dirname(songFile),
      properties: ["openFile" as const],
      filters: [
        { name: "Sound Creator instrument bank", extensions: ["json"] },
      ],
    };
    const pickedBank = parent
      ? await dialog.showOpenDialog(parent, bankOptions)
      : await dialog.showOpenDialog(bankOptions);
    if (pickedBank.canceled || !pickedBank.filePaths[0]) {
      return { cancelled: true };
    }
    bankFile = pickedBank.filePaths[0];
  }
  const bank = toProjectPath(projectRoot, bankFile);
  if (!bank) {
    return {
      error: `${bankFile}: the instrument bank must be inside the project folder (${projectRoot})`,
    };
  }

  return {
    binding: {
      format: "ngps",
      song,
      bank,
      // Keys of the V3 file format (ngpcraft.json).
      // eslint-disable-next-line camelcase
      note_encoding: "sound-creator",
      // eslint-disable-next-line camelcase
      tick_clock: "vblank",
    },
  };
};

// "--project <song>" opens the Sound Creator project holding the song (its
// folder or the parent folder), skipping the project chooser.
export const openInSoundCreator = async (
  projectRoot: string,
  songPath: string,
): Promise<string | undefined> => {
  const songFile = Path.join(projectRoot, songPath);
  let exe: string | undefined;
  for (const candidate of ngpcraftSoundCreatorCandidates) {
    if (await pathExists(candidate)) {
      exe = candidate;
      break;
    }
  }
  if (!exe) {
    return `Sound Creator not found (set NGPCRAFT_SOUND_CREATOR): ${ngpcraftSoundCreatorCandidates.join(", ")}`;
  }
  const child = spawn(exe, ["--project", songFile], {
    cwd: Path.dirname(exe),
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  return undefined;
};
