/*
 * NgpCraft: convert the project's songs for the NGPC at build time, as GB
 * Studio plays a .uge or .mod with nothing to do. A song without a native
 * version, or whose automatic conversion was made from an older version of
 * the file, goes through the same recipe as "Convert for the NGPC"
 * (convertUge). A song linked to a Sound Creator .ngps, or converted before
 * the fingerprint existed from a .mod, is left as the author set it.
 *
 * A song that cannot be converted does not stop the build here: the V3
 * refuses it, with its location, only if a script plays it.
 */
import Path from "path";
import { pathExists, readJSON } from "fs-extra";
import { assetPath } from "shared/lib/helpers/assets";
import type { MusicResource, SoundResource } from "shared/lib/resources/types";
import {
  AutoMusicBinding,
  AutoSfxBinding,
  getNgpcSoundEffects,
  setNgpcSoundEffects,
  getNgpcResourceSetting,
  NgpcNativeMusicHeldScore,
  NgpcProjectExtension,
  setNgpcResourceSetting,
} from "shared/lib/ngpcraft/projectExtension";
import {
  CONVERTED_SOURCE_FILE,
  convertUge,
  NATIVE_AUDIO_FOLDER,
  songFingerprint,
} from "lib/ngpcraft/convertUge";
import { convertSfx } from "lib/ngpcraft/convertSfx";

const isConvertible = (song: MusicResource) =>
  /\.(uge|mod)$/i.test(song.filename);

// True when the linked conversion was made from another version of the file.
const isStale = async (
  projectRoot: string,
  songFile: string,
  binding: NgpcNativeMusicHeldScore,
): Promise<boolean> => {
  const folder = Path.join(projectRoot, Path.dirname(binding.score));
  if (!binding.score.startsWith(`${NATIVE_AUDIO_FOLDER}/`)) {
    return false;
  }
  const current = await songFingerprint(songFile);
  const marker = Path.join(folder, CONVERTED_SOURCE_FILE);
  if (await pathExists(marker)) {
    return (await readJSON(marker)).sha256 !== current;
  }
  // Conversions made before the fingerprint: a .uge is the converter's own
  // source, its hash is in the loss report.
  const report = Path.join(folder, "loss-report.json");
  if (/\.uge$/i.test(songFile) && (await pathExists(report))) {
    return (await readJSON(report)).source_sha256 !== current;
  }
  return false;
};

export const autoConvertMusic = async (
  projectRoot: string,
  music: MusicResource[],
  extension: NgpcProjectExtension | null,
  log: (message: string) => void,
): Promise<{
  extension: NgpcProjectExtension | null;
  converted: AutoMusicBinding[];
}> => {
  let result = extension;
  const converted: AutoMusicBinding[] = [];
  for (const song of music) {
    if (!isConvertible(song)) {
      continue;
    }
    const songPath = assetPath("music", song);
    const songFile = Path.join(projectRoot, songPath);
    if (!(await pathExists(songFile))) {
      continue;
    }
    const linked = getNgpcResourceSetting(result, song.id, "native_music") as
      | { format?: string }
      | undefined;
    if (linked) {
      if (
        linked.format !== "held-score" ||
        !(await isStale(
          projectRoot,
          songFile,
          linked as NgpcNativeMusicHeldScore,
        ))
      ) {
        continue;
      }
      log(`NgpCraft: "${song.name}" changed, converting it again...`);
    } else {
      log(`NgpCraft: converting "${song.name}" for the NGPC...`);
    }
    const conversion = await convertUge(
      projectRoot,
      songPath,
      song.type === "mod" || /\.mod$/i.test(song.filename)
        ? !song.settings?.disableSpeedConversion
        : undefined,
    ).catch((e) => ({ error: String(e) }));
    if ("error" in conversion) {
      log(
        `warning: "${song.name}" could not be converted for the NGPC: ${conversion.error}`,
      );
      continue;
    }
    result = setNgpcResourceSetting(
      result,
      song.id,
      "native_music",
      conversion.binding,
    );
    converted.push({ resourceId: song.id, binding: conversion.binding });
  }
  return { extension: result, converted };
};

/*
 * Sound effect files (FX Hammer banks, VGM) without a native version are
 * converted the way the "Convert" button of the Sounds page does. WAV is
 * left to the V3, which refuses it with its reason (no sample player yet).
 */
export const autoConvertSounds = async (
  projectRoot: string,
  sounds: SoundResource[],
  extension: NgpcProjectExtension | null,
  log: (message: string) => void,
): Promise<{
  extension: NgpcProjectExtension | null;
  converted: AutoSfxBinding[];
}> => {
  let result = extension;
  const converted: AutoSfxBinding[] = [];
  for (const sound of sounds) {
    if (sound.type !== "fxhammer" && sound.type !== "vgm") {
      continue;
    }
    // GB Studio plays effect 0 of anything but an FX Hammer bank.
    const total =
      sound.type === "fxhammer" ? Math.max(1, sound.numEffects ?? 1) : 1;
    if (getNgpcSoundEffects(result, sound.id).length >= total) {
      continue;
    }
    log(`NgpCraft: converting sound "${sound.name}" for the NGPC...`);
    const conversion = await convertSfx(
      Path.join(projectRoot, assetPath("sounds", sound)),
      total,
    ).catch((e) => ({ error: String(e) }));
    if ("error" in conversion) {
      log(
        `warning: sound "${sound.name}" could not be converted for the NGPC: ${conversion.error}`,
      );
      continue;
    }
    result = setNgpcSoundEffects(result, sound.id, conversion.timelines);
    converted.push({ soundId: sound.id, timelines: conversion.timelines });
  }
  return { extension: result, converted };
};
