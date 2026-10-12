/* eslint-disable camelcase */ // Keys of ngpcraft.json (V3 format).
/*
 * NgpCraft: the build converts the project's songs for the NGPC, as GB Studio
 * plays a .uge with nothing to do (lib/ngpcraft/autoConvertMusic).
 *
 * Needs the V3 compiler and Python; skipped otherwise.
 */
import Path from "path";
import os from "os";
import { copySync, existsSync, mkdtempSync, writeJSONSync } from "fs-extra";
import {
  autoConvertMusic,
  autoConvertSounds,
} from "lib/ngpcraft/autoConvertMusic";
import { CONVERTED_SOURCE_FILE } from "lib/ngpcraft/convertUge";
import {
  getNgpcResourceSetting,
  getNgpcSoundEffects,
} from "shared/lib/ngpcraft/projectExtension";
import type { MusicResource, SoundResource } from "shared/lib/resources/types";
import { ngpcraftDefaultV3Root, projectTemplatesRoot } from "consts";

const v3Root = process.env.NGPCRAFT_V3_HOME || ngpcraftDefaultV3Root;
const available = existsSync(Path.join(v3Root, "ngpcraft"));

const song = (id: string, filename: string): MusicResource => ({
  _resourceType: "music",
  id,
  name: filename,
  symbol: `song_${id}`,
  filename,
  settings: {},
});

(available ? test : test.skip)(
  "unlinked and changed songs are converted, others left as set",
  async () => {
    const project = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-auto-"));
    copySync(
      Path.join(
        projectTemplatesRoot,
        "gbs2",
        "assets",
        "music",
        "Rulz_GonaSpace.uge",
      ),
      Path.join(project, "assets", "music", "Rulz_GonaSpace.uge"),
    );
    const music = [
      song("gona", "Rulz_GonaSpace.uge"),
      song("sc", "theme.ngps"),
    ];
    const sound = {
      format: "ngps",
      song: "sc/theme.ngps",
      bank: "sc/instruments.json",
      note_encoding: "sound-creator",
      tick_clock: "vblank",
    };
    const start = {
      schema: "ngpcraft-project/1",
      resources: { sc: { native_music: sound } },
    };
    const log: string[] = [];
    const first = await autoConvertMusic(project, music, start, (m) =>
      log.push(m),
    );
    expect(first.converted.map((c) => c.resourceId)).toEqual(["gona"]);
    expect(
      getNgpcResourceSetting(first.extension, "gona", "native_music"),
    ).toEqual({
      format: "held-score",
      score: "native-audio/Rulz_GonaSpace/held-score.json",
      bank: "native-audio/Rulz_GonaSpace/bank.json",
    });
    expect(
      getNgpcResourceSetting(first.extension, "sc", "native_music"),
    ).toEqual(sound);
    const folder = Path.join(project, "native-audio", "Rulz_GonaSpace");
    expect(existsSync(Path.join(folder, CONVERTED_SOURCE_FILE))).toBe(true);

    // Same file: nothing to do.
    const again = await autoConvertMusic(
      project,
      music,
      first.extension,
      () => {},
    );
    expect(again.converted).toEqual([]);

    // The song changed since its conversion: converted again.
    writeJSONSync(Path.join(folder, CONVERTED_SOURCE_FILE), { sha256: "old" });
    const changed = await autoConvertMusic(
      project,
      music,
      first.extension,
      () => {},
    );
    expect(changed.converted.map((c) => c.resourceId)).toEqual(["gona"]);
  },
  600000,
);

(available ? test : test.skip)(
  "an FX Hammer bank without native effects is converted, WAV left to the V3",
  async () => {
    const project = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-autosfx-"));
    copySync(
      Path.join(
        projectTemplatesRoot,
        "gbs2",
        "assets",
        "sounds",
        "Tronimal_Sound_Effects.sav",
      ),
      Path.join(project, "assets", "sounds", "Tronimal_Sound_Effects.sav"),
    );
    const sound = (
      id: string,
      filename: string,
      type: "fxhammer" | "wav",
    ): SoundResource => ({
      _resourceType: "sound",
      id,
      name: filename,
      symbol: `sound_${id}`,
      filename,
      type,
      numEffects: type === "fxhammer" ? 3 : undefined,
    });
    const sounds = [
      sound("fx", "Tronimal_Sound_Effects.sav", "fxhammer"),
      sound("wav", "voice.wav", "wav"),
    ];
    const first = await autoConvertSounds(project, sounds, null, () => {});
    expect(first.converted.map((c) => c.soundId)).toEqual(["fx"]);
    expect(getNgpcSoundEffects(first.extension, "fx")).toEqual([0, 1, 2]);
    expect(getNgpcSoundEffects(first.extension, "wav")).toEqual([]);
    const again = await autoConvertSounds(
      project,
      sounds,
      first.extension,
      () => {},
    );
    expect(again.converted).toEqual([]);
  },
  600000,
);
